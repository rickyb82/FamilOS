"""Synthetic session samples: no downsampling, personal archives or radio."""
import json
import pytest
from app.services.homepulse.store import HomePulseStore, StoreError

START=1_600_000_000

@pytest.fixture
def archive(tmp_path):
    store=HomePulseStore(tmp_path/'samples')
    store.append('a','phone','session',[
        {'event_id':'start','sequence':1,'captured_at':START,'kind':'workout','payload':{'action':'started','session_id':'workout'}},
        {'event_id':'end','sequence':2,'captured_at':START+60,'kind':'workout','payload':{'action':'ended','session_id':'workout','active_seconds':60}}])
    with store.connection(True) as db:
        for owner in ('a','b'):
            for index,offset in enumerate((1,2,2,3,60,65)):
                db.execute('INSERT INTO points(owner_id,kind,t,value,source,quality,metadata,identity) VALUES(?,?,?,?,?,?,?,?)',
                    (owner,'heart_rate',START+offset,100+index,'oura-recorded-offline',.9,json.dumps({'interpolated':True,'provisional':True,'model':'WHR-U2.5.1','revision':1}),str(index)))
        for metric,value in [('spo2',97),('temperature',33.5),('motion',12),('steps',7),('respiratory_rate',15)]:
            db.execute('INSERT INTO points(owner_id,kind,t,value,source,quality,metadata,identity) VALUES(?,?,?,?,?,?,?,?)',('a',metric,START+2,value,'synthetic',1,'{}',metric))
    return store

def test_pages_preserve_all_intermediate_samples_including_equal_timestamps(archive):
    first=archive.samples('a','workout',limit=2)
    assert first['total_points']==5 and first['has_more']
    points=first['points'];page=first
    while page['has_more']:
        page=archive.samples('a','workout',limit=2,cursor=page['next_cursor'])
        points+=page['points']
    assert [p['v'] for p in points]==[100,101,102,103,104]
    assert points[1]['t']==points[2]['t']
    assert all(p['interpolated'] and p['provisional'] and p['quality']==.9 for p in points)
    assert points[0]['elapsed_seconds']==1 and points[0]['captured_at'].endswith('+00:00')
    assert not page['downsampled'] and not page['pagination_snapshot']

def test_range_and_recovery_are_explicit_not_mixed_with_workout(archive):
    limited=archive.samples('a','workout',start_offset_seconds=2,end_offset_seconds=3)
    assert [p['v'] for p in limited['points']]==[101,102,103]
    assert limited['range_stats']=={'min':101,'max':103,'mean':102}
    assert archive.samples('a','workout',include_recovery=True)['total_points']==6
    assert archive.samples('a','workout',start_offset_seconds=80)['points']==[]

@pytest.mark.parametrize('metric,unit,value',[('spo2','%',97),('temperature','°C',33.5),('motion','s',12),('step_increments','steps',7),('respiratory_rate','breaths/min',15)])
def test_other_metrics_keep_values_and_acquisition_times(archive,metric,unit,value):
    page=archive.samples('a','workout',metric=metric)
    assert page['unit']==unit and page['points'][0]['v']==value
    assert page['points'][0]['t']==START+2

def test_owner_isolation_and_query_bound_cursor(archive):
    token=archive.samples('a','workout',limit=1)['next_cursor']
    with pytest.raises(StoreError):archive.samples('b','workout')
    for args in ({'metric':'spo2','cursor':token},{'cursor':'broken'},{'cursor':'W10='},{'limit':True},{'limit':1001},{'include_recovery':1},{'start_offset_seconds':float('nan')},{'start_offset_seconds':3,'end_offset_seconds':2}):
        with pytest.raises(StoreError):archive.samples('a','workout',**args)
