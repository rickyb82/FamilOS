import time
import uuid
import pytest
from app.services.homepulse.store import HomePulseStore,StoreError


def workout(store,owner,name,start,end=None):
    events=[dict(event_id=uuid.uuid4().hex,sequence=1,captured_at=start,kind='workout',payload=dict(action='started',session_id=name))]
    if end is not None:events.append(dict(event_id=uuid.uuid4().hex,sequence=2,captured_at=end,kind='workout',payload=dict(action='ended',session_id=name,active_seconds=end-start)))
    store.append(owner,'phone-'+owner,uuid.uuid4().hex,events)


def test_cleanup_is_durable_owner_scoped_and_preserves_future_sessions_and_sensors(tmp_path):
    store=HomePulseStore(tmp_path/'diary');now=time.time();start=now-3600;end=start+1200;cutoff=now-100
    for name,a,b in [('before',start-500,start-1),('overlap',start-60,start+60),('inside',start+100,end-10),('after',end+1,end+60),('future',cutoff+1,cutoff+20)]:workout(store,'a',name,a,b)
    workout(store,'b','other-owner',start-500,start-1)
    with store.connection(True) as db:store._point(db,'a','heart_rate',start,90,'synthetic','sensor-1')
    rule=dict(retain_start=start,retain_end=end,created_before=cutoff)
    result=store.cleanup_diary('a',rule)
    assert result['removed_from_diary']==2 and result['sensor_data_preserved']
    assert {s['id'] for s in store.state('a')['sessions']}=={'overlap','inside','future'}
    assert len(store.state('b')['sessions'])==1
    assert store.cleanup_diary('a',rule)['removed_from_diary']==0
    assert len(store.analysis('a',start=start,end=end)['heart_rate'])==1
    with pytest.raises(StoreError):store.analysis('a',session_id='before')
    with pytest.raises(StoreError):store.samples('a','before')
    workout(store,'a','late-old',start-1000,start-500)
    assert 'late-old' not in {s['id'] for s in HomePulseStore(store.base).state('a')['sessions']}
    with store.connection() as db:
        assert db.execute('SELECT count(*) FROM sessions WHERE owner_id=?',('a',)).fetchone()[0]==6
        assert db.execute('SELECT count(*) FROM events WHERE owner_id=?',('a',)).fetchone()[0]==12


def test_bounded_diary_keeps_original_extrema_gaps_and_owner_scope(tmp_path,monkeypatch):
    store=HomePulseStore(tmp_path/'diary');now=int(time.time());monkeypatch.setattr(time,'time',lambda:now);start=now-7200
    with store.connection(True) as db:
        for i in range(6000):
            if 2000<=i<=2100:continue
            store._point(db,'a','heart_rate',start+i+1,250 if i==4201 else 30 if i==4213 else 80+i%5,'synthetic',str(i))
        store._point(db,'b','heart_rate',now-1,199,'synthetic','secret')
        store._point(db,'a','heart_rate',now-8000,200,'synthetic','older')
    for hours in (2,4,8,24,48,168):
        value=store.diary_analysis('a',hours);points=value['heart_rate']
        assert len(points)<=284 and value['full_samples_preserved']
        assert max(p['v'] for p in points)==250 and min(p['v'] for p in points)==30
        assert len({p['segment'] for p in points})>=2
        assert all(p['v']!=199 for p in points)
        assert value['window_end']-value['window_start']==hours*3600
    with store.connection() as db:assert db.execute('SELECT count(*) FROM points WHERE owner_id=?',('a',)).fetchone()[0]==5900
    for hours in (0,1,3,169,True):
        with pytest.raises(StoreError):store.diary_analysis('a',hours)
    for rule in ({},dict(retain_start=1,retain_end=2,created_before=float('inf')),dict(retain_start=4,retain_end=2,created_before=3)):
        with pytest.raises(StoreError):store.cleanup_diary('a',rule)

from test_wellness_sleep import installed_module,owner_id

@pytest.mark.asyncio
async def test_mobile_diary_cleanup_cannot_target_another_owner(admin,anna,kid,installed_module):
    store=installed_module;a=await owner_id('admin');b=await owner_id('anna');now=time.time()
    workout(store,a,'old-a',now-5000,now-4900);workout(store,b,'old-b',now-5000,now-4900)
    pairing=(await admin.post('/api/wellness/v1/bridges/pair')).json()
    device=(await admin.post('/api/wellness/v1/bridges/enroll',json={'pairing_code':pairing['pairing_code']})).json()
    headers={'Authorization':'Bearer '+device['access_token']}
    rule=dict(retain_start=now-3600,retain_end=now-1800,created_before=now-60)
    assert (await admin.post('/api/wellness/v1/mobile/diary/cleanup',json=rule)).status_code==401
    assert (await admin.post('/api/wellness/v1/mobile/diary/cleanup',headers=headers,json={**rule,'owner_id':b})).status_code==400
    response=await admin.post('/api/wellness/v1/mobile/diary/cleanup',headers=headers,json=rule)
    assert response.status_code==200 and response.json()['removed_from_diary']==1
    assert len(store.state(b)['sessions'])==1 and store.state(a)['sessions']==[]
    assert (await admin.get('/api/wellness/v1/mobile/diary?hours=168',headers=headers)).status_code==200
    assert (await kid.get('/api/wellness/v1/diary')).status_code==403
