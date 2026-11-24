"""Experimental Oura BLE adapter, reconstructed from Oura Android 7.10.0.

Static evidence: ../oura-analysis/protocol.json and source-evidence.json.
Gen3 firmware3.4.3 reception was tested separately; see hardware reports.
This module performs no key provisioning, reset or cloud access.
"""
import asyncio
import os
import sqlite3
import time
from contextlib import suppress
from dataclasses import dataclass

OURA_SERVICE = '98ed0001-a541-11e4-b6a0-0002a5d5c51b'
OURA_WRITE = '98ed0002-a541-11e4-b6a0-0002a5d5c51b'
OURA_NOTIFY = '98ed0003-a541-11e4-b6a0-0002a5d5c51b'

MODE_LIVE = bytes.fromhex('2f03220203')
SUBSCRIBE_LATEST = bytes.fromhex('2f03260202')
MODE_AUTOMATIC = bytes.fromhex('2f03220201')
UNSUBSCRIBE = bytes.fromhex('2f03260200')
FIRMWARE_REQUEST = bytes.fromhex('0803000000')
NONCE_REQUEST = bytes.fromhex('2f012b')
MAX_CAPABILITY_PAGES = 16  # Adapter bound, not a documented firmware limit.


class OuraProtocolError(Exception):
    """Safe diagnostic: must never contain key, nonce or ciphertext."""


class OuraAuthenticationError(OuraProtocolError):
    pass


def _link_security_error(exc):
    message = str(exc).casefold()
    return any(marker in message for marker in (
        'encryption is insufficient', 'insufficient encryption',
        'authentication is insufficient', 'insufficient authentication',
        'cbatterrordomain code=15', 'cbatterrordomain code=5'))


async def ensure_notifications(client, receiver, timeout=10, on_status=None):
    """Use normal OS pairing after a link-encryption failure, never bypass it.

    Bleak's macOS backend has no explicit pair() API; an encrypted GATT read
    can trigger the system dialog. The read is attempted once, with time for
    the owner to answer. See Bleak's macOS backend documentation.
    """
    try:
        await asyncio.wait_for(client.start_notify(OURA_NOTIFY, receiver), timeout)
        return
    except Exception as exc:
        if not _link_security_error(exc):
            raise
    if on_status:
        on_status('Gen3: conferma l’associazione Bluetooth nell’eventuale finestra del sistema sul computer')
    try:
        # This is an ordinary read, not a raw CCCD write or security downgrade.
        await asyncio.wait_for(client.read_gatt_char(OURA_NOTIFY), max(60, timeout))
        await asyncio.wait_for(client.start_notify(OURA_NOTIFY, receiver), max(60, timeout))
    except Exception:
        raise OuraAuthenticationError('Associazione Bluetooth del sistema non completata: autorizza l’eventuale richiesta sul computer e riprova. Nessun bond è stato rimosso.') from None


@dataclass(frozen=True)
class LiveSample:
    bpm: int | None
    ibi_ms: int | None
    age_raw: int
    reason: str
    warnings: tuple[str, ...] = ()


def _complete(frame, minimum, tag, extended=None):
    if len(frame) < minimum or frame[0] != tag:
        raise OuraProtocolError('Risposta Oura incompleta o inattesa')
    if frame[1] + 2 != len(frame):
        raise OuraProtocolError('Lunghezza della risposta Oura non coerente')
    if extended is not None and (len(frame) < 3 or frame[2] != extended):
        raise OuraProtocolError('Tipo di risposta Oura inatteso')


def parse_capabilities(frame):
    _complete(frame, 4, 0x2f, 0x02)
    pages = frame[3]
    if not 1 <= pages <= MAX_CAPABILITY_PAGES or (len(frame) - 4) % 2:
        raise OuraProtocolError('Inventario delle capacità Oura non valido')
    features = {}
    for pos in range(4, len(frame), 2):
        feature, version = frame[pos:pos + 2]
        if feature in features and features[feature] != version:
            raise OuraProtocolError('Capacità Oura duplicate e incoerenti')
        features[feature] = version
    return pages, features


def parse_firmware(frame):
    _complete(frame, 20, 0x09)
    return tuple(frame[2:5])


def parse_ack(frame, extended):
    _complete(frame, 5, 0x2f, extended)
    if frame[3] != 2:
        raise OuraProtocolError('Risposta riferita a una funzione diversa dal battito live')
    errors = {1:'non supportato', 2:'non disponibile', 3:'anello non indossato',
              4:'comando incompleto', 5:'batteria insufficiente'}
    if frame[4] != 0:
        raise OuraProtocolError('Comando Oura rifiutato: ' + errors.get(frame[4], 'risultato sconosciuto'))


def parse_live(frame, api_version):
    """Use the recovered IBI-validity gate; retain motion as a separate signal."""
    minimum = 17 if tuple(api_version) >= (1, 10, 0) else 15
    _complete(frame, minimum, 0x2f, 0x28)
    if frame[3] != 2:
        raise OuraProtocolError('Notifica diversa dal battito live')
    age_raw = int.from_bytes(frame[6:8], 'little')
    ibi = frame[8] | ((frame[9] & 0x0f) << 8)
    validity = (frame[9] >> 4) & 0x0f
    flags = frame[4]
    reason = ''
    if frame[5] != 2 or not flags & 1:
        reason = 'Gen3: misura non ancora attiva'
    elif validity != 1 or not 0 < ibi <= 2000:
        reason = 'Gen3: intervallo cardiaco non valido'
    warnings = tuple(label for bit,label in ((16,'movimento segnalato'),(4,'segnale ottico debole'),
                      (8,'dita fredde'),(2,'ricerca segnale'),(32,'identificazione segnale')) if flags & bit)
    # No synthetic temperature, HRV, SpO2, or metabolic metrics from this frame.
    return LiveSample(None if reason else round(60000 / ibi),
                      None if reason else ibi, age_raw, reason, warnings)


def auth_ciphertext(key_hex, nonce):
    """Java AES/ECB/PKCS5Padding for a 15-byte nonce is AES(nonce || 01)."""
    try:
        key = bytes.fromhex(key_hex)
    except (TypeError, ValueError):
        raise OuraAuthenticationError('Chiave proprietario non valida: servono 16 byte esadecimali') from None
    if len(key) != 16 or len(nonce) != 15:
        raise OuraAuthenticationError('Dimensioni chiave o nonce Oura non valide')
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    except ImportError:
        raise OuraAuthenticationError('Installa cryptography per autenticare il Gen3 con la tua chiave') from None
    encryptor = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    return encryptor.update(nonce + b'\x01') + encryptor.finalize()
