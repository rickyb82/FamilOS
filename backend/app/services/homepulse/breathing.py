"""Decode a firmware-provided breathing average from a standard 0x62 record.

Static reconstruction of libringeventparser.so shipped in Oura Android 7.10.0.
This never computes respiration from heart rate, never performs radio I/O,
and never labels a historical on-demand average as a live measurement.
See ../oura-analysis/decoder-respiro.md for the complete evidence chain.
"""
from .protocol import OuraProtocolError, _complete

ON_DEMAND_MEAS_TAG = 0x62


def decode_breathing_record(frame):
    """Decode one complete, standard API_ON_DEMAND_MEAS event.

    The raw ring timestamp is preserved without guessing an epoch/timezone.
    No independent breathing-quality or integrity check was recovered for
    this field; the returned flags remain False. Zero is displayed as absent,
    rather than interpreted as zero breaths or a physiological diagnosis.
    """
    if not isinstance(frame, (bytes, bytearray, memoryview)):
        raise OuraProtocolError('Il record respiratorio deve contenere byte')
    frame = bytes(frame)
    _complete(frame, 7, ON_DEMAND_MEAS_TAG)
    payload = frame[6:]
    # Native parse_api_on_demand_meas accepts 1..8 payload bytes. It has no
    # API-version branch here; the breathing member is optional by length.
    if not 1 <= len(payload) <= 8:
        raise OuraProtocolError('Layout del record respiratorio non riconosciuto')
    raw = payload[6] if len(payload) >= 7 else None
    encoded_rate = raw / 10.0 if raw is not None else None
    if raw is None:
        reason = 'Campo respiratorio assente in questo layout storico'
    elif raw == 0:
        reason = 'Valore respiratorio zero non interpretabile; non mostrato come misura'
    else:
        reason = None
    return {
        'source': 'oura-firmware-on-demand-history-experimental',
        'record_tag': ON_DEMAND_MEAS_TAG,
        'timestamp_raw': int.from_bytes(frame[2:6], 'little'),
        'timestamp_scale': 'ring_clock_not_yet_resolved',
        'utc_timestamp': None,
        'average_breathing_rate_per_minute': encoded_rate if raw else None,
        'breathing_rate_raw_tenths': raw,
        'encoded_breathing_rate_per_minute': encoded_rate,
        'available': raw is not None and raw != 0,
        'unavailable_reason': reason,
        'is_live': False,
        'averaging_window_seconds': None,
        'physiological_quality_validated': False,
        'integrity_validated': False,
        'payload_length': len(payload),
    }


def extract_breathing_records(capture):
    """Select 0x62 records from an oura_history capture, in received order.

    Non-0x62 records are ignored; malformed 0x62 records raise instead of
    being promoted to plausible values. No sorting implies a resolved time.
    """
    if not isinstance(capture, dict) or not isinstance(capture.get('records'), list):
        raise OuraProtocolError('Cattura storica non valida')
    result = []
    for record in capture['records']:
        if not isinstance(record, dict) or not isinstance(record.get('raw_hex'), str):
            raise OuraProtocolError('Record della cattura storica non valido')
        try:
            raw = bytes.fromhex(record['raw_hex'])
        except ValueError:
            raise OuraProtocolError('Record storico non esadecimale') from None
        if raw and raw[0] == ON_DEMAND_MEAS_TAG:
            result.append(decode_breathing_record(raw))
    return result


SLEEP_INFO_TAG = 0x6a


def decode_sleep_breathing_record(frame):
    """Gen3 SleepPeriodInfo2 mean, directly encoded by the firmware.

    Proven native parser fields: payload4/8 and payload5/8. This preserves
    the original mean/variability, without treating sleepState as quality,
    inventing an averaging window or deriving breathing from heart rate.
    """
    if not isinstance(frame, (bytes, bytearray, memoryview)):
        raise OuraProtocolError('Il record respiratorio deve contenere byte')
    frame = bytes(frame)
    _complete(frame, 16, SLEEP_INFO_TAG)
    if not 16 <= len(frame) <= 20:
        raise OuraProtocolError('Layout SleepPeriodInfo2 non riconosciuto')
    payload = frame[6:]
    if payload[6] > 120 or payload[7] not in (0, 1, 2):
        raise OuraProtocolError('Campi SleepPeriodInfo2 fuori dal contratto firmware')
    raw = payload[4]
    return {
        'source': 'oura-firmware-sleep-period-history',
        'record_tag': SLEEP_INFO_TAG,
        'timestamp_raw': int.from_bytes(frame[2:6], 'little'),
        'average_breathing_rate_per_minute': raw / 8 if raw else None,
        'std_breathing_rate_per_minute': payload[5] / 8,
        'breathing_rate_raw_eighths': raw,
        'zero_display_policy': 'zero-not-published',
        'available': raw != 0,
        'unavailable_reason': None if raw else 'Valore zero non mostrato come misura respiratoria',
        'is_live': False, 'averaging_window_seconds': None,
        'physiological_quality_validated': False,
        'sleep_state_raw': payload[7], 'payload_length': len(payload),
    }
