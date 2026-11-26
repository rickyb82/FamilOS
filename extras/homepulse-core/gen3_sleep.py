"""Gen3 firmware bedtime windows, not sleep stages or total time asleep.

0x76 standard payload contains start/end in the header's100ms ring epoch.
UTC comes only from the pinned native mapper. Overlap windows may omit a
period until the complete same-boot interval is present in another job.
"""
from bisect import bisect_left
from collections import defaultdict, deque
import hashlib

CONTRACT = 'gen3-bedtime76-native-clock-v1'
MAX_DURATION_TICKS = 16 * 3600 * 10


def extract(frames, mapped, *, verified):
    report = {'contract': CONTRACT, 'periods': [], 'candidates': 0, 'rejected': {}}
    def reject(reason):
        report['rejected'][reason] = report['rejected'].get(reason, 0) + 1
    if not verified:
        return report
    lookup = defaultdict(deque)
    for raw, utc in mapped:
        lookup[raw].append(utc)
    segment = []
    boot = None
    for raw in frames:
        if not lookup[raw]:
            raise ValueError('Gen3 mapping identity mismatch')
        utc = lookup[raw].popleft()
        tick = int.from_bytes(raw[2:6], 'little')
        if raw[0] == 0x41:
            segment = []
            boot = hashlib.sha256(raw[:raw[1]+2]).hexdigest()
            continue
        if raw[0] == 0x42:
            # TimeSync is evidence, but an old prefix is not sensor coverage.
            continue
        if segment and tick < segment[-1][0]:
            segment = []
            boot = None
        segment.append((tick, utc - tick * 100))
        if raw[0] != 0x76:
            continue
        report['candidates'] += 1
        if raw[1] != 12 or len(raw) != 20 or any(raw[14:]):
            reject('unsupported_frame'); continue
        start = int.from_bytes(raw[6:10], 'little')
        end = int.from_bytes(raw[10:14], 'little')
        if not 0 < start < end <= tick or end-start > MAX_DURATION_TICKS:
            reject('incomplete_or_invalid_period'); continue
        if not boot or not segment or segment[0][0] > start:
            reject('incomplete_same_boot_coverage'); continue
        first = max(0, bisect_left(segment, (start, -float('inf'))) - 1)
        offsets = [item[1] for item in segment[first:]]
        # One timestamp quantum permits native integer rounding only. A clock
        # step is never repaired by guessing an offset or creating TimeSync.
        if max(offsets)-min(offsets) > 100:
            reject('clock_discontinuity'); continue
        start_ms = utc + 100 * (start-tick)
        end_ms = utc + 100 * (end-tick)
        if not 0 < start_ms < end_ms <= utc:
            reject('invalid_native_utc'); continue
        report['periods'].append({
            'source': 'oura-ring-bedtime', 'source_tag': 0x76,
            'record_sha256': hashlib.sha256(raw[:14]).hexdigest(),
            'started_at_utc_ms': start_ms, 'ended_at_utc_ms': end_ms,
            'time_in_bed_seconds': (end-start)/10,
            'boot_id': boot,
            'mapping_method': 'original-outer-utc-plus-same-epoch-100ms-delta',
            'total_sleep_seconds': None, 'stage_seconds': None,
            'phases_30_seconds': None, 'phases_5_minutes': None})
    return report
