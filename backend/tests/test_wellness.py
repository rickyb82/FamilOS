"""Real temporary SQLite + authenticated ASGI; synthetic sensor data only."""
import hashlib
import json
from pathlib import Path
import time
import uuid

import pytest

from app.services.homepulse.store import HomePulseStore,StoreError,ConflictError,occurrences,validate_settings
from app.api import wellness
from conftest import make_client

LIVE=bytes.fromhex('2f0f280211020000201300000000ac0d64')


def event(kind='sensor_packet',payload=None,stamp=None,identity=None):
    return {'event_id':identity or uuid.uuid4().hex,'sequence':1,'captured_at':time.time() if stamp is None else stamp,
            'kind':kind,'payload':payload if payload is not None else {'raw_hex':LIVE.hex(),'api_version':[2,0,0]}}


@pytest.fixture
def archive(tmp_path):
    return HomePulseStore(tmp_path/'homepulse')


def test_ack_is_durable_idempotent_and_owner_private(archive):
    first=event()
    receipt=archive.append('a','phone-a','batch',[first])
    assert receipt['accepted_event_ids']==[first['event_id']]
    fresh=HomePulseStore(archive.base)
    replay=fresh.append('a','phone-a','again',[first])
    assert replay['duplicates']==1
    assert fresh.state('a')['archive']['events']==1
    assert fresh.state('a')['latest']['heart_rate']['bpm']==75
    assert fresh.state('a')['latest']['heart_rate']['warnings']==['movimento segnalato']
    assert fresh.state('b')['archive']['events']==0
    assert fresh.state('b')['latest']=={}


def test_conflicting_replay_rolls_back_whole_batch(archive):
    original=event();archive.append('a','phone','first',[original])
    another=event()
    changed={**original,'captured_at':original['captured_at']+1}
    with pytest.raises(ConflictError):archive.append('a','phone','second',[another,changed])
    assert archive.state('a')['archive']['events']==1


def test_unknown_and_invalid_sensor_fields_survive(archive):
    unknown=event(payload={'raw_hex':'2f0428fa','unknown_field':{'raw':42}})
    invalid=event(payload={'raw_hex':'2f0f280211020000202300000000ac0d64'})
    assert len(archive.append('a','phone','raw',[unknown,invalid])['accepted_event_ids'])==2
    state=archive.state('a')
    assert state['archive']['events']==2
    assert state['latest'].get('heart_rate') is None
    with archive.connection() as db:
        value=json.loads(db.execute('SELECT payload FROM events WHERE event_id=?',(unknown['event_id'],)).fetchone()[0])
        assert value['unknown_field']=={'raw':42}


def test_pairing_one_use_revocation_and_encrypted_key(archive):
    pair=archive.create_pairing('owner')
    device=archive.enroll(pair['pairing_code'],'iPhone')
    with pytest.raises(StoreError):archive.enroll(pair['pairing_code'],'attacker')
    assert archive.authenticate(device['access_token'])['owner_id']=='owner'
    archive.revoke('owner',device['device_id'])
    assert archive.authenticate(device['access_token']) is None
    with archive.connection() as db:
        assert device['access_token'] not in str(tuple(db.execute('SELECT * FROM devices').fetchone()))


def test_expired_pairing_is_not_accepted(archive):
    pair=archive.create_pairing('a')
    with archive.connection(True) as db:db.execute('UPDATE pairings SET expires_at=0')
    with pytest.raises(StoreError):archive.enroll(pair['pairing_code'],'phone')


def test_history_packets_deduplicate_separately_from_event_journal(archive):
    payload={'raw_hex':'470a010000000f01020380005006020000000304'}
    # First record is12 bytes, followed by an8-byte ActivityInfo record.
    archive.append('a','phone','one',[event('ring_history',payload)])
    archive.append('a','phone','two',[event('ring_history',payload)])
    state=archive.state('a')
    assert state['archive']['events']==2
    assert state['archive']['history_records']==2


def test_delayed_upload_never_becomes_live_or_moves_in_graph(archive):
    stamp=time.time()-7200
    archive.append('a','phone','old',[event(stamp=stamp)])
    assert archive.state('a')['latest']['heart_rate']['fresh'] is False
    graph=archive.analysis('a',start=stamp-1,end=stamp+1)
    assert graph['heart_rate'][0]['t']==stamp
    assert graph['heart_rate'][0]['v']==75
    assert graph['acquisition_time_alignment'] is True


def test_authentication_packets_never_enter_sensor_archive(archive):
    for raw in ('2f102c'+'00'*15,'2410'+'00'*16):
        with pytest.raises(StoreError):archive.append('a','p','bad',[event(payload={'raw_hex':raw})])
    assert archive.state('a')['archive']['events']==0


def test_session_offline_recovery_and_false_detection_preserve_raw(archive):
    start=time.time()-120;identity=uuid.uuid4().hex
    data=[event('workout',{'action':'started','session_id':identity,'auto_detected':True,'provisional':True},start),
          event(stamp=start+10),
          event('workout',{'action':'activity_updated','session_id':identity,'activity_name':'Cyclette'},start+30),
          event('workout',{'action':'ended','session_id':identity,'active_seconds':90},start+90)]
    archive.append('a','p','workout',data)
    session=archive.state('a')['sessions'][0]
    assert session['status']=='completed'
    assert session['activity_name']=='Cyclette'
    assert session['provisional'] is False
    assert session['auto_detected'] is True
    assert session['active_seconds']==90
    assert archive.analysis('a',identity)['heart_rate'][0]['t']==start+10
    with pytest.raises(StoreError):archive.analysis('b',identity)


def test_stale_workout_control_cannot_rewind_current_session(archive):
    start=time.time()-100;identity='session-a'
    archive.append('a','p','start',[event('workout',{'action':'started','session_id':identity},start)])
    archive.append('a','p','end',[event('workout',{'action':'ended','session_id':identity,'active_seconds':90},start+90)])
    archive.append('a','p','old-control',[event('workout',{'action':'resumed','session_id':identity,'active_seconds':20},start+20)])
    assert archive.state('a')['sessions'][0]['status']=='completed'
    assert archive.state('a')['archive']['events']==3


def test_background_batches_can_arrive_out_of_order(archive):
    stamp=time.time()-100
    ended=event('workout',{'action':'ended','session_id':'late-start','active_seconds':60},stamp+60)
    started=event('workout',{'action':'started','session_id':'late-start','activity_name':'Circuito'},stamp)
    archive.append('a','phone','ends-first',[ended])
    assert archive.state('a')['sessions']==[]
    archive.append('a','phone','start-later',[started])
    assert archive.state('a')['sessions'][0]['status']=='completed'
    assert archive.state('a')['sessions'][0]['active_seconds']==60


def test_uncommitted_data_is_not_acknowledged(archive,monkeypatch):
    original=archive._project
    calls=[]
    def fail(db,owner,device,value):
        calls.append(value)
        if len(calls)==2:raise OSError('simulated disk failure')
        return original(db,owner,device,value)
    monkeypatch.setattr(archive,'_project',fail)
    with pytest.raises(OSError):archive.append('a','phone','no-ack',[event(),event()])
    recovered=HomePulseStore(archive.base)
    assert recovered.state('a')['archive']['events']==0


def test_confirmation_preserves_elapsed_and_paused_session(archive):
    stamp=time.time()-100
    archive.append('a','phone','start',[event('workout',{'action':'started','session_id':'auto','provisional':True},stamp)])
    archive.append('a','phone','label',[event('workout',{'action':'activity_updated','session_id':'auto','activity_name':'Pesi'},stamp+40)])
    active=archive.state('a')['active_session']
    assert 99 <= active['active_seconds'] <= 102
    assert not active['provisional']
    archive.append('a','phone','pause',[event('workout',{'action':'paused','session_id':'auto','active_seconds':80},stamp+80)])
    assert archive.state('a')['active_session']['status']=='paused'
    assert archive.state('a')['active_session']['active_seconds']==80


def test_outside_display_heart_rate_is_retained_without_poisoning_summary(archive):
    raw=bytearray(LIVE)
    raw[8]=0x90;raw[9]=0x11  # valid400ms →150bpm
    normal=event(payload={'raw_hex':raw.hex()})
    raw[8]=100;raw[9]=0x10  # valid100ms →600bpm, raw remains available
    extreme=event(payload={'raw_hex':raw.hex()})
    archive.append('a','phone','range',[normal,extreme])
    state=archive.state('a')
    assert state['latest']['heart_rate']['value']==150
    assert state['latest']['heart_rate_outside_display']['value']==600
    graph=archive.analysis('a')
    assert graph['stats']['max_bpm']==150
    assert graph['heart_rate_outside_display'][0]['v']==600
    assert state['archive']['events']==2


def test_native_outputs_and_checkpoint_are_one_durable_transaction(archive,monkeypatch):
    stamp=(time.time()-60)*1000
    rejected={'data_type':6,'average_raw':66,'quality_raw':0.2,'timestamp_utc_ms_native':stamp}
    accepted={**rejected,'average_raw':97,'quality_raw':1}
    result={'status':'ok','process_true':True,'deinit_returned':True,'spo2_outputs':[rejected,accepted],'unknown_output':42}
    def fail(*args,**kwargs):raise OSError('disk full')
    original=archive._point
    monkeypatch.setattr(archive,'_point',fail)
    with pytest.raises(OSError):archive.publish_processed('a',[{'digest':'x'}],result)
    with archive.connection() as db:
        assert db.execute('SELECT count(*) FROM native_runs').fetchone()[0]==0
        assert db.execute('SELECT count(*) FROM processing').fetchone()[0]==0
    monkeypatch.setattr(archive,'_point',original)
    archive.publish_processed('a',[{'digest':'x'}],result)
    archive.publish_processed('a',[{'digest':'x'}],result)
    with archive.connection() as db:
        rows=db.execute('SELECT result FROM native_runs WHERE owner_id=?',('a',)).fetchall()
        assert len(rows)==1
        assert json.loads(rows[0][0])['spo2_outputs']==[rejected,accepted]
        assert db.execute('SELECT history_count FROM processing').fetchone()[0]==1
    assert archive.state('a')['latest']['spo2']['value']==97
    assert archive.state('b')['latest']=={}


@pytest.mark.asyncio
async def test_native_response_for_different_input_is_archived_and_retried(archive,monkeypatch):
    archive.append('a','p','clock',[event('ring_history',{'raw_hex':'420de803000000e10b5e0000000000'})])
    wrong={'status':'ok','process_true':True,'deinit_returned':True,'input_record_count':1,
           'source_input_sha256':'wrong-input','spo2_outputs':[{'data_type':6,'average_raw':97,'quality_raw':1,'timestamp_utc_ms_native':time.time()*1000}]}
    class Client:
        def __init__(self,**kwargs):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def post(self,*args,**kwargs):
            import httpx
            return httpx.Response(200,json=wrong,request=httpx.Request('POST','http://core/process'))
    monkeypatch.setattr(wellness,'store',lambda:archive)
    monkeypatch.setattr(wellness.httpx,'AsyncClient',Client)
    await wellness.process_history_once()
    assert archive.state('a')['latest'].get('spo2') is None
    assert archive.pending_owners()==['a']
    with archive.connection() as db:
        rejected=json.loads(db.execute('SELECT result FROM native_runs').fetchone()[0])
        assert rejected['status']=='result_mismatch'
        assert rejected['rejected_response']==wrong


def test_europe_rome_schedule_keeps_wall_time_across_dst():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    zone=ZoneInfo('Europe/Rome')
    weekly=[{'id':'mon','weekday':1,'start':'13:30','end':'14:15','timezone':'Europe/Rome','label':'Palestra'}]
    now=datetime(2027,3,22,9,tzinfo=zone).timestamp()
    values=occurrences(weekly,now,days=10)
    assert len(values)==2
    assert all(v['duration_minutes']==45 for v in values)
    assert all(datetime.fromtimestamp(v['starts_at'],zone).hour==13 for v in values)
    assert values[1]['starts_at']-values[0]['starts_at']==7*86400-3600


def test_ambiguous_body_composition_does_not_invent_basal(archive):
    archive.update_settings('a',{'profile':{'weight_kg':100,'height_cm':175,'body_fat_pct':33,'reported_lean_pct':60,'reported_retention_pct':7}})
    assert archive.state('a')['composition']['basal_kcal_day'] is None


@pytest.mark.asyncio
async def test_api_native_enrollment_upload_owner_separation_and_revoke(admin,anna,kid):
    wellness.pair_limiter.hits.clear()
    pair=(await admin.post('/api/wellness/v1/bridges/pair')).json()
    assert pair.get('qr_png_data_url','').startswith('data:image/png;base64,')
    async with make_client() as anonymous:
        enroll=await anonymous.post('/api/wellness/v1/bridges/enroll',json={'pairing_code':pair['pairing_code'],'device_name':'Test iPhone'})
        assert enroll.status_code==200,enroll.text
        device=enroll.json()
        again=await anonymous.post('/api/wellness/v1/bridges/enroll',json={'pairing_code':pair['pairing_code']})
        assert again.status_code==400
    async with make_client(token=device['access_token']) as mobile:
        first=event()
        value=await mobile.post('/api/wellness/v1/bridges/events',json={'batch_id':'batch-native','events':[first]})
        assert value.status_code==200,value.text
        assert value.json()['accepted_event_ids']==[first['event_id']]
        assert (await mobile.get('/api/wellness/v1/mobile/bootstrap')).status_code==200
        assert (await mobile.get('/api/wellness/v1/state')).status_code==401
        assert (await anna.get('/api/wellness/v1/state')).json()['latest']=={}
        assert (await kid.get('/api/wellness/v1/state')).status_code==403
        assert (await anna.delete('/api/wellness/v1/bridges/'+device['device_id'])).status_code==400
        assert (await admin.delete('/api/wellness/v1/bridges/'+device['device_id'])).status_code==200
        assert (await mobile.get('/api/wellness/v1/mobile/bootstrap')).status_code==401


@pytest.mark.asyncio
async def test_ring_key_is_only_returned_to_enrolled_owner_device(admin,anna):
    key='00112233445566778899aabbccddeeff'
    result=await admin.post('/api/wellness/v1/ring-binding',json={'owner_key_hex':key,'logical_ring_id':'ring-test'})
    assert result.status_code==200
    state=(await admin.get('/api/wellness/v1/state')).json()
    assert key not in json.dumps(state)
    assert (await anna.get('/api/wellness/v1/state')).json()['ring_binding'] is None
    with wellness.store().connection() as db:
        row=db.execute('SELECT encrypted_key FROM ring_bindings WHERE logical_ring_id=?',('ring-test',)).fetchone()
        assert row[0]!=key


@pytest.mark.asyncio
async def test_browser_csrf_and_anonymous_access_remain_enforced(admin):
    async with make_client() as client:
        assert (await client.get('/api/wellness/v1/state')).status_code==401
    response=await admin.put('/api/wellness/v1/settings',json={},headers={'X-Requested-With':'untrusted','Origin':'https://outside.example'})
    assert response.status_code==403
