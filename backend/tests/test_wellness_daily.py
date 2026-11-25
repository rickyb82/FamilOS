"""Synthetic time-integrated energy; no physiological archive."""
import json
from datetime import datetime
from zoneinfo import ZoneInfo
import pytest
from app.services.homepulse.daily import estimate,window
from app.services.homepulse.store import HomePulseStore
P={'weight_kg':80,'height_cm':180,'age':40,'energy_sex':'male'}
def interval(a,b,met=3,priority=2):return {'start':a,'end':b,'met':met,'source':'synthetic','priority':priority}

def test_rest_and_net_activity_are_added_once_with_exact_partial_intervals():
    result=estimate(P,[interval(0,60),interval(0,60),interval(0,60,8,1)],0,30)
    assert result['active_kcal']==pytest.approx(2.8*30/60)
    assert result['base_kcal']==pytest.approx(1730*30/86400)
    assert result['total_kcal']==pytest.approx(result['active_kcal']+result['base_kcal'])
    assert result['estimated_activity_seconds']==30 and result['unestimated_activity_seconds']==0

def test_conflicts_missing_mets_and_absent_weight_do_not_become_activity():
    assert estimate(P,[interval(0,60,3),interval(0,60,4)],0,60)['estimated_activity_seconds']==0
    assert estimate(P,[interval(0,60,0)],0,60)['unestimated_activity_seconds']==60
    assert estimate({},[interval(0,60)],0,60)['active_kcal'] is None

def test_profile_conflict_uses_mifflin_not_invented_lean_mass():
    result=estimate({**P,'body_fat_pct':30,'reported_lean_pct':60},[],0,86400)
    assert result['rest_method']=='Mifflin-St-Jeor-1990' and result['base_kcal']==1730
    both=estimate({**P,'energy_sex':None},[],0,86400)
    assert both['base_kcal'] is None and both['base_kcal_range']=={'min':1564,'max':1730}

def test_hour_buckets_and_dst_follow_elapsed_seconds():
    zone=ZoneInfo('Europe/Rome')
    now=datetime(2026,10,26,1,tzinfo=zone).timestamp()
    day,a,b=window(now,'2026-10-25')
    assert b-a==25*3600
    result=estimate(P,[interval(a,b)],a,b)
    assert len(result['hourly'])==25
    assert sum(v['active_kcal'] for v in result['hourly'])==pytest.approx(result['active_kcal'])

def test_store_uses_acquired_mets_with_owner_isolation_and_durable_history(tmp_path,monkeypatch):
    archive=HomePulseStore(tmp_path/'day');archive.update_settings('a',{'profile':P})
    now=datetime(2026,11,25,12,tzinfo=ZoneInfo('Europe/Rome')).timestamp()
    monkeypatch.setattr('app.services.homepulse.store.time.time',lambda:now)
    with archive.connection(True) as db:
        for owner,levels in [('a',[1,3,5]),('b',[30,30,30])]:
            db.execute('INSERT INTO points(owner_id,kind,t,value,source,quality,metadata,identity) VALUES(?,?,?,?,?,?,?,?)',(owner,'steps',now,10,'oura-activity-history',None,json.dumps({'met_levels':levels,'step_count':10}),'synthetic'))
    result=archive.daily_energy('a')
    assert result['enabled'] and result['active_kcal']==pytest.approx((0+2+4)*3.5*80/200)
    assert result['estimated_activity_seconds']==180 and result['steps_in_records']==10
    assert 'oura-minute-met' in result['activity_sources']
    assert archive.daily_energy('unknown')['active_kcal'] is None
    assert HomePulseStore(archive.base).daily_energy('a')['active_kcal']==result['active_kcal']
