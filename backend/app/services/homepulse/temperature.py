"""Historical temperature conversion recovered from Oura Android native code.

API_TEMP_EVENT0x46: signed little-endian16-bit fields /100. Seven fields share
one record timestamp; sensor identity and chronology within them are unknown.
No core-body-temperature claim, live conversion or hardware access.
"""
from .protocol import OuraProtocolError, _complete


def parse_temperature_record(frame, *, selected_field_index=None):
    """Decode all fields; selecting a named field requires an explicit index.

    Sources: libringeventparser parse_api_temp_event0x2a450c..0x2a4788;
    a20.d.h(TemperatureInfo) /100; ecore temperature_process_event initializes
    absent extended fields to0x8000. Do not call the final field 'latest'.
    """
    frame = bytes(frame)
    _complete(frame, 10, 0x46)
    payload = frame[6:]
    if len(payload) not in range(4, 15, 2):
        raise OuraProtocolError('Formato temperatura storico non supportato')
    if selected_field_index is not None and (type(selected_field_index) is not int or not 1 <= selected_field_index <= 7):
        raise ValueError('Indice campo temperatura deve essere1..7 o None')
    fields=[]
    for index in range(7):
        present = 2*index < len(payload)
        raw = int.from_bytes(payload[2*index:2*index+2], 'little', signed=True) if present else None
        missing = raw is None or raw == -32768
        fields.append({'field_index': index+1, 'raw_centidegrees': raw,
                       'celsius': None if missing else raw/100.0,
                       'present_in_record': present, 'missing_sentinel': raw == -32768,
                       'measurement_valid': None, 'sensor_identity': None})
    values=[f['celsius'] for f in fields if f['celsius'] is not None]
    selected=fields[selected_field_index-1] if selected_field_index is not None else None
    return {'source':'oura-ble-temperature-history','tag':0x46,
            'ring_cursor_raw':int.from_bytes(frame[2:6],'little'),
            'utc_timestamp':None,'fields':fields,
            'selected_field_index':selected_field_index,
            'selected_celsius':selected['celsius'] if selected else None,
            'min_celsius':min(values) if values else None,
            'max_celsius':max(values) if values else None,
            'field_relationship':'same_record_timestamp; chronological_order_not_proven',
            'skin_sensor_field_index':None,'quality':None,
            'measurement_valid':None,'core_body_temperature':False}


def parse_live_temperature_raw(frame, *, api_version):
    """Extract cap2 subscription temperature only; live °C scale unproven.

    Event28 layout has no result byte. API>=1.10 temperature is signedLE16 at
    offset14; legacy API<1.10 at offset13. Unit is deliberately None.
    """
    version=tuple(api_version)
    if len(version)!=3 or any(type(v) is not int or v<0 for v in version):
        raise ValueError('Versione API deve essere una tupla di3 interi')
    modern=version >= (1,10,0)
    frame=bytes(frame)
    _complete(frame,17 if modern else 15,0x2f,0x28)
    if frame[3]!=2 or frame[5] not in range(4):
        raise OuraProtocolError('Evento temperatura live inatteso')
    offset=14 if modern else 13
    raw=int.from_bytes(frame[offset:offset+2],'little',signed=True)
    return {'source':'oura-ble-live-temperature-raw','raw':raw,'raw_offset':offset,
            'celsius':None,'unit':None,'flags_raw':frame[4],'state_raw':frame[5],
            'age_raw':int.from_bytes(frame[6:8],'little'),
            'measurement_valid':None,'core_body_temperature':False}
