"""Statically recovered motion/activity decoders for Android Oura 7.10.0.

Sources and limits: ../oura-analysis/movimento-e-attivita.md. These functions
do not access a device, start accelerometry, classify a workout, or infer pedal
revolutions. Historical UTC must be supplied by the native ring-time mapper.
"""
import hashlib
import math
import struct

from .protocol import OuraProtocolError, _complete


def _utc(value):
    if value is None:
        return None
    if type(value) not in (int, float) or value <= 0:
        raise ValueError('UTC mappato non valido')
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        raise ValueError('UTC mappato non valido')
    return value


def _base(frame, tag, utc_ms):
    frame = bytes(frame)
    _complete(frame, 6, tag)
    return frame, {
        'tag_raw': tag,
        'record_sha256': hashlib.sha256(frame).hexdigest(),
        'raw_hex': frame.hex(),
        'timestamp_raw': int.from_bytes(frame[2:6], 'little'),
        'timestamp_scale': 'ring_clock',
        'utc_ms': _utc(utc_ms),
        'utc_source': 'caller_native_ring_time_mapping' if utc_ms is not None else None,
        'integrity_validated': False,
        'workout_type': None,
        'pedal_revolutions': None,
    }


def parse_motion_record(frame, *, utc_ms=None):
    """Decode standard0x47 motion; no raw-axis SI unit or cadence is claimed.

    Native ecore accepts wire length byte8..10, i.e. payload4..6. Its separate
    ringeventparser rejects bit6 of either optional intensity byte. The latter
    exposes signed axis bytes multiplied by8 as averageX/Y/Z. Optional fields
    are zero in the app when absent; present flags distinguish those defaults.
    """
    frame, result = _base(frame, 0x47, utc_ms)
    payload = frame[6:]
    if not 4 <= len(payload) <= 6:
        raise OuraProtocolError('Lunghezza del record movimento non supportata')
    if any(value & 0x40 for value in payload[4:6]):
        raise OuraProtocolError('Codifica intensità movimento non supportata dal parser originale')
    signed = lambda value: value - 256 if value >= 128 else value
    low = payload[4] if len(payload) >= 5 else 0
    high = payload[5] if len(payload) >= 6 else 0
    result.update({
        'source': 'oura-ble-motion-history',
        'orientation_raw': payload[0] >> 5,
        'motion_seconds': payload[0] & 0x1f,
        'regularity_raw': low >> 7,
        'low_density_motion_count': low & 0x3f,
        'high_density_motion_count': high & 0x3f,
        'low_density_field_present': len(payload) >= 5,
        'high_density_field_present': len(payload) >= 6,
        'average_x_raw_signed': signed(payload[1]),
        'average_y_raw_signed': signed(payload[2]),
        'average_z_raw_signed': signed(payload[3]),
        'average_x_scaled': signed(payload[1]) * 8,
        'average_y_scaled': signed(payload[2]) * 8,
        'average_z_scaled': signed(payload[3]) * 8,
        'axis_unit': None,
        'record_interval_seconds': None,
        'step_count': None,
        'cadence_rpm': None,
        'measurement_valid': None,
    })
    return result


def unpack_met_level(encoded):
    """Mirror the recovered byte-to-float MET conversion, including float32."""
    if type(encoded) is not int or not 0 <= encoded <= 255:
        raise ValueError('MET compresso: richiesto un byte')
    decoded = encoded * 0.1 if encoded < 128 else 12.8 + (encoded - 128) * 0.2
    return struct.unpack('<f', struct.pack('<f', decoded))[0]


def parse_activity_record(frame, *, utc_ms=None):
    """Decode standard0x50 step count plus up to13 packed minute METs.

    step_count is the record's unsigned byte, which the native activity engine
    adds directly to its step accumulator; it is not a live cumulative counter.
    The app defines the record's period end as its mapped timestamp and its
    start as end minus one minute per supplied MET. No sport label is encoded.
    """
    frame, result = _base(frame, 0x50, utc_ms)
    payload = frame[6:]
    if not 1 <= len(payload) <= 14:
        raise OuraProtocolError('Lunghezza del record attività non supportata')
    mets = [unpack_met_level(value) for value in payload[1:]]
    result.update({
        'source': 'oura-ble-activity-history',
        'step_count': payload[0],
        'step_count_kind': 'record_increment_not_cumulative',
        'met_levels': mets,
        'met_levels_raw': list(payload[1:]),
        'met_count': len(mets),
        'met_interval_seconds': 60,
        'period_duration_seconds': len(mets) * 60,
        'period_end_utc_ms': result['utc_ms'],
        'period_start_utc_ms': None if result['utc_ms'] is None else result['utc_ms'] - len(mets) * 60_000,
        'sport_classification': None,
        'cadence_rpm': None,
        'measurement_valid': None,
    })
    return result


def parse_live_accelerometer(frame, *, received_at=None):
    """Decode raw0x33 ACM notification; do not fabricate sample timestamps.

    The APK reads one or two signed little-endian int16 XYZ samples, a signed
    sample-rate byte and a signed sequence byte. Rate units, coordinate scale
    and per-sample acquisition UTC have not been established. received_at, if
    supplied, is only the host packet arrival epoch seconds.
    """
    frame = bytes(frame)
    _complete(frame, 10, 0x33)
    if len(frame) not in (10, 16):
        raise OuraProtocolError('Notifica accelerometro incompleta o formato non supportato')
    if received_at is not None:
        _utc(received_at)
    signed = lambda value: value - 256 if value >= 128 else value
    samples = []
    for offset in range(4, len(frame), 6):
        x, y, z = struct.unpack_from('<hhh', frame, offset)
        samples.append({'x_raw': x, 'y_raw': y, 'z_raw': z, 'utc_ms': None})
    return {
        'source': 'oura-ble-accelerometer-raw-experimental',
        'tag_raw': 0x33,
        'raw_hex': frame.hex(),
        'sample_rate_raw': signed(frame[2]),
        'sample_rate_raw_unsigned': frame[2],
        'sample_rate_unit': None,
        'sequence_number_raw': signed(frame[3]),
        'sequence_number_unsigned': frame[3],
        'host_received_at': received_at,
        'samples': samples,
        'axis_unit': None,
        'sample_acquisition_utc_known': False,
        'step_count': None,
        'pedal_revolutions': None,
        'cadence_rpm': None,
        'workout_type': None,
        'measurement_valid': None,
    }
