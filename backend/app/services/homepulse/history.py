"""Bounded read-only history capture; no physiological interpretation.

GetEvent.java lines 34–41, 85–118 (Android 7.10.0). The caller owns one
connected/authenticated SetupSession exclusively. No key access, time writes,
analysis trigger, erase or radio connection is performed by this module.
"""
import asyncio
import hashlib
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import uuid

from .protocol import OURA_WRITE, OuraProtocolError, OuraAuthenticationError, _complete


def make_history_request(start_timestamp=0, max_events=8, flags=0xffffffff):
    # Default eight, application maximum64; the APK/wire allows up to255.
    if type(start_timestamp) is not int or not 0 <= start_timestamp <= 0xffffffff:
        raise ValueError('Timestamp ring fuori intervallo')
    if type(max_events) is not int or not 1 <= max_events <= 64:
        raise ValueError('Richiedere da 1 a 64 record per lettura')
    if type(flags) is not int or not 0 <= flags <= 0xffffffff:
        raise ValueError('Filtro record fuori intervallo')
    return bytes.fromhex('1009') + start_timestamp.to_bytes(4, 'little') + bytes([max_events]) + flags.to_bytes(4, 'little')


def parse_history_summary(frame):
    frame = bytes(frame)
    _complete(frame, 3, 0x11)
    return {'events_count': frame[2],
            'sleep_analysis_progress_raw': frame[3] if frame[1] >= 2 else None,
            'bytes_left': int.from_bytes(frame[4:8], 'little') if frame[1] >= 6 else None,
            'raw_hex': frame.hex()}


def describe_raw_record(frame):
    """Preserve records verbatim. Native CRC/time mapping is not ported."""
    frame = bytes(frame)
    if not frame or frame[0] < 0x41 or len(frame) > 4096:
        raise OuraProtocolError('Record storico inatteso o oltre il limite locale')
    tag = frame[0]
    result = {'tag_raw': tag, 'raw_hex': frame.hex(), 'integrity_validated': False,
              'utc_timestamp': None, 'physiology': None}
    if tag not in (0xfd, 0xfe):
        _complete(frame, 6, tag)
        result.update(timestamp_raw=int.from_bytes(frame[2:6], 'little'),
                      timestamp_scale='ring_clock_not_yet_resolved',
                      payload_hex=frame[6:].hex())
    elif tag == 0xfd:
        if len(frame) < 12 or frame[3] + 4 != len(frame):
            raise OuraProtocolError('Record storico esteso incompleto')
        result['encoding'] = 'extended_fd_crc_not_verified'
    else:
        # FE has subtype-dependent length layouts. Raw preservation only.
        if len(frame) < 8:
            raise OuraProtocolError('Record storico FE incompleto')
        result['encoding'] = 'extended_fe_not_decoded'
    return result


def split_history_packet(packet):
    """The app splits up to20 concatenated standard records per notification."""
    packet = bytes(packet)
    result, offset = [], 0
    while offset < len(packet):
        if len(result) >= 20 or len(packet) - offset < 2:
            raise OuraProtocolError('Pacchetto storico concatenato non valido')
        tag = packet[offset]
        if tag == 0xfe:
            # Subtype-dependent framing remains opaque; no cursor advancement.
            result.append(packet[offset:])
            break
        if tag == 0xfd:
            if len(packet) - offset < 4:
                raise OuraProtocolError('Record FD troncato')
            size = packet[offset+3] + 4
        else:
            size = packet[offset+1] + 2
        if tag < 0x41 or size < 6 or offset + size > len(packet):
            raise OuraProtocolError('Confine del record storico non valido')
        result.append(packet[offset:offset+size])
        offset += size
    return result
