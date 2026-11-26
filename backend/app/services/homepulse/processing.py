"""Bounded chronological native jobs, retaining a complete sleep overlap.

Ring ticks select windows only. Published UTC always comes from the verified
native mapper. No sample truncation or synthetic clock anchors are permitted.
"""
from bisect import bisect_left
from collections import defaultdict
import hashlib
import json

CONTRACT = 'ecore-gen3-physiology-overlap18h-v2'
SENSOR_MAPPING_ABI = 'native-mapped-sensors-47-50-62-6a-v2'
JOB_RECORDS = 60000
WORKER_RECORD_LIMIT = 65536
OVERLAP_TICKS = 18 * 3600 * 10


class PlanningError(ValueError):
    pass


def sensor(row):
    raw = bytes.fromhex(row['raw_hex'])
    return 6 <= len(raw) <= 20 and raw[0] not in (0x5c, 0xfd, 0xfe)


def canonical(records):
    return {'schema': 'standard-ring-events-raw-hex-v1',
            'records': [bytes.fromhex(row['raw_hex'])[:bytes.fromhex(row['raw_hex'])[1]+2].hex() for row in records]}


def input_hash(records):
    return hashlib.sha256(json.dumps(canonical(records), separators=(',', ':')).encode()).hexdigest()


def plan(records, budget=JOB_RECORDS):
    if type(budget) is not int or not 4 <= budget <= WORKER_RECORD_LIMIT-2:
        raise PlanningError('Limite blocco non valido')
    rings = defaultdict(list)
    for row in records:
        if sensor(row):
            rings[row.get('ring_id', 'default')].append(row)
    jobs, replay = [], {}
    for ring, rows in rings.items():
        if any(type(row.get('cursor')) is not int or row['cursor'] < 0 for row in rows):
            raise PlanningError('Record senza posizione temporale: storico conservato')
        rows.sort(key=lambda row: (row['cursor'], row.get('id', 0)))
        ticks = [row['cursor'] for row in rows]
        start = 0
        while start < len(rows):
            stop = min(start + budget, len(rows))
            prefix = []
            for tag in (0x41, 0x42):
                previous = next((i for i in range(start-1, -1, -1) if rows[i]['tag'] == tag), None)
                if previous is not None:
                    prefix.append(previous)
            selected = [rows[i] for i in sorted(set(prefix))] + rows[start:stop]
            if len(selected) > WORKER_RECORD_LIMIT:
                raise PlanningError('Blocco oltre il limite del motore; nessun record eliminato')
            jobs.append(selected)
            if stop == len(rows):
                break
            following = bisect_left(ticks, ticks[stop-1] - OVERLAP_TICKS)
            if following <= start:
                raise PlanningError('Densità oltre il limite: impossibile conservare 18 ore di contesto in un blocco. Record conservati')
            start = following
        replay[ring] = max(0, ticks[-1] - OVERLAP_TICKS)
    return jobs, replay


def http_failure(status, body=None):
    code = body.get('status') if isinstance(body, dict) else None
    known = {'busy', 'invalid_input', 'input_limit', 'timeout', 'not_configured',
             'native_job_failed', 'worker_error', 'output_limit', 'result_mismatch', 'unauthorized'}
    code = code if code in known else 'http_error'
    return {'status': 'worker_' + code, 'process_true': False,
            'error': f'Motore storico: HTTP {status} ({code}); record conservati', 'http_status': status}
