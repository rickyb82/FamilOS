"""Synthetic large-archive replay, clock proof and durable checkpoint regressions."""
import hashlib
import importlib.util
from pathlib import Path
import time

import pytest

from app.services.homepulse import processing
from app.services.homepulse.store import HomePulseStore
from app.services.homepulse.sleep import NATIVE_ABI


def record(tick, tag=0x47, ident=0):
    raw=bytes((tag,6))+tick.to_bytes(4,'little')+ident.to_bytes(2,'little')
    return dict(id=ident,ring_id='synthetic',cursor=tick,tag=tag,raw_hex=raw.hex(),digest=hashlib.sha256(raw).hexdigest())


def test_archive_over_native_limit_is_complete_bounded_and_overlapped():
    records=[record(0,0x41),record(1,0x42)]+[record(i*100,ident=i) for i in range(1,65535)]+[record(6553500,ident=65535)]
    jobs,replay=processing.plan(records)
    assert len(jobs)==2
    assert all(len(job)<=65536 for job in jobs)
    assert {row['raw_hex'] for job in jobs for row in job}=={r['raw_hex'] for r in records}
    assert jobs[1][0]['tag']==0x41 and jobs[1][1]['tag']==0x42
    assert jobs[0][-1]['cursor']-jobs[1][2]['cursor']>=processing.OVERLAP_TICKS-100
    assert replay['synthetic']==records[-1]['cursor']-processing.OVERLAP_TICKS


def test_density_and_missing_cursor_fail_without_truncation():
    with pytest.raises(processing.PlanningError,match='Densità'):
        processing.plan([record(i,ident=i) for i in range(9)],budget=5)
    with pytest.raises(processing.PlanningError,match='posizione'):
        processing.plan([record(1),{**record(2),'cursor':None}])
    assert processing.http_failure(400,{'status':'input_limit'})['http_status']==400
    assert 'input_limit' in processing.http_failure(400,{'status':'input_limit'})['error']


def test_resume_cache_and_incremental_tail_are_owner_scoped(tmp_path):
    store=HomePulseStore(tmp_path/'private')
    rows=[record(1,0x41),record(2,0x42),record(100,ident=3),record(900000,ident=4)]
    with store.connection(True) as db:
        for row in rows:
            db.execute('INSERT INTO history(owner_id,ring_id,raw_hex,digest,tag,cursor,received_at) VALUES(?,?,?,?,?,?,?)',('a',row['ring_id'],row['raw_hex'],row['digest'],row['tag'],row['cursor'],time.time()))
    snapshot=store.processing_snapshot('a');jobs,replay=processing.plan(snapshot['records'])
    digest=processing.input_hash(jobs[0])
    result={'status':'ok','process_true':True,'deinit_returned':True,'version':[3,6,2],'mapping':{'complete':True},'sleep_native_abi':NATIVE_ABI,'sleep_capture_complete':True,'source_input_sha256':digest,'gen3_sleep':{'contract':gen3.CONTRACT,'periods':[]}}
    store.publish_processed('a',jobs[0],result,False)
    assert store.pending_owners()==['a']
    assert store.cached_processing_job('a',digest)==result
    assert store.cached_processing_job('b',digest) is None
    store.finish_processing('a',snapshot,replay)
    assert store.pending_owners()==[]
    tail=store.processing_snapshot('a')['records']
    assert [r['tag'] for r in tail]==[0x41,0x42,0x47]
    assert tail[-1]['cursor']==900000
    store.publish_processed('a',[],{'status':'worker_input_limit','error':'HTTP400'},False)
    with store.connection() as db:
        assert db.execute('SELECT history_count FROM processing WHERE owner_id="a"').fetchone()[0]==4


spec=importlib.util.spec_from_file_location('gen3_sleep',Path(__file__).parents[2]/'extras/homepulse-core/gen3_sleep.py')
gen3=importlib.util.module_from_spec(spec);spec.loader.exec_module(gen3)


def padded(tag,tick,payload=b''):
    return (bytes((tag,len(payload)+4))+tick.to_bytes(4,'little')+payload).ljust(20,b'\0')


def synthetic_period(offset=1_700_000_000_000):
    frames=[padded(0x41,1),padded(0x42,2),padded(0x6a,100),padded(0x6a,150),padded(0x76,300,(150).to_bytes(4,'little')+(290).to_bytes(4,'little'))]
    return frames,[(raw,offset+int.from_bytes(raw[2:6],'little')*100) for raw in frames]


def test_gen3_bedtime_uses_native_epoch_and_never_invents_stages():
    frames,mapped=synthetic_period()
    result=gen3.extract(frames,mapped,verified=True)
    assert len(result['periods'])==1
    period=result['periods'][0]
    assert period['started_at_utc_ms']==1_700_000_015_000
    assert period['time_in_bed_seconds']==14
    assert period['total_sleep_seconds'] is None and period['stage_seconds'] is None
    assert gen3.extract(frames,mapped,verified=False)['periods']==[]
    # An old TimeSync prefix alone cannot prove coverage before a window.
    assert gen3.extract(frames[:2]+frames[-1:],mapped[:2]+mapped[-1:],verified=True)['rejected']=={'incomplete_same_boot_coverage':1}
    bad=[*mapped[:-1],(mapped[-1][0],mapped[-1][1]+1000)]
    assert gen3.extract(frames,bad,verified=True)['rejected']=={'clock_discontinuity':1}
    reset=frames[:-1]+[padded(0x41,200)]+frames[-1:]
    assert gen3.extract(reset,[(raw,1_700_000_000_000+int.from_bytes(raw[2:6],'little')*100) for raw in reset],verified=True)['periods']==[]


def test_firmware_bedtime_persists_without_sleep_totals(tmp_path):
    frames,mapped=synthetic_period(offset=int(time.time()*1000)-50000)
    capture=gen3.extract(frames,mapped,verified=True)
    store=HomePulseStore(tmp_path/'private')
    result={'status':'ok','process_true':True,'deinit_returned':True,'version':[3,6,2],'mapping':{'complete':True},'sleep_native_abi':NATIVE_ABI,'sleep_capture_complete':True,'gen3_sleep':capture}
    store.publish_processed('a',[],result)
    period=store.sleep('a')['periods'][0]
    assert period['source']=='oura-ring-bedtime' and period['display_time_zone']=='Europe/Rome'
    assert period['total_sleep_seconds'] is None and period['phases_30_seconds'] is None
    assert store.sleep('b')['periods']==[]
    store.publish_processed('a',[],{**result,'version':[3,6,1]})
    assert len(store.sleep('a')['periods'])==1


@pytest.mark.asyncio
async def test_interrupted_replay_reuses_completed_job_and_only_commits_complete_archive(tmp_path,monkeypatch):
    import httpx
    from app.api import wellness
    from app.services.homepulse import module
    store=HomePulseStore(tmp_path/'private')
    rows=[record(0,0x41),record(1,0x42)]+[record(i*10000,ident=i) for i in range(1,6)]
    with store.connection(True) as db:
        for row in rows:db.execute('INSERT INTO history(owner_id,ring_id,raw_hex,digest,tag,cursor,received_at) VALUES(?,?,?,?,?,?,?)',('a',row['ring_id'],row['raw_hex'],row['digest'],row['tag'],row['cursor'],time.time()))
    monkeypatch.setattr(wellness,'store',lambda:store)
    monkeypatch.setattr(module,'enabled',lambda:True)
    original=processing.plan
    monkeypatch.setattr(processing,'plan',lambda records:original(records,budget=4))
    monkeypatch.setattr(processing,'OVERLAP_TICKS',1000)
    calls=[]
    class Client:
        def __init__(self,**kwargs):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def post(self,url,*,json,headers):
            calls.append(json)
            if len(calls)==2:return httpx.Response(500,json={'status':'worker_error'})
            payload={'schema':'standard-ring-events-raw-hex-v1','records':json['records']}
            import json as codec
            digest=hashlib.sha256(codec.dumps(payload,separators=(',',':')).encode()).hexdigest()
            return httpx.Response(200,json={'status':'ok','process_true':True,'deinit_returned':True,'version':[3,6,2],'input_record_count':len(json['records']),'source_input_sha256':digest,'mapping':{'complete':True},'sleep_native_abi':NATIVE_ABI,'sleep_capture_complete':True,'gen3_sleep':{'contract':gen3.CONTRACT,'periods':[]}})
    monkeypatch.setattr(wellness.httpx,'AsyncClient',Client)
    await wellness.process_history_once()
    assert store.pending_owners()==['a']
    assert 'HTTP 500' in store.sleep('a')['processing_error']
    await wellness.process_history_once()
    assert len(calls)==3 and calls[1]==calls[2]
    assert store.pending_owners()==[] and store.sleep('a')['processing_error'] is None
    assert len(store.historical_records('a'))==7
