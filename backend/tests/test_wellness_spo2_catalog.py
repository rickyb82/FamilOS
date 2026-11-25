"""Synthetic replay distinguishes original aggregation from live quality gates."""
import hashlib
import json
import time

import pytest

from app.services.homepulse.store import HomePulseStore
from app.services.homepulse.spo2 import normalize
from test_wellness_sleep import native_period, result


def sample(stamp, value=97, quality=1, **extra):
    return dict(data_type=6, timestamp_utc_ms_native=int(stamp*1000),
                average_raw=value, quality_raw=quality, timezone_raw=60, **extra)


def test_catalog_retains_native_quality_and_range_without_promoting_bad_live_samples(tmp_path):
    store=HomePulseStore(tmp_path/'catalog');start=int(time.time())-7200
    samples=[sample(start+60,96,0),sample(start+120,99,1),sample(start+180,108,0),sample(start+240,0,0)]
    store.publish_processed('a',[],result(native_period(start),spo2_outputs=samples))
    saved=store.sleep('a')['periods'][0]['spo2']
    assert saved['average']==97.5 and saved['display_percentage']==98
    assert saved['sample_count']==2 and saved['catalog_count']==4 and saved['excluded_range_count']==2
    assert saved['quality_one_count']==1 and saved['quality_prefilter_applied'] is False
    assert saved['median']==97.5 and saved['p5']==96 and saved['p95']==99
    # Live/high-confidence timeline remains distinct from original nightly mean.
    assert len(store.analysis('a',start=start,end=start+1800)['spo2'])==1
    summary=store.state('a')['spo2_catalog']
    assert summary['sample_count']==4 and summary['latest_sleep']['average']==97.5
    assert summary['recent_native_outputs'][0]['value']==0
    assert summary['recent_native_outputs'][0]['timezone_raw']==60
    assert store.sleep('b')['periods']==[] and store.state('b')['spo2_catalog']['sample_count']==0
    assert HomePulseStore(store.base).state('a')['spo2_catalog']==summary


def test_background_backfill_does_not_redownload_and_old_runs_cannot_overwrite_new(tmp_path):
    store=HomePulseStore(tmp_path/'catalog');stamp=int(time.time())-60
    def save(owner,data):
        encoded=json.dumps(data,sort_keys=True)
        with store.connection(True) as db:
            return db.execute('INSERT INTO native_runs(owner_id,created_at,result,result_sha256) VALUES(?,?,?,?)',
                (owner,time.time(),encoded,hashlib.sha256(encoded.encode()).hexdigest())).lastrowid
    old=save('a',result(spo2_outputs=[sample(stamp,95,0)]))
    store.publish_processed('a',[],result(spo2_outputs=[sample(stamp,98,1)]))
    assert store.backfill_spo2_catalog(limit=1)==1
    assert store.state('a')['spo2_catalog']['recent_native_outputs'][0]['value']==98
    assert store.backfill_spo2_catalog()==0
    with store.connection() as db:
        assert db.execute('SELECT count(*) FROM history').fetchone()[0]==0
        assert db.execute('SELECT count(*) FROM spo2_catalog_runs WHERE run_id=?',(old,)).fetchone()[0]==1


def test_catalog_requires_successful_versioned_mapped_native_run(tmp_path):
    store=HomePulseStore(tmp_path/'catalog');row=sample(time.time()-60)
    for change in ({'version':[9,9,9]},{'process_true':False},{'mapping':None}):
        store.publish_processed('a',[],result(spo2_outputs=[row],**change))
    assert store.state('a')['spo2_catalog']['sample_count']==0
    for change in ({'quality_raw':float('inf')},{'average_raw':256},{'data_type':5},
                   {'timestamp_utc_ms_native':False},{'average_raw':97.1}):
        assert normalize({**row,**change}) is None


def test_empty_aggregate_never_becomes_zero_percentage(tmp_path):
    store=HomePulseStore(tmp_path/'catalog')
    store.publish_processed('a',[],result(native_period(),spo2_outputs=[]))
    aggregate=store.sleep('a')['periods'][0]['spo2']
    assert aggregate['average'] is None and aggregate['display_percentage'] is None
    assert store.state('a')['spo2_catalog']['latest_sleep'] is None


def test_emily_native_catalog_pagination_is_owner_scoped_and_keeps_quality(tmp_path):
    from app.services.homepulse.store import StoreError
    store=HomePulseStore(tmp_path/'catalog');start=int(time.time())-600
    store.manual_session('a','start',session_id='session-a')
    with store.connection(True) as db:
        db.execute("UPDATE sessions SET started_at=?,ended_at=?,status='ended' WHERE owner_id='a' AND id='session-a'",(start,start+300))
    store.publish_processed('a',[],result(spo2_outputs=[sample(start+60,96,0),sample(start+120,108,0)]))
    first=store.samples('a','session-a',metric='spo2_native',limit=1)
    assert first['total_points']==2 and first['has_more']
    assert first['points'][0]['quality']==0 and first['points'][0]['native_catalog'] is True
    second=store.samples('a','session-a',metric='spo2_native',limit=1,cursor=first['next_cursor'])
    assert second['points'][0]['v']==108 and not second['has_more']
    assert store.samples('a','session-a',metric='spo2')['total_points']==0
    with pytest.raises(StoreError):store.samples('b','session-a',metric='spo2_native')
    with pytest.raises(StoreError):store.samples('a','session-a',metric='spo2',cursor=first['next_cursor'])
