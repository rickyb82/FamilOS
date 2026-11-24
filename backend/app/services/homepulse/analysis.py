"""Align acquired workout series using their explicit source timestamps.

This module has no device/file access and does not interpret arrival/download
time as a historical measurement timestamp. Native SpO2 and mapped temperature
timestamps are milliseconds; heart timestamps and session bounds are epoch
seconds. Missing/invalid points are omitted, never filled or shifted in time.
"""
import math
import time

RECOVERY_SECONDS = 600
HEART_GAP_SECONDS = 8  # Application freshness threshold, not sensor cadence.


def _finite(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _epoch(value):
    return _finite(value) and value >= 0


def _bounds(started_at, ended_at, now):
    if not _epoch(started_at) or not _epoch(now):
        raise ValueError('Inizio sessione e istante attuale devono essere epoch secondi validi')
    if ended_at is not None and (not _epoch(ended_at) or ended_at < started_at):
        raise ValueError('Fine sessione non valida o precedente all’inizio')
    if ended_at is None and now < started_at:
        raise ValueError('La sessione non può iniziare dopo l’istante attuale')
    end = now if ended_at is None else max(ended_at, min(now, ended_at + RECOVERY_SECONDS))
    return float(started_at), float(end)


def _rows(values):
    if values is None:
        return []
    if not isinstance(values, (list, tuple)):
        raise ValueError('Le serie devono essere liste di record')
    return values


def _dedup(points):
    # Distinct values at the same timestamp remain visible as a conflict;
    # neither replaces the other. Identical (timestamp, value) pairs collapse.
    unique = sorted(set(points), key=lambda point: (point[0], point[1]))
    return [{'t': timestamp, 'v': value} for timestamp, value in unique]


def _summary(points):
    values = [point['v'] for point in points]
    return {'count': len(values), 'min': min(values) if values else None,
            'max': max(values) if values else None,
            'average': sum(values) / len(values) if values else None}


def build_analysis(started_at, ended_at, heart_samples, spo2_outputs, temperatures,
                   *, now=None, demo=False,motion_records=None,activity_records=None):
    """Return aligned sparse series plus explicit workout/recovery bounds.

    Inputs:
      heart_samples: {timestamp: epoch_seconds, bpm, source, contact?}
      spo2_outputs: {data_type: 6, timestamp_utc_ms_native, average_raw, quality_raw}
      temperatures: {utc_ms, field_1_celsius, record_sha256?}

    Output series are heart_rate/spo2/temperature lists of {t: epoch_seconds,v}.
    The window includes up to ten minutes after ended_at, capped at now; while
    running it ends at now. Native quality/range filters are conservative
    application gates, not clinical validation. A missing source timestamp is
    never replaced by received_at, an elapsed value, or the download time.
    """
    if type(demo) is not bool:
        raise ValueError('La modalità demo deve essere booleana')
    current = time.time() if now is None else now
    start, end = _bounds(started_at, ended_at, current)
    heart_samples, spo2_outputs, temperatures = map(_rows, (heart_samples, spo2_outputs, temperatures))
    points = {'heart_rate': [], 'spo2': [], 'temperature': []}
    motion,steps,quality_events = [],[],[]
    excluded = {'heart_rate': 0, 'spo2': 0, 'temperature': 0}
    invalid_heart_times = []
    heart_sources = set()

    for row in heart_samples:
        if not isinstance(row, dict):
            excluded['heart_rate'] += 1
            continue
        timestamp, value, source = row.get('timestamp'), row.get('bpm'), row.get('source')
        if not _epoch(timestamp) or not start <= timestamp <= end:
            excluded['heart_rate'] += 1
            continue
        if not isinstance(source, str) or not source.strip() or (source.strip().casefold() == 'demo') != demo:
            excluded['heart_rate'] += 1
            continue
        contact = row.get('contact')
        if (contact is not None and type(contact) is not bool) or contact is False or not _finite(value) or not 20 <= value <= 250:
            excluded['heart_rate'] += 1
            invalid_heart_times.append(float(timestamp))
            continue
        points['heart_rate'].append((float(timestamp), value))
        if row.get('warnings'):
            quality_events.append({'t':float(timestamp),'flags_raw':row.get('flags_raw'),'warnings':row['warnings']})
        heart_sources.add(source.strip())

    if demo:
        # The demo heart series must never inherit a user's real body metrics.
        excluded['spo2'] = len(spo2_outputs)
        excluded['temperature'] = len(temperatures)
    else:
        for row in spo2_outputs:
            if not isinstance(row, dict):
                excluded['spo2'] += 1
                continue
            native_ms = row.get('timestamp_utc_ms_native')
            average, quality = row.get('average_raw'), row.get('quality_raw')
            if (type(row.get('data_type')) is not int or row['data_type'] != 6 or
                    not _epoch(native_ms) or not _finite(average) or not 85 <= average <= 100 or
                    not _finite(quality) or quality != 1.0):
                excluded['spo2'] += 1
                continue
            timestamp = native_ms / 1000
            if not start <= timestamp <= end:
                excluded['spo2'] += 1
                continue
            points['spo2'].append((timestamp, average))
        for row in temperatures:
            if not isinstance(row, dict):
                excluded['temperature'] += 1
                continue
            utc_ms, value = row.get('utc_ms'), row.get('field_1_celsius')
            if not _epoch(utc_ms) or not _finite(value) or value <= 0:
                excluded['temperature'] += 1
                continue
            timestamp = utc_ms / 1000
            if not start <= timestamp <= end:
                excluded['temperature'] += 1
                continue
            points['temperature'].append((timestamp, value))

    series = {key: _dedup(values) for key, values in points.items()}
    gaps = []
    invalid_heart_times.sort()
    invalid_index = 0
    for previous, following in zip(series['heart_rate'], series['heart_rate'][1:]):
        while invalid_index < len(invalid_heart_times) and invalid_heart_times[invalid_index] <= previous['t']:
            invalid_index += 1
        has_invalid = (invalid_index < len(invalid_heart_times) and
                       invalid_heart_times[invalid_index] < following['t'])
        if following['t'] - previous['t'] > HEART_GAP_SECONDS or has_invalid:
            gaps.append({'from': previous['t'], 'to': following['t'],
                         'reason': 'invalid_sample' if has_invalid else 'no_recent_sample'})

    heart = series['heart_rate']
    workout_heart = heart if ended_at is None else [point for point in heart if point['t'] <= ended_at]
    recovery_heart = [] if ended_at is None else [point for point in heart if point['t'] > ended_at]
    conflicts = {}
    for key, values in series.items():
        conflicts[key] = len({point['t'] for point in values}) != len(values)
    if not demo:
        for row in _rows(motion_records):
            stamp,value = row.get('utc_ms'),row.get('motion_seconds')
            if _epoch(stamp) and _finite(value) and start <= stamp/1000 <= end:
                motion.append((stamp/1000,value))
        seen_activity=set()
        for row in _rows(activity_records):
            stamp,value = row.get('utc_ms'),row.get('step_count')
            if _epoch(stamp) and _finite(value) and start <= stamp/1000 <= end:
                identity=row.get('record_sha256') or (stamp,value)
                if identity in seen_activity:continue
                seen_activity.add(identity)
                steps.append((stamp/1000,value))
    return {
        'motion':_dedup(motion),'step_increments':_dedup(steps),'quality_events':quality_events,
        'steps_in_records':sum(value for _,value in steps),
        **series,
        'window_start': start,
        'window_end': end,
        'workout_start': start,
        'workout_end': None if ended_at is None else float(ended_at),
        'recovery_window_seconds': RECOVERY_SECONDS,
        'heart_rate_gap_seconds': HEART_GAP_SECONDS,
        'gaps': {'heart_rate': gaps},
        'summary': {'heart_rate': _summary(heart),
                    'heart_rate_workout': _summary(workout_heart),
                    'heart_rate_recovery': _summary(recovery_heart)},
        'excluded': excluded,
        'source': 'demo' if demo else 'local-acquisition',
        'demo': demo,
        'source_flags': {
            'heart_rate_sources': sorted(heart_sources),
            'heart_rate_time_basis': 'host_receive_epoch_seconds',
            'spo2_time_basis': 'native_utc_milliseconds',
            'temperature_time_basis': 'mapped_ring_utc_milliseconds',
            'download_time_used_as_measurement_time': False,
            'interpolated': False,
            'sparse_series_valid': True,
            'conflicting_values_at_same_timestamp': conflicts,
            'clinically_validated': False,
        },
        'limitations': [
            'Il battito usa l’istante di ricezione sul computer; la latenza del sensore non viene stimata.',
            'SpO₂: solo output nativi data_type 6 con qualità 1.0 e valore tra 85 e 100; il filtro non è una validazione clinica.',
            'Temperatura: solo campo 1 positivo con UTC disponibile; non rappresenta la temperatura interna del corpo.',
            'Nessun campione viene interpolato o sostituito durante i vuoti.',
        ] + (['La demo esclude sempre i dati reali SpO₂ e temperatura.'] if demo else []),
    }
