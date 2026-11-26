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


@router.get('/diary')
async def diary(hours:int=2,ctx:AuthContext=Depends(require_adult)):
    try:return await asyncio.to_thread(store().diary_analysis,ctx.user.id,hours)
    except StoreError as exc:raise _error(exc) from None


@router.get('/mobile/diary')
async def mobile_diary(hours:int=2,device:dict=Depends(bridge)):
    try:return await asyncio.to_thread(store().diary_analysis,device['owner_id'],hours)
    except StoreError as exc:raise _error(exc) from None


@router.post('/mobile/diary/cleanup')
async def mobile_diary_cleanup(body:dict,device:dict=Depends(bridge)):
    try:return await asyncio.to_thread(store().cleanup_diary,device['owner_id'],body)
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
    """Replay bounded overlapping jobs; outputs keep native acquisition times."""
    from ..services.homepulse.module import enabled
    from ..services.homepulse import processing
    from ..services.homepulse.sleep import NATIVE_ABI, GEN3_CONTRACT
    if not enabled():return
    await asyncio.to_thread(store().backfill_spo2_catalog)
    url=os.getenv('HOME_PULSE_CORE_URL','http://homepulse-core:8081').rstrip('/')
    for owner in await asyncio.to_thread(store().pending_owners):
        snapshot=await asyncio.to_thread(store().processing_snapshot,owner)
        try:jobs,replay=await asyncio.to_thread(processing.plan,snapshot['records'])
        except (processing.PlanningError,ValueError,KeyError) as exc:
            await asyncio.to_thread(store().publish_processed,owner,[],{'status':'history_plan_failed','error':str(exc),'process_true':False})
            continue
        all_complete=True
        async with httpx.AsyncClient(timeout=35,trust_env=False,follow_redirects=False) as client:
            for records in jobs:
                expected_hash=processing.input_hash(records)
                if await asyncio.to_thread(store().cached_processing_job,owner,expected_hash):continue
                token=os.getenv('HOME_PULSE_CORE_TOKEN')
                try:
                    for attempt in range(3):
                        response=await client.post(url+'/process',json={'records':processing.canonical(records)['records']},headers={'Authorization':'Bearer '+token} if token else {})
                        if response.status_code!=503 or attempt==2:break
                        await asyncio.sleep(1)
                    try:body=response.json()
                    except ValueError:body=None
                    if response.status_code!=200:result=processing.http_failure(response.status_code,body)
                    else:
                        result=body
                        if not isinstance(result,dict) or result.get('source_input_sha256')!=expected_hash or result.get('input_record_count')!=len(records):
                            result={'status':'result_mismatch','error':'Risultato non associabile ai record inviati; dati conservati','rejected_response':result}
                except httpx.TimeoutException:
                    result={'status':'worker_timeout','error':'Il motore storico ha superato il tempo di risposta; record conservati'}
                except httpx.HTTPError:
                    result={'status':'worker_unavailable','error':'Connessione al motore storico non riuscita; record conservati'}
                if result.get('status')=='ok' and (result.get('version')!=[3,6,2] or not isinstance(result.get('mapping'),dict) or result['mapping'].get('complete') is not True):
                    result={'status':'native_contract_failed','error':'Contratto o mapping del motore non verificato; record conservati','rejected_response':result}
                await asyncio.to_thread(store().publish_processed,owner,records,result,False)
                if not (result.get('status')=='ok' and result.get('process_true') is True and result.get('deinit_returned') is True and isinstance(result.get('mapping'),dict) and result['mapping'].get('complete') is True and result.get('version')==[3,6,2]):
                    all_complete=False;break
                if (result.get('sleep_capture_complete') is not True or result.get('sleep_callback_errors') or result.get('sleep_native_abi')!=NATIVE_ABI or not isinstance(result.get('gen3_sleep'),dict) or result['gen3_sleep'].get('contract')!=GEN3_CONTRACT):
                    errors=result.get('sleep_callback_errors') or ['Cattura sonno incompleta']
                    await asyncio.to_thread(store().publish_processed,owner,[],{'status':'sleep_decode_failed','error':'Decodifica sonno da completare: '+str(errors[0])[:200]})
                    all_complete=False;break
        if all_complete:await asyncio.to_thread(store().finish_processing,owner,snapshot,replay)


async def processing_loop():
    while True:
        try:await process_history_once()
        except asyncio.CancelledError:raise
        except Exception:
            # No raw payloads, keys or owner identifiers are printed to logs.
            pass
        await asyncio.sleep(10)
