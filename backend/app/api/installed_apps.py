"""Installed app catalogue and capability-scoped, sandboxed HTTP gateway."""
import asyncio
import base64
from dataclasses import dataclass
from io import BytesIO
import json
import logging
import re
import secrets
import time
from urllib.parse import urlsplit

import httpx
from PIL import Image
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from ..config import get_settings
from ..db import get_db
from ..models import Role, User
from ..security.sessions import AuthContext, SESSION_COOKIE, RateLimiter, require_auth, require_admin
from .updates import agent_request, recent_admin
from .auth import audit

router = APIRouter(tags=['installed-apps'])
ID = r'^[a-z][a-z0-9-]{1,47}$'

class GatewayLogFilter(logging.Filter):
    def filter(self, record):
        # Uvicorn includes the raw URL in access-log args; capabilities must not be logged.
        if isinstance(record.args, tuple):
            record.args = tuple(re.sub(r'(/api/app-gateway/[^/]+/)[A-Za-z0-9_-]+', r'\1[redacted]', x)
                                if isinstance(x,str) else x for x in record.args)
        return True

logging.getLogger('uvicorn.access').addFilter(GatewayLogFilter())


def registry():
    path = get_settings().data_dir / 'installed-apps.json'
    return json.loads(path.read_text()) if path.exists() else {}


def permitted(item, ctx):
    return item.get('enabled') and ctx.user.role != Role.child and (
        ctx.user.role == Role.admin or ctx.user.id in item.get('users', []))


@router.get('/installed-apps')
async def listing(ctx: AuthContext = Depends(require_auth)):
    return [{'id':key,'name':item['manifest']['name'],'description':item['manifest']['description'],
             'version':item['manifest']['version'],'enabled':item['enabled'],'icon':item['icon'],
             'users':item.get('users',[]) if ctx.user.role == Role.admin else []}
            for key,item in registry().items() if ctx.user.role == Role.admin or permitted(item,ctx)]


class ImportIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    manifest: dict
    icon: str = Field(max_length=180000)
    users: list[str] = Field(default_factory=list, max_length=200)
    confirm_maintenance: bool = False


@router.post('/admin/app-catalog/install')
async def install(body: ImportIn, request: Request, ctx=Depends(require_admin), db=Depends(get_db)):
    recent_admin(ctx,body.confirm_maintenance)
    if len(json.dumps(body.manifest)) > 65536:
        raise HTTPException(400,'Manifest troppo grande')
    app_id = body.manifest.get('id')
    if not isinstance(app_id,str) or not re.fullmatch(ID,app_id):
        raise HTTPException(400,'ID manifest non valido')
    # Decode/re-encode a small PNG; SVG and decompression bombs are never forwarded.
    try:
        raw = base64.b64decode(body.icon, validate=True)
        if len(raw)>131072: raise ValueError()
        with Image.open(BytesIO(raw)) as image:
            if image.format!='PNG' or not (1<=image.width<=512 and 1<=image.height<=512): raise ValueError()
            image.load(); clean=BytesIO();image.convert('RGBA').save(clean,format='PNG')
        icon=base64.b64encode(clean.getvalue()).decode()
    except Exception:
        raise HTTPException(400,'Icona PNG valida richiesta, massimo 512×512 e 128 KiB')
    for user_id in body.users:
        user = await db.get(User,user_id)
        if not user or not user.is_active or user.role==Role.child or user.family_id!=ctx.user.family_id:
            raise HTTPException(400,'Selezionare utenti adulti attivi della propria famiglia')
    result = await agent_request('POST','/jobs',{'action':'app-install','target':app_id,
        'manifest':body.manifest,'icon':icon,'users':body.users,'confirm_maintenance':True})
    await audit(db,'app_install',request,ctx.user,app_id)
    return result


class ActionIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    users: list[str] = Field(default_factory=list, max_length=200)
    action: str = Field(pattern=r'^(start|stop|access)$')
    confirm_maintenance: bool = False


@router.post('/admin/app-catalog/{app_id}/action')
async def action(app_id:str, body:ActionIn, request:Request, ctx=Depends(require_admin),db=Depends(get_db)):
    recent_admin(ctx,body.confirm_maintenance)
    if not re.fullmatch(ID,app_id): raise HTTPException(400,'ID non valido')
    for uid in body.users:
        user=await db.get(User,uid)
        if not user or not user.is_active or user.role==Role.child or user.family_id!=ctx.user.family_id:
            raise HTTPException(400,'Utenti non validi')
    result=await agent_request('POST','/jobs',{'action':'app-'+body.action,'target':app_id,'users':body.users,'confirm_maintenance':True})
    await audit(db,'app_'+body.action,request,ctx.user,app_id)
    return result


@dataclass
class Lease:
    app_id:str
    sid:str
    expires:float

leases:dict[str,Lease]={}
gateway_slots=asyncio.Semaphore(8)
gateway_limits=RateLimiter(max_hits=300,window_s=60)


@router.post('/installed-apps/{app_id}/launch')
async def launch(app_id:str,ctx:AuthContext=Depends(require_auth)):
    item=registry().get(app_id)
    if not item or not permitted(item,ctx): raise HTTPException(403,'App non disponibile per questo account')
    now=time.monotonic()
    for token in list(leases):
        if leases[token].expires<now:leases.pop(token,None)
    if len(leases)>=1000:raise HTTPException(429,'Troppe sessioni applicative')
    token=secrets.token_urlsafe(32)
    leases[token]=Lease(app_id,ctx.session.id,now+3600)
    return {'name':item['manifest']['name'],'url':f'/api/app-gateway/{app_id}/{token}'+item['manifest']['launch']['path'], 'expires_in':3600}


@router.api_route('/app-gateway/{app_id}/{token}/{path:path}',methods=['GET','HEAD','POST','PUT','PATCH','DELETE','OPTIONS'],include_in_schema=False)
async def gateway(app_id:str,token:str,path:str,request:Request,db=Depends(get_db)):
    try:
        await asyncio.wait_for(gateway_slots.acquire(),timeout=2)
    except TimeoutError:
        raise HTTPException(429,'Gateway occupato, riprovare')
    try:
        return await proxy_request(app_id,token,path,request,db)
    finally:
        gateway_slots.release()


async def proxy_request(app_id,token,path,request,db):
    lease=leases.get(token)
    if not lease or lease.app_id!=app_id or lease.expires<time.monotonic():
        raise HTTPException(401,'Sessione app scaduta: riaprire la finestra')
    gateway_limits.check(lease.sid)
    # The scoped capability substitutes for CSRF at this endpoint ONLY. Recheck the
    # original login, MFA, user state and screen lock on every request, including assets.
    scope={**request.scope,'method':'GET','headers':[(b'cookie',(SESSION_COOKIE+'='+lease.sid).encode())]}
    ctx=await require_auth(Request(scope),db)
    from ..services.activity import remember_actor
    remember_actor(request,ctx.user.id,ctx.user.family_id)
    item=registry().get(app_id)
    if not item or not permitted(item,ctx):raise HTTPException(403,'Accesso app revocato')
    cors={'Access-Control-Allow-Origin':'null','Access-Control-Allow-Methods':'GET,HEAD,POST,PUT,PATCH,DELETE,OPTIONS',
          'Access-Control-Allow-Headers':'Content-Type','Cache-Control':'no-store','Referrer-Policy':'no-referrer',
          'X-Content-Type-Options':'nosniff'}
    origin=request.headers.get('origin')
    if origin and origin not in ('null',str(request.base_url).rstrip('/')):
        raise HTTPException(403,'Origine non ammessa')
    if request.method=='OPTIONS':return Response(status_code=204,headers=cors)
    # No redirects, alternate hosts, dot segments, cookies or platform auth headers.
    if any(p in ('.','..') for p in path.split('/')) or '\\' in path or path.startswith('/'):
        raise HTTPException(400,'Percorso app non valido')
    m=item['manifest'];service=m['launch']['service'];spec=m['services'][service]
    upstream=f'http://app-{app_id}-{service}:{spec["port"]}/'+path
    payload=bytearray()
    async for chunk in request.stream():
        payload.extend(chunk)
        if len(payload)>16*1024*1024:raise HTTPException(413,'Richiesta troppo grande')
    prefix=f'/api/app-gateway/{app_id}/{token}/'
    headers={'X-Forwarded-Prefix':prefix,'X-FamilOS-User':ctx.user.id}
    if request.headers.get('content-type'):headers['Content-Type']=request.headers['content-type']
    try:
        async with httpx.AsyncClient(timeout=30,trust_env=False,follow_redirects=False) as client:
            async with client.stream(request.method,upstream,params=request.query_params,content=bytes(payload),headers=headers) as response:
                content=bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content)>32*1024*1024:raise HTTPException(502,'Risposta app troppo grande')
                status=response.status_code
                content_type=response.headers.get('content-type','application/octet-stream')
    except httpx.HTTPError:raise HTTPException(502,'App non raggiungibile')
    if 300<=status<400:raise HTTPException(502,'L’app deve usare URL relativi senza redirect esterni')
    # CSP sandbox remains effective when opened as a top-level tab; never grant same-origin.
    base=str(request.base_url).rstrip('/')+prefix
    cors['Content-Security-Policy']=("sandbox allow-scripts allow-forms allow-downloads; default-src 'none'; "
        f"script-src 'unsafe-inline' 'wasm-unsafe-eval' {base}; style-src 'unsafe-inline' {base}; "
        f"img-src data: blob: {base}; font-src data: {base}; connect-src {base}; form-action {base}; "
        "frame-src 'none'; object-src 'none'; base-uri 'none'; frame-ancestors 'self'")
    cors['Content-Type']=content_type
    return Response(bytes(content),status_code=status,headers=cors)
