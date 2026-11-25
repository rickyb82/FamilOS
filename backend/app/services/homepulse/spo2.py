"""Native averaged SpO2 catalog; nightly aggregation follows the inspected APK.

Raw optical/event bytes are never percentages here. Quality is stored verbatim;
85..100 is the original daily aggregate range, not a quality replacement.
"""
import math

POLICY = 'native-type6-original-range-v1'


def normalize(row):
    if not isinstance(row, dict) or row.get('data_type') != 6:
        return None
    stamp, value, quality = (row.get(key) for key in
        ('timestamp_utc_ms_native', 'average_raw', 'quality_raw'))
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in (stamp, value, quality)):
        return None
    # ABI uint8 average, UTC milliseconds, float quality. Preserve even zero
    # and out-of-display-range outputs; the original run retains any bad ABI.
    if stamp <= 0 or stamp != int(stamp) or not 0 <= value <= 255 or value != int(value):
        return None
    return {'utc_ms': int(stamp), 'value': int(value), 'quality': quality,
            'timezone_raw': row.get('timezone_raw') if type(row.get('timezone_raw')) is int else None}


def capture(db, owner, run_id, result):
    if not (result.get('status') == 'ok' and result.get('process_true') is True
            and result.get('deinit_returned') is True and result.get('version') == [3, 6, 2]
            and isinstance(result.get('mapping'), dict) and result['mapping'].get('complete') is True):
        return
    outputs=result.get('spo2_outputs', [])
    if not isinstance(outputs, list):return
    for raw in outputs:
        row = normalize(raw)
        if row is None:
            continue
        db.execute('INSERT INTO spo2_catalog(owner_id,utc_ms,value,quality,timezone_raw,native_run_id)\n            VALUES(?,?,?,?,?,?) ON CONFLICT(owner_id,utc_ms) DO UPDATE SET\n            value=excluded.value,quality=excluded.quality,timezone_raw=excluded.timezone_raw,native_run_id=excluded.native_run_id\n            WHERE excluded.native_run_id>=spo2_catalog.native_run_id',
            (owner, row['utc_ms'], row['value'], row['quality'], row['timezone_raw'], run_id))


def aggregate(db, owner, start, end):
    rows = db.execute('SELECT utc_ms,value,quality FROM spo2_catalog WHERE owner_id=? AND utc_ms>=? AND utc_ms<=? ORDER BY utc_ms',
                      (owner, start * 1000, end * 1000)).fetchall()
    values = sorted(row['value'] for row in rows if 85 <= row['value'] <= 100)
    # APK daily builder uses an unweighted arithmetic mean with no quality
    # prefilter. BDI's quality==1 rule is a different consumer.
    average = sum(values) / len(values) if values else None
    return {'average': average, 'display_percentage': math.floor(average + .5) if average is not None else None,
            'minimum': values[0] if values else None, 'maximum': values[-1] if values else None,
            'median': ((values[(len(values)-1)//2]+values[len(values)//2])/2) if values else None,
            'p5': values[min(math.floor(.05*len(values)),len(values)-1)] if values else None,
            'p95': values[min(math.floor(.95*len(values)),len(values)-1)] if values else None,
            'sample_count': len(values), 'catalog_count': len(rows),
            'quality_one_count': sum(row['quality'] == 1 for row in rows),
            'excluded_range_count': len(rows) - len(values),
            'started_at': start, 'ended_at': end, 'source': 'oura-ecore',
            'aggregation_policy': POLICY, 'bias_correction_applied': False,
            'quality_prefilter_applied': False, 'live': False}
