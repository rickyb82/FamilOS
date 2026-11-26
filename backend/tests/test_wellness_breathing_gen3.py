"""Gen3 firmware respiratory means: exact bytes, native UTC and owner isolation."""
import hashlib
import time
from copy import deepcopy

import pytest

from app.services.homepulse.breathing import decode_sleep_breathing_record
from app.services.homepulse.protocol import OuraProtocolError
from app.services.homepulse.processing import SENSOR_MAPPING_ABI
from app.services.homepulse.sleep import NATIVE_ABI
from app.services.homepulse.store import HomePulseStore


def frame(rate=128, *, state=1, motion=0, tick=100):
    return bytes((0x6a,14))+tick.to_bytes(4,'little')+bytes((120,0,10,1,rate,8,motion,state,0,0))


def test_known_eighths_keep_mean_variability_and_missing_values():
    decoded=decode_sleep_breathing_record(frame(129))
    assert decoded['average_breathing_rate_per_minute']==16.125
    assert decoded['std_breathing_rate_per_minute']==1
    assert decoded['timestamp_raw']==100
    assert decoded['is_live'] is False and decoded['averaging_window_seconds'] is None
    assert decoded['physiological_quality_validated'] is False
    assert decode_sleep_breathing_record(frame(0))['available'] is False
    assert decode_sleep_breathing_record(frame(255))['average_breathing_rate_per_minute']==31.875
    extended=bytes((0x6a,18))+frame()[2:]+bytes((1,2,3,4))
    assert decode_sleep_breathing_record(extended)['average_breathing_rate_per_minute']==16
    for raw in (frame()[:-1],frame(state=3),frame(state=255),frame(motion=121),bytes((0x62,))+frame()[1:]):
        with pytest.raises(OuraProtocolError):decode_sleep_breathing_record(raw)


def test_native_mapped_respiratory_series_backfills_diary_and_sleep(tmp_path):
    store=HomePulseStore(tmp_path/'private');now=int(time.time());rows=[];mapped=[]
    for i,rate in enumerate((120,136,0)):
        raw=frame(rate,tick=100+i)
        event={'event_id':str(i),'sequence':i,'captured_at':now,'kind':'ring_history','payload':{'raw_hex':raw.hex()}}
        store.append('a','synthetic-phone','batch-'+str(i),[event])
        mapped.append({'record_sha256':hashlib.sha256(raw).hexdigest(),'utc_ms':(now-180+i*30)*1000,'tag_raw':0x6a})
    rows=store.historical_records('a')
    start=now-300;end=now-60
    period={'native_version':[3,6,2],'native_abi':NATIVE_ABI,'bedtime_start_utc_seconds':start,'bedtime_end_utc_seconds':end,'time_in_bed_seconds':end-start,'bedtime_start_time_zone_minutes':60}
    result={'status':'ok','process_true':True,'deinit_returned':True,'version':[3,6,2],'mapping':{'complete':True},'sleep_native_abi':NATIVE_ABI,'sleep_capture_complete':True,'sleep_outputs':[period],'mapped_sensor_records':mapped,'sensor_mapping_abi':SENSOR_MAPPING_ABI}
    store.publish_processed('a',rows,result)
    store.publish_processed('a',rows,result)
    assert [p['v'] for p in store.diary_analysis('a')['respiratory_rate']]==[15,17]
    summary=store.sleep('a')['periods'][0]['respiratory_rate']
    assert summary['sample_count']==2 and summary['average']==16
    assert summary['minimum']==15 and summary['maximum']==17
    assert summary['aggregation']=='arithmetic-mean-of-record-means'
    assert store.state('a')['latest']['respiratory_rate']['captured_at']==now-150
    assert store.diary_analysis('b')['respiratory_rate']==[]
    assert len(store.historical_records('a'))==3
    for overrides in ({'version':[3,6,1]},{'mapping':{'complete':False}},{'sensor_mapping_abi':'unknown'}):
        store.publish_processed('b',rows,{**deepcopy(result),**overrides})
    assert store.diary_analysis('b')['respiratory_rate']==[]
