"""Experimental Oura SpO2 latest-value decoder and explicit night setup.

Recovered from Android 7.10.0, GetFeatureLatestValues.parseResponse() lines
97–111 and FeatureLatestValues.Spo2Values constructor lines 750–765.
These are stored latest sensor fields, not a validated live oximetry reading.
No connection, credentials or writes happen at import time.
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .protocol import OuraProtocolError, _complete

SPO2_LATEST_REQUEST = bytes.fromhex('2f022404')
SPO2_AUTO_REQUEST = bytes.fromhex('2f03220401')
SPO2_APK_DEFAULT_PARAMETERS = bytes.fromhex('0164031e18000000')
SPO2_APK_DEFAULT_REQUEST = bytes.fromhex('2f0a2904') + SPO2_APK_DEFAULT_PARAMETERS


def make_sync_time_request(when=None, timezone_name='Europe/Rome'):
    """Normal SyncTime (force flag false); Unix seconds + signed half-hours.

    SyncTime.generateRequestBytes(), Android 7.10.0 lines 71–85. This is a
    clock write, not a factory reset. DST offset is determined at `when`.
    """
    when = when or datetime.now(timezone.utc)
    if when.tzinfo is None or when.utcoffset() is None:
        raise ValueError('La data deve includere un fuso orario')
    offset = when.astimezone(ZoneInfo(timezone_name)).utcoffset()
    seconds = int(when.timestamp())
    offset_seconds = int(offset.total_seconds())
    if seconds < 0 or seconds >= 1 << 64 or offset_seconds % 1800:
        raise ValueError('Data o fuso non rappresentabili dal protocollo')
    half_hours = offset_seconds // 1800
    if not -128 <= half_hours <= 127:
        raise ValueError('Fuso fuori intervallo')
    return bytes.fromhex('1209') + seconds.to_bytes(8, 'little') + bytes([half_hours & 255])


def parse_sync_time_reply(frame):
    """APK checks tag 0x13 only; do not invent a result-byte meaning."""
    frame = bytes(frame)
    _complete(frame, 2, 0x13)
    return {'ack_received': True, 'raw_hex': frame.hex(),
            'result_status': None, 'status_semantics': 'not_defined_by_apk_parser'}


def parse_spo2_setting_reply(frame, extended_tag):
    if extended_tag not in (0x23, 0x2a):
        raise ValueError('ACK impostazione non supportato')
    frame = bytes(frame)
    _complete(frame, 5, 0x2f, extended_tag)
    if frame[3] != 4 or frame[4] != 0:
        raise OuraProtocolError('Impostazione SpO2 rifiutata o funzione errata')
    return {'ack_received': True, 'result_raw': 0, 'raw_hex': frame.hex()}


async def configure_night(session, capabilities, *, when=None,
                          timezone_name='Europe/Rome'):
    """Explicit reversible writes, only for the recovered cap4 version1.

    Caller supplies an exclusively owned, authenticated SetupSession. Config
    uses APK fallback defaults, not verified server tuning or a guarantee of
    overnight measurements. No owner keys, pairing, reset or history deletion.
    A failed ACK raises; prior successful clock/mode writes may remain active.
    """
    if capabilities.get(4, capabilities.get('4')) != 1:
        raise OuraProtocolError('Configurazione notte ammessa solo per capability SpO2 versione 1')
    request = make_sync_time_request(when, timezone_name)
    clock = parse_sync_time_reply(await session.exchange(request, 0x13))
    mode = parse_spo2_setting_reply(
        await session.exchange(SPO2_AUTO_REQUEST, 0x2f, 0x23), 0x23)
    parameters = parse_spo2_setting_reply(
        await session.exchange(SPO2_APK_DEFAULT_REQUEST, 0x2f, 0x2a), 0x2a)
    return {'configured': True, 'configuration_source': 'android_7.10.0_apk_fallback_defaults',
            'hardware_measurement_verified': False, 'timezone': timezone_name,
            'clock_ack': clock, 'mode_ack': mode, 'parameters_ack': parameters,
            'latest': await read_spo2(session)}


def parse_spo2_latest(frame):
    """Decode exact firmware fields; preserve unknown units and quality.

    A nonzero success-like byte or plausible-looking level does not establish
    measurement validity. Thus spo2_percent and measurement_valid stay None.
    Signedness of byte fields follows the recovered Java getters; unsigned
    forms are included for future firmware-specific interpretation.
    """
    frame = bytes(frame)
    _complete(frame, 16, 0x2f, 0x25)
    if frame[3] != 4:
        raise OuraProtocolError('Risposta riferita a una funzione diversa da SpO2')
    if frame[4] not in range(6):
        raise OuraProtocolError('Esito SpO2 non riconosciuto')
    if frame[6] not in range(4):
        raise OuraProtocolError('Stato SpO2 non riconosciuto')
    signed = lambda b: b - 256 if b >= 128 else b
    flags = frame[5]
    names = ('on', 'searching', 'no_reliable_ppg_signal', 'cold_fingers',
             'too_much_movements', 'identifying_signal')
    reasons = []
    if frame[4] != 0:
        reasons.append('request_not_successful')
    if frame[12] == 0:
        reasons.append('zero_or_unavailable_level')
    if not flags & 1:
        reasons.append('feature_off')
    reasons.extend(name for bit, name in enumerate(names[1:], 1)
                   if flags & (1 << bit))
    reasons.append('signal_quality_and_units_not_validated')
    return {
        'source': 'oura-ble-spo2-latest-experimental',
        'kind': 'last_stored_sensor_fields',
        'request_result_raw': frame[4],
        'request_succeeded': frame[4] == 0,
        'flags_raw': flags,
        'flags': {name: bool(flags & (1 << bit))
                  for bit, name in enumerate(names)},
        'state_raw': frame[6],
        'age_raw': int.from_bytes(frame[7:9], 'little'),
        'age_raw_signed': int.from_bytes(frame[7:9], 'little', signed=True),
        'age_unit': None,
        'signal_quality_raw': signed(frame[9]),
        'signal_quality_raw_unsigned': frame[9],
        'perfusion_red_raw': signed(frame[10]),
        'perfusion_red_raw_unsigned': frame[10],
        'perfusion_ir_raw': signed(frame[11]),
        'perfusion_ir_raw_unsigned': frame[11],
        'last_spo2_level_raw': signed(frame[12]),
        'last_spo2_level_raw_unsigned': frame[12],
        'last_hr_level_raw': signed(frame[13]),
        'last_hr_level_raw_unsigned': frame[13],
        'temperature_raw': int.from_bytes(frame[14:16], 'little', signed=True),
        'temperature_unit': None,
        'spo2_percent': None,
        'measurement_valid': None,
        'validated_live': False,
        'not_publishable_reasons': reasons,
    }


async def read_spo2(session):
    """Use an already connected/authenticated SetupSession; no state changes.

    The caller owns authentication, radio serialization and capability checks.
    The sole protocol write requests the latest stored feature4 values.
    """
    frame = await session.exchange(SPO2_LATEST_REQUEST, 0x2f, 0x25)
    result = parse_spo2_latest(frame)
    result['received_at'] = datetime.now(timezone.utc).isoformat()
    return result
