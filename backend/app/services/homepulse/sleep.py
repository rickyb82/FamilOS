"""Validate native sleep output without inventing a score or epoch alignment."""
from __future__ import annotations

import hashlib
import math

NATIVE_VERSION = [3, 6, 2]
NATIVE_ABI = 'ecore-3.6.2-linux-x86_64-sleep-output-200-v1'
MAX_PERIOD_SECONDS = 57600
MAX_PERIODS_PER_RUN = 1024


def _number(value, low, high):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError('Invalid native sleep number')
    return value


def _optional_number(value, maximum):
    return None if value is None else _number(value, 0, maximum)


def _phases(value, maximum):
    if value is None:
        return None
    # '0' in packed AppEvents is not a confirmed callback string symbol.
    # Preserve the literal native string, including empty strings/edge epochs.
    if not isinstance(value, str) or len(value) > maximum or any(c not in '1234' for c in value):
        raise ValueError('Invalid native sleep phases')
    return value


def normalize_period(owner, row, now):
    if not isinstance(row, dict) or row.get('native_version') != NATIVE_VERSION or row.get('native_abi') != NATIVE_ABI:
        raise ValueError('Unverified native sleep ABI')
    start = _number(row.get('bedtime_start_utc_seconds'), 1, now + 300)
    end = _number(row.get('bedtime_end_utc_seconds'), start, now + 300)
    if end <= start or end - start > MAX_PERIOD_SECONDS or start != int(start) or end != int(end):
        raise ValueError('Invalid native sleep interval')
    duration = _number(row.get('time_in_bed_seconds'), 1, MAX_PERIOD_SECONDS)
    zone = _number(row.get('bedtime_start_time_zone_minutes'), -1440, 1440)
    if zone != int(zone):
        raise ValueError('Invalid native sleep timezone')
    end_zone = _number(row.get('bedtime_end_time_zone_minutes', zone), -1440, 1440)
    if end_zone != int(end_zone):
        raise ValueError('Invalid native sleep timezone')
    summary = row.get('summary2')
    if summary is not None and not isinstance(summary, dict):
        raise ValueError('Invalid native sleep summary')
    summary = summary or {}
    stages = {name: _optional_number(summary.get(native), duration)
              for name, native in (('awake', 'wake_seconds'), ('rem', 'rem_seconds'),
                                   ('light', 'light_seconds'), ('deep', 'deep_seconds'))}
    if 'wake_up_count_raw' in summary:
        count = _optional_number(summary['wake_up_count_raw'], 1920)
        if count is not None and count != int(count):
            raise ValueError('Invalid native wake count')
    identity = hashlib.sha256((str(owner) + '\0' + str(int(start))).encode()).hexdigest()[:32]
    return {'id': 'sleep-' + identity, 'started_at': start, 'ended_at': end,
            'time_zone_minutes': int(zone), 'end_time_zone_minutes': int(end_zone), 'time_in_bed_seconds': duration,
            'total_sleep_seconds': _optional_number(summary.get('total_sleep_seconds'), duration),
            'stage_seconds': stages if all(value is not None for value in stages.values()) else None,
            'latency_seconds': _optional_number(summary.get('latency_seconds'), duration),
            'efficiency': _optional_number(summary.get('efficiency_raw'), 100),
            'phases_30_seconds': _phases(row.get('phases_30_seconds'), 1920),
            'phases_5_minutes': _phases(row.get('phases_5_minutes'), 192),
            'source': 'oura-ecore', 'native_version': list(NATIVE_VERSION)}


def merge_period(previous, current):
    """A sparse full-history replay cannot erase previously captured details."""
    if current.get("source")=="oura-ring-bedtime" and previous.get("source")=="oura-ecore":
        return previous
    merged = dict(current)
    for key in ('total_sleep_seconds', 'stage_seconds', 'latency_seconds', 'efficiency',
                'phases_30_seconds', 'phases_5_minutes'):
        if current[key] is None or current[key] == '':
            merged[key] = previous.get(key)
    # If a changed native interval cannot accommodate retained old summaries,
    # keep the previous complete projection. Both runs remain in native_runs.
    durations = [merged['total_sleep_seconds'], merged['latency_seconds']]
    durations += list((merged['stage_seconds'] or {}).values())
    if any(value is not None and value > merged['time_in_bed_seconds'] for value in durations):
        return previous
    return merged


def verified_run(result):
    return (result.get('status') == 'ok' and result.get('process_true') is True
            and result.get('deinit_returned') is True
            and isinstance(result.get('mapping'), dict) and result['mapping'].get('complete') is True
            and result.get('version') == NATIVE_VERSION and result.get('sleep_native_abi') == NATIVE_ABI
            and result.get('sleep_capture_complete') is True and not result.get('sleep_callback_errors'))


GEN3_CONTRACT = 'gen3-bedtime76-native-clock-v1'


def normalize_bedtime(owner, row, now):
    """A firmware bedtime window cannot supply a total or any sleep phase."""
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo
    if (row.get('source') != 'oura-ring-bedtime' or row.get('source_tag') != 0x76
            or row.get('mapping_method') != 'original-outer-utc-plus-same-epoch-100ms-delta'
            or not isinstance(row.get('boot_id'), str) or len(row['boot_id']) != 64):
        raise ValueError('Unverified firmware bedtime source')
    start = _number(row.get('started_at_utc_ms'), 1, (now+300)*1000) / 1000
    end = _number(row.get('ended_at_utc_ms'), 1, (now+300)*1000) / 1000
    if not 0 < end-start <= MAX_PERIOD_SECONDS:
        raise ValueError('Invalid firmware bedtime interval')
    duration = _number(row.get('time_in_bed_seconds'), 1, MAX_PERIOD_SECONDS)
    if abs(duration-(end-start)) > .001:
        raise ValueError('Firmware bedtime duration mismatch')
    zone = ZoneInfo('Europe/Rome')
    offsets = [int(datetime.fromtimestamp(t, timezone.utc).astimezone(zone).utcoffset().total_seconds()/60) for t in (start, end)]
    identity = hashlib.sha256((str(owner) + '\0' + str(start)).encode()).hexdigest()[:32]
    return {'id': 'sleep-' + identity, 'started_at': start, 'ended_at': end,
            'time_zone_minutes': offsets[0], 'end_time_zone_minutes': offsets[1],
            'display_time_zone': 'Europe/Rome', 'timezone_provenance': 'explicit-display-zone',
            'time_in_bed_seconds': duration, 'total_sleep_seconds': None,
            'stage_seconds': None, 'latency_seconds': None, 'efficiency': None,
            'phases_30_seconds': None, 'phases_5_minutes': None,
            'source': 'oura-ring-bedtime', 'source_tag': 0x76, 'native_version': list(NATIVE_VERSION)}
