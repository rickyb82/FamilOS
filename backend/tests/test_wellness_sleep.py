"""Synthetic native output, durable SQLite and owner-authenticated sleep reads."""
from copy import deepcopy
import json
import math
import time

import pytest
from sqlalchemy import select

from app.api import wellness
from app.db import SessionLocal
from app.models import User
from app.services.homepulse.sleep import NATIVE_ABI, normalize_period
from app.services.homepulse.store import HomePulseStore, StoreError
from conftest import make_client


@pytest.fixture(autouse=True)
def installed_module(monkeypatch, tmp_path):
    from app.services.homepulse import module
    monkeypatch.setattr(module, 'enabled', lambda: True)
    archive = HomePulseStore(tmp_path / 'sleep-private', wellness.encrypt, wellness.decrypt)
    monkeypatch.setattr(wellness, 'store', lambda: archive)
    return archive


def native_period(start=None, **overrides):
    start = int(time.time()) - 3600 if start is None else start
    return {'timestamp_utc_seconds': start, 'sleep_period_id': 1,
            'bedtime_start_utc_seconds': start, 'bedtime_end_utc_seconds': start + 1800,
            'bedtime_start_time_zone_minutes': 60, 'bedtime_end_time_zone_minutes': 60,
            'time_in_bed_seconds': 1800, 'phases_30_seconds': '1234', 'phases_5_minutes': '23',
            'summary2': {'wake_seconds': 120, 'rem_seconds': 300, 'light_seconds': 900,
                         'deep_seconds': 300, 'latency_seconds': 60, 'total_sleep_seconds': 1500,
                         'efficiency_raw': 83, 'score_raw': 255, 'wake_up_count_raw': 2},
            'native_version': [3, 6, 2], 'native_abi': NATIVE_ABI, **overrides}


def result(*rows, **overrides):
    return {'status': 'ok', 'process_true': True, 'deinit_returned': True,
            'version': [3, 6, 2], 'sleep_native_abi': NATIVE_ABI,
            'mapping': {'complete': True}, 'sleep_capture_complete': True,
            'sleep_outputs': list(rows), **overrides}


def test_decoder_upgrade_replays_existing_history_once_without_redownload(installed_module):
    archive = installed_module
    archive.append('a', 'synthetic-phone', 'synthetic-batch', [{
        'event_id': 'synthetic-event', 'sequence': 1, 'captured_at': time.time(),
        'kind': 'ring_history', 'payload': {'raw_hex': '410401000000'}}])
    records = archive.historical_records('a')
    assert records
    # Previous production worker already acknowledged this entire history.
    archive.publish_processed('a', records, {'status': 'ok', 'process_true': True,
                                            'deinit_returned': True})
    assert archive.pending_owners() == ['a']
    # A valid empty native sleep capture is still a completed decoder attempt.
    archive.publish_processed('a', records, result())
    assert archive.pending_owners() == []
    assert HomePulseStore(archive.base).pending_owners() == []
    assert archive.sleep('a')['periods'] == []


def test_durable_owner_scoped_dedup_and_native_raw_preserved(installed_module):
    archive = installed_module
    row = native_period()
    first = result(row)
    archive.publish_processed('owner-a', [], first)
    archive.publish_processed('owner-a', [], first)
    newer = deepcopy(row)
    newer['sleep_period_id'] = 99  # Reinitialization may change this native key.
    newer['summary2']['total_sleep_seconds'] = 1440
    newer['phases_30_seconds'] = '4444'
    archive.publish_processed('owner-a', [], result(newer))
    reopened = HomePulseStore(archive.base)
    periods = reopened.sleep('owner-a')['periods']
    assert len(periods) == 1
    assert periods[0]['id'] == normalize_period('owner-a', row, time.time())['id']
    assert periods[0]['total_sleep_seconds'] == 1440
    assert periods[0]['phases_30_seconds'] == '4444'
    assert reopened.sleep('owner-b')['periods'] == []
    archive.publish_processed('owner-b', [], first)
    assert reopened.sleep('owner-b')['periods'][0]['id'] != periods[0]['id']
    assert 'score_raw' not in json.dumps(periods)
    with reopened.connection() as db:
        originals = [json.loads(r[0]) for r in db.execute('SELECT result FROM native_runs WHERE owner_id=?', ('owner-a',))]
        assert len(originals) == 2
        assert originals[0]['sleep_outputs'][0]['summary2']['score_raw'] == 255
        assert db.execute('SELECT count(*) FROM sleep_periods WHERE owner_id=?', ('owner-a',)).fetchone()[0] == 1


def test_empty_failed_and_unverified_replays_keep_prior_period(installed_module):
    archive = installed_module
    good = native_period()
    archive.publish_processed('a', [], result(good))
    saved = archive.sleep('a')['periods']
    changed = native_period(good['bedtime_start_utc_seconds'], phases_30_seconds='4')
    for overrides in ({'process_true': False}, {'deinit_returned': False}, {'mapping': {'complete': False}},
                      {'status': 'worker_unavailable'}, {'sleep_capture_complete': False},
                      {'sleep_callback_errors': ['invalid']}, {'version': [3, 6, 1]},
                      {'sleep_native_abi': 'unknown'}):
        archive.publish_processed('a', [], result(changed, **overrides))
        assert archive.sleep('a')['periods'] == saved
    archive.publish_processed('a', [], result())
    archive.publish_processed('a', [], {'status': 'ok', 'process_true': True, 'deinit_returned': True})
    assert archive.sleep('a')['periods'] == saved
    # Partial newer callbacks cannot clear known summary/phases from a good run.
    sparse = {**good, 'summary2': None, 'phases_30_seconds': None, 'phases_5_minutes': ''}
    archive.publish_processed('a', [], result(sparse))
    assert archive.sleep('a')['periods'] == saved


def test_invalid_rows_excluded_but_originals_and_good_rows_survive(installed_module):
    archive = installed_module
    good = native_period()
    invalid = [native_period(good['bedtime_start_utc_seconds'] - 60, **changes) for changes in
               ({'time_in_bed_seconds': 0}, {'time_in_bed_seconds': 57601},
                {'phases_30_seconds': '1204'}, {'phases_30_seconds': '1' * 1921},
                {'phases_5_minutes': '2' * 193}, {'native_abi': 'unknown'},
                {'bedtime_end_utc_seconds': good['bedtime_start_utc_seconds'] - 100},
                {'summary2': {'total_sleep_seconds': -1}},
                {'summary2': {'total_sleep_seconds': 2000}},
                {'summary2': {'efficiency_raw': 101}},
                {'summary2': {'wake_up_count_raw': -1}},
                {'summary2': {'deep_seconds': True}})]
    raw = result(*invalid, good)
    archive.publish_processed('a', [], raw)
    assert len(archive.sleep('a')['periods']) == 1
    with archive.connection() as db:
        assert json.loads(db.execute('SELECT result FROM native_runs').fetchone()[0]) == raw
    for changes in ({'bedtime_start_utc_seconds': math.nan}, {'bedtime_end_utc_seconds': math.inf},
                    {'time_in_bed_seconds': math.nan}, {'summary2': {'efficiency_raw': math.inf}}):
        with pytest.raises(ValueError):
            normalize_period('a', {**good, **changes}, time.time())


def test_no_inferred_stages_nullable_summary_and_no_forced_epoch_alignment(installed_module):
    archive = installed_module
    row = native_period(summary2=None, phases_30_seconds=None, phases_5_minutes=None)
    archive.publish_processed('a', [], result(row))
    period = archive.sleep('a')['periods'][0]
    assert all(period[key] is None for key in ('stage_seconds', 'total_sleep_seconds', 'latency_seconds',
                                              'efficiency', 'phases_30_seconds', 'phases_5_minutes'))
    # Literal4 epochs =120seconds, whereas native bedspan is1800: preserve both.
    archive.publish_processed('a', [], result(native_period(row['bedtime_start_utc_seconds'])))
    period = archive.sleep('a')['periods'][0]
    assert period['phases_30_seconds'] == '1234'
    assert period['ended_at'] - period['started_at'] == 1800
    assert period['stage_seconds'] == {'awake': 120, 'rem': 300, 'light': 900, 'deep': 300}
    assert 'epochs' not in period


def test_window_bounds_no_raw_motion_inference_and_durable_transaction(installed_module, monkeypatch):
    archive = installed_module
    now = int(time.time())
    archive.publish_processed('a', [], result(native_period(now - 2 * 86400), native_period(now - 20 * 86400)))
    assert len(archive.sleep('a', days=14)['periods']) == 1
    assert len(archive.sleep('a', days=90)['periods']) == 2
    for days in (0, 91, True, 1.5):
        with pytest.raises(StoreError):
            archive.sleep('a', days)
    before = archive.sleep('a', 90)['periods']
    original = archive._point
    def fail(*args, **kwargs):
        raise OSError('synthetic disk full')
    monkeypatch.setattr(archive, '_point', fail)
    failing = result(native_period(now - 86400), spo2_outputs=[{'data_type': 6, 'average_raw': 97,
                         'quality_raw': 1, 'timestamp_utc_ms_native': now * 1000}])
    with pytest.raises(OSError):
        archive.publish_processed('a', [], failing)
    assert archive.sleep('a', 90)['periods'] == before
    with archive.connection() as db:
        assert db.execute('SELECT count(*) FROM native_runs').fetchone()[0] == 1
    monkeypatch.setattr(archive, '_point', original)
    archive.append('empty', 'phone', 'motion', [{'event_id': 'motion1', 'sequence': 1, 'captured_at': now,
                         'kind': 'ring_history', 'payload': {'raw_hex': '470a010000000f0102038000'}}])
    assert archive.sleep('empty')['periods'] == []


async def owner_id(username):
    async with SessionLocal() as db:
        return (await db.execute(select(User.id).where(User.username == username))).scalar_one()


@pytest.mark.asyncio
async def test_sleep_auth_owner_separation_days_and_device_revocation(admin, anna, kid, bob, installed_module):
    archive = installed_module
    admin_id = await owner_id('admin')
    archive.publish_processed(admin_id, [], result(native_period()))
    response = await admin.get('/api/wellness/v1/sleep')
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {'server_time', 'window_start', 'window_end', 'periods', 'processing_status', 'last_processed_at', 'processing_error'}
    assert body['window_end'] - body['window_start'] == 14 * 86400
    assert len(body['periods']) == 1
    assert set(body['periods'][0]) == {'id', 'started_at', 'ended_at', 'time_zone_minutes', 'end_time_zone_minutes', 'time_in_bed_seconds', 'spo2', 'respiratory_rate',
              'total_sleep_seconds', 'stage_seconds', 'latency_seconds', 'efficiency',
              'phases_30_seconds', 'phases_5_minutes', 'source', 'native_version'}
    empty = (await anna.get('/api/wellness/v1/sleep', params={'owner_id': admin_id})).json()
    assert empty['periods'] == [] and empty['last_processed_at'] is None
    assert (await kid.get('/api/wellness/v1/sleep')).status_code == 403
    assert (await bob.get('/api/wellness/v1/sleep')).status_code == 403
    for days in ('0', '91', '1.5'):
        assert (await admin.get('/api/wellness/v1/sleep', params={'days': days})).status_code == 422
    assert (await admin.get('/api/wellness/v1/mobile/sleep')).status_code == 401
    async with make_client() as anonymous:
        assert (await anonymous.get('/api/wellness/v1/sleep')).status_code == 401
        assert (await anonymous.get('/api/wellness/v1/mobile/sleep')).status_code == 401
    pair = archive.create_pairing(admin_id)
    device = archive.enroll(pair['pairing_code'], 'Synthetic iPhone')
    other = archive.enroll(archive.create_pairing(await owner_id('anna'))['pairing_code'], 'Other synthetic iPhone')
    async with make_client(token=other['access_token']) as other_mobile:
        private = await other_mobile.get('/api/wellness/v1/mobile/sleep', params={'owner_id': admin_id})
        assert private.status_code == 200 and private.json()['periods'] == []
    async with make_client(token=device['access_token']) as mobile:
        mobile_response = await mobile.get('/api/wellness/v1/mobile/sleep')
        assert mobile_response.status_code == 200
        assert mobile_response.json()['periods'] == body['periods']
        assert (await mobile.get('/api/wellness/v1/sleep')).status_code == 401
        assert (await mobile.get('/api/wellness/v1/mobile/sleep?days=91')).status_code == 422
        archive.revoke(admin_id, device['device_id'])
        assert (await mobile.get('/api/wellness/v1/mobile/sleep')).status_code == 401


@pytest.mark.asyncio
async def test_sleep_module_disabled_and_child_or_disabled_app_device(admin, kid, bob, installed_module, monkeypatch):
    archive = installed_module
    from app.services.homepulse import module
    for name in ('kid', 'bob'):
        device = archive.enroll(archive.create_pairing(await owner_id(name))['pairing_code'], 'synthetic')
        async with make_client(token=device['access_token']) as mobile:
            assert (await mobile.get('/api/wellness/v1/mobile/sleep')).status_code == 403
    admin_id = await owner_id('admin')
    device = archive.enroll(archive.create_pairing(admin_id)['pairing_code'], 'synthetic')
    monkeypatch.setattr(module, 'enabled', lambda: False)
    assert (await admin.get('/api/wellness/v1/sleep')).status_code == 404
    async with make_client(token=device['access_token']) as mobile:
        assert (await mobile.get('/api/wellness/v1/mobile/sleep')).status_code == 403
