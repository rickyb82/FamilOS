"""Only reconcile matching sleep projections, preserving original native runs."""
import json
import time

import pytest

from app.services.homepulse.sleep import (
    GEN3_CONTRACT, NATIVE_ABI, coalesce_periods, normalize_bedtime, normalize_period,
)
from app.services.homepulse.store import HomePulseStore


def native(start):
    return dict(bedtime_start_utc_seconds=start, bedtime_end_utc_seconds=start+1800,
                bedtime_start_time_zone_minutes=60, bedtime_end_time_zone_minutes=60,
                time_in_bed_seconds=1800, native_version=[3,6,2], native_abi=NATIVE_ABI,
                summary2=dict(total_sleep_seconds=1500, wake_seconds=300, rem_seconds=300,
                              light_seconds=900, deep_seconds=300),
                phases_30_seconds='1234', phases_5_minutes='23')


def firmware(start):
    return dict(source='oura-ring-bedtime', source_tag=0x76, boot_id='a'*64,
                mapping_method='original-outer-utc-plus-same-epoch-100ms-delta',
                started_at_utc_ms=start*1000, ended_at_utc_ms=(start+1800)*1000,
                time_in_bed_seconds=1800)


def result(*rows, bedtime=()):
    return dict(status='ok',process_true=True,deinit_returned=True,version=[3,6,2],
                mapping={'complete':True},sleep_native_abi=NATIVE_ABI,
                sleep_capture_complete=True,sleep_outputs=list(rows),
                gen3_sleep={'contract':GEN3_CONTRACT,'periods':list(bedtime)})


@pytest.mark.parametrize('rich_first',[False,True])
@pytest.mark.parametrize('start_offset,end_offset',[(.1,.1),(-11.9,-.4)])
def test_write_reconciles_source_clock_and_phase_rounding_in_both_arrival_orders(tmp_path,rich_first,start_offset,end_offset):
    store=HomePulseStore(tmp_path/'private');start=int(time.time())-3600
    firmware_row=firmware(start+start_offset)
    firmware_row['ended_at_utc_ms']=(start+1800+end_offset)*1000
    firmware_row['time_in_bed_seconds']=1800+end_offset-start_offset
    full=result(native(start));empty=result(bedtime=[firmware_row])
    for output in ([full,empty] if rich_first else [empty,full]):
        store.publish_processed('a',[],output)
    periods=HomePulseStore(store.base).sleep('a')['periods']
    assert len(periods)==1 and periods[0]['started_at']==start
    assert periods[0]['total_sleep_seconds']==1500
    assert periods[0]['phases_30_seconds']=='1234'
    with store.connection() as db:
        assert db.execute('SELECT count(*) FROM sleep_periods').fetchone()[0]==1
        originals=[json.loads(row[0]) for row in db.execute('SELECT result FROM native_runs')]
        assert len(originals)==2 and full in originals and empty in originals


def test_read_repairs_legacy_duplicates_and_write_cleans_projection_only(tmp_path):
    store=HomePulseStore(tmp_path/'private');start=int(time.time())-3600
    store.publish_processed('a',[],result(native(start)))
    empty=normalize_bedtime('a',firmware(start+.1),time.time())
    # Emulate projections left by the previous implementation. The original
    # native run already exists and must survive projection reconciliation.
    with store.connection(True) as db:
        db.execute('INSERT INTO sleep_periods VALUES(?,?,?,?,?,?,?)',
                   ('a',empty['started_at'],empty['ended_at'],empty['id'],json.dumps(empty),'synthetic-existing-digest',time.time()))
        db.execute("INSERT INTO history(owner_id,ring_id,raw_hex,digest,tag,cursor,received_at) VALUES('a','synthetic','410401000000','synthetic-raw',65,1,?)",(time.time(),))
    assert len(store.sleep('a')['periods'])==1
    with store.connection() as db:
        assert db.execute('SELECT count(*) FROM sleep_periods').fetchone()[0]==2
    store.publish_processed('a',[],result(bedtime=[firmware(start+.1)]))
    with store.connection() as db:
        assert db.execute('SELECT count(*) FROM sleep_periods').fetchone()[0]==1
        assert db.execute('SELECT count(*) FROM history').fetchone()[0]==1
        assert db.execute('SELECT count(*) FROM native_runs').fetchone()[0]==2
    assert store.sleep('a')['periods'][0]['total_sleep_seconds']==1500


def test_distinct_nights_and_unmatched_empty_period_remain_owner_private(tmp_path):
    store=HomePulseStore(tmp_path/'private');start=int(time.time())-3600
    store.publish_processed('a',[],result(native(start)))
    store.publish_processed('a',[],result(bedtime=[firmware(start-86400)]))
    store.publish_processed('b',[],result(bedtime=[firmware(start+.1)]))
    a,b=store.sleep('a')['periods'],store.sleep('b')['periods']
    assert len(a)==2 and len(b)==1
    assert a[0]['total_sleep_seconds']==1500
    assert a[1]['total_sleep_seconds'] is None and b[0]['total_sleep_seconds'] is None
    with store.connection() as db:
        assert db.execute('SELECT count(*) FROM sleep_periods').fetchone()[0]==3


def test_duplicate_cleanup_rolls_back_with_failed_publication(tmp_path,monkeypatch):
    store=HomePulseStore(tmp_path/'private');start=int(time.time())-3600
    store.publish_processed('a',[],result(native(start)))
    empty=normalize_bedtime('a',firmware(start+.1),time.time())
    with store.connection(True) as db:
        db.execute('INSERT INTO sleep_periods VALUES(?,?,?,?,?,?,?)',
                   ('a',empty['started_at'],empty['ended_at'],empty['id'],json.dumps(empty),'synthetic-old',time.time()))
    def fail(*args,**kwargs):raise OSError('synthetic disk full')
    monkeypatch.setattr(store,'_point',fail)
    output=result(bedtime=[firmware(start+.1)])
    output['spo2_outputs']=[dict(data_type=6,average_raw=97,quality_raw=1,timestamp_utc_ms_native=start*1000)]
    with pytest.raises(OSError):store.publish_processed('a',[],output)
    with store.connection() as db:
        assert db.execute('SELECT count(*) FROM sleep_periods').fetchone()[0]==2
        assert db.execute('SELECT count(*) FROM native_runs').fetchone()[0]==1
    assert store.sleep('a')['periods'][0]['total_sleep_seconds']==1500


def test_both_endpoints_must_match_and_groups_cannot_drift_transitively():
    start=int(time.time())-3600
    rich=normalize_period('a',native(start),time.time())
    baseline=normalize_bedtime('a',firmware(start+.1),time.time())
    assert len(coalesce_periods([baseline,rich]))==1
    # Matching the start or calendar day alone is insufficient.
    for change in ({'started_at':start+30.1},{'ended_at':start+1830.1}):
        assert len(coalesce_periods([rich,{**baseline,**change}]))==2
    shifted=[rich,{**baseline,'started_at':start+20,'ended_at':start+1820},
             {**rich,'started_at':start+40,'ended_at':start+1840}]
    assert len(coalesce_periods(shifted))==2
    # Close endpoints alone are not enough to discard two unanalysed entries
    # from the same source, nor to widen the tolerance between two analyses.
    assert len(coalesce_periods([baseline,{**baseline,'started_at':start+.8,'ended_at':start+1800.8}]))==2
    assert len(coalesce_periods([rich,{**rich,'started_at':start+1.1,'ended_at':start+1801.1}]))==2
