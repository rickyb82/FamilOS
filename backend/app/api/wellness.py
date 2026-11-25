"""Personal Home Pulse API. Browser identity and bridge identity never mix."""
from __future__ import annotations

import asyncio
import base64
import hashlib
from functools import lru_cache
import io
import json
import os
import time
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter,Depends,HTTPException,Query,Request,Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel,Field
import qrcode
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import get_db,SessionLocal
from ..models import User,Role
from ..security.sessions import AuthContext,require_adult as base_require_adult,require_auth,RateLimiter,client_ip
from ..services.settings_store import encrypt,decrypt
from ..services.homepulse.store import HomePulseStore,StoreError,ConflictError,ALLOWED_ACTIVITIES

router=APIRouter(prefix='/wellness/v1',tags=['wellness'])
pair_limiter=RateLimiter(12,300)

async def require_adult(ctx=Depends(base_require_adult)):
    from ..services.homepulse.module import enabled
    from ..services.apps import app_enabled
    if not enabled():raise HTTPException(404,'Modulo Allenamento non installato')
    if not app_enabled(ctx.user,'wellness'):raise HTTPException(403,'Allenamento non abilitato per questo profilo')
    return ctx



@lru_cache(maxsize=1)
def store():
    return HomePulseStore(get_settings().data_dir/'wellness',encrypt,decrypt)


def _error(exc):
    return HTTPException(409 if isinstance(exc,ConflictError) else 400,str(exc))


class Enrollment(BaseModel):
    pairing_code:str=Field(min_length=1,max_length=128)
    device_name:str=Field(default='iPhone',min_length=1,max_length=120)


class Batch(BaseModel):
    batch_id:str=Field(min_length=1,max_length=200)
    events:list[dict]=Field(min_length=1,max_length=1000)


async def bridge(request:Request,db:AsyncSession=Depends(get_db)):
    from ..services.homepulse.module import enabled
    from ..services.apps import app_enabled
    header=request.headers.get('authorization','')
    if not header.startswith('Bearer ') or len(header)>300:raise HTTPException(401,'Collega l’iPhone dal tuo profilo FamilOS')
    if not enabled():raise HTTPException(403,'Modulo Allenamento disattivato: dati da conservare nella coda')
    device=await asyncio.to_thread(store().authenticate,header[7:])
    if not device:raise HTTPException(401,'Collegamento iPhone scaduto o revocato')
    user=await db.get(User,device['owner_id'])
    if not user or not user.is_active or user.role==Role.child:raise HTTPException(403,'Profilo non disponibile')
    if not app_enabled(user,'wellness'):raise HTTPException(403,'Allenamento non abilitato')
    return {**device,'owner_name':user.display_name}


@router.get('/state')
async def state(ctx:AuthContext=Depends(require_adult)):
    return await asyncio.to_thread(store().state,ctx.user.id)


@router.get('/analysis')
async def analysis(session_id:str|None=None,start:float|None=None,end:float|None=None,ctx:AuthContext=Depends(require_adult)):
    try:return await asyncio.to_thread(store().analysis,ctx.user.id,session_id,start,end)
    except StoreError as exc:raise _error(exc) from None


@router.get('/energy')
async def energy(date:str|None=None,ctx:AuthContext=Depends(require_adult)):
    try:return await asyncio.to_thread(store().daily_energy,ctx.user.id,date)
    except StoreError as exc:raise _error(exc) from None


@router.get('/sleep')
async def sleep(days:int=Query(default=14,ge=1,le=90),ctx:AuthContext=Depends(require_adult)):
    return await asyncio.to_thread(store().sleep,ctx.user.id,days)


@router.put('/settings')
async def settings(body:dict,ctx:AuthContext=Depends(require_adult)):
    try:await asyncio.to_thread(store().update_settings,ctx.user.id,body)
    except (StoreError,ValueError) as exc:raise _error(exc) from None
    return await asyncio.to_thread(store().state,ctx.user.id)


@router.post('/ring-binding')
async def ring_binding(body:dict,ctx:AuthContext=Depends(require_adult)):
    try:await asyncio.to_thread(store().set_ring,ctx.user.id,body.get('logical_ring_id'),body.get('owner_key_hex'))
    except StoreError as exc:raise _error(exc) from None
    return {'configured':True}


@router.post('/bridges/pair')
async def pair(request:Request,ctx:AuthContext=Depends(require_adult)):
    value=await asyncio.to_thread(store().create_pairing,ctx.user.id)
    server_url=str(request.base_url).rstrip('/')
    uri='familos-homepulse://pair?'+urlencode({'server':server_url,'code':value['pairing_code']})
    image=qrcode.make(uri);buf=io.BytesIO();image.save(buf,format='PNG')
    return {**value,'server_url':server_url,'enrollment_uri':uri,
            'qr_png_data_url':'data:image/png;base64,'+base64.b64encode(buf.getvalue()).decode()}


@router.post('/bridges/enroll')
async def enroll(body:Enrollment,request:Request,db:AsyncSession=Depends(get_db)):
    from ..services.homepulse.module import enabled
    from ..services.apps import app_enabled
    if not enabled():raise HTTPException(403,'Modulo Allenamento non disponibile')
    pair_limiter.check(client_ip(request))
    try:result=await asyncio.to_thread(store().enroll,body.pairing_code,body.device_name)
    except StoreError as exc:raise _error(exc) from None
    user=await db.get(User,result['owner_id'])
    if not user or not user.is_active or user.role==Role.child or not app_enabled(user,'wellness'):
        await asyncio.to_thread(store().revoke,result['owner_id'],result['device_id'])
        raise HTTPException(403,'Profilo non disponibile')
    return {'device_id':result['device_id'],'access_token':result['access_token'],
            'owner_name':user.display_name,'server_time':time.time(),'protocol_version':1,
            'allowed_activities':ALLOWED_ACTIVITIES,
            'settings':await asyncio.to_thread(store().settings,result['owner_id']),
            'ring_binding':await asyncio.to_thread(store().ring,result['owner_id'],True)}


@router.get('/bridges')
async def bridges(ctx:AuthContext=Depends(require_adult)):
    value=await asyncio.to_thread(store().state,ctx.user.id)
    return value['devices']


@router.delete('/bridges/{device_id}')
async def revoke(device_id:str,ctx:AuthContext=Depends(require_adult)):
    try:await asyncio.to_thread(store().revoke,ctx.user.id,device_id)
    except StoreError as exc:raise _error(exc) from None
    return {'revoked':True}


@router.post('/bridges/events')
async def events(body:Batch,device:dict=Depends(bridge)):
    try:return await asyncio.to_thread(store().append,device['owner_id'],device['id'],body.batch_id,body.events)
    except (StoreError,ValueError) as exc:raise _error(exc) from None


@router.get('/mobile/bootstrap')
async def bootstrap(device:dict=Depends(bridge)):
    value=await asyncio.to_thread(store().state,device['owner_id'])
    value['ring_binding']=await asyncio.to_thread(store().ring,device['owner_id'],True)
    return value


@router.patch('/mobile/settings')
async def mobile_settings(body:dict,device:dict=Depends(bridge)):
    try:await asyncio.to_thread(store().update_settings,device['owner_id'],body)
    except (StoreError,ValueError) as exc:raise _error(exc) from None
    return await bootstrap(device)


@router.get('/mobile/analysis')
async def mobile_analysis(session_id:str|None=None,start:float|None=None,end:float|None=None,device:dict=Depends(bridge)):
    try:return await asyncio.to_thread(store().analysis,device['owner_id'],session_id,start,end)
    except StoreError as exc:raise _error(exc) from None


@router.get('/mobile/sleep')
async def mobile_sleep(days:int=Query(default=14,ge=1,le=90),device:dict=Depends(bridge)):
    return await asyncio.to_thread(store().sleep,device['owner_id'],days)


@router.post('/sessions')
async def session(body:dict,ctx:AuthContext=Depends(require_adult)):
    try:return await asyncio.to_thread(store().manual_session,ctx.user.id,body.get('action'),body.get('session_id'),body.get('activity_name'))
    except (StoreError,ValueError) as exc:raise _error(exc) from None


@router.get('/events')
async def stream(request:Request,ctx:AuthContext=Depends(require_adult)):
    async def generate():
        checked=time.monotonic()
        while not await request.is_disconnected():
            from ..services.homepulse.module import enabled
            if not enabled():return
            if time.monotonic()-checked>=10:
                async with SessionLocal() as db:
                    try:await require_adult(await require_auth(request,db))
                    except HTTPException:return
                checked=time.monotonic()
            value=await asyncio.to_thread(store().state,ctx.user.id)
            yield 'data: '+json.dumps(value,ensure_ascii=False,allow_nan=False)+'\n\n'
            await asyncio.sleep(1)
    return StreamingResponse(generate(),media_type='text/event-stream',headers={'Cache-Control':'no-store','X-Accel-Buffering':'no'})


async def process_history_once():
    """Worker receives sensor records only, never user profiles or credentials."""
    from ..services.homepulse.module import enabled
    if not enabled():return
    url=os.getenv('HOME_PULSE_CORE_URL','http://homepulse-core:8081').rstrip('/')
    for owner in await asyncio.to_thread(store().pending_owners):
        records=await asyncio.to_thread(store().historical_records,owner)
        sensor=[]
        for record in records:
            raw=bytes.fromhex(record['raw_hex'])
            if 6<=len(raw)<=20 and raw[0] not in (0x5c,0xfd,0xfe):sensor.append(record['raw_hex'])
        if not sensor:continue
        canonical={'schema':'standard-ring-events-raw-hex-v1',
                   'records':[bytes.fromhex(raw)[:bytes.fromhex(raw)[1]+2].hex() for raw in sensor]}
        expected_hash=hashlib.sha256(json.dumps(canonical,separators=(',',':')).encode()).hexdigest()
        try:
            async with httpx.AsyncClient(timeout=35,trust_env=False,follow_redirects=False) as client:
                token=os.getenv('HOME_PULSE_CORE_TOKEN')
                response=await client.post(url+'/process',json={'records':sensor},headers={'Authorization':'Bearer '+token} if token else {})
                response.raise_for_status();result=response.json()
                if not isinstance(result,dict) or result.get('source_input_sha256')!=expected_hash or result.get('input_record_count')!=len(sensor):
                    result={'status':'result_mismatch','error':'Risultato non associabile ai record inviati; dati conservati',
                            'rejected_response':result}
        except (httpx.HTTPError,ValueError):result={'status':'worker_unavailable','error':'Motore locale non raggiungibile; record conservati'}
        await asyncio.to_thread(store().publish_processed,owner,records,result)


async def processing_loop():
    while True:
        try:await process_history_once()
        except asyncio.CancelledError:raise
        except Exception:
            # No raw payloads, keys or owner identifiers are printed to logs.
            pass
        await asyncio.sleep(10)
