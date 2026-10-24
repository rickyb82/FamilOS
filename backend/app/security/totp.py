"""TOTP (RFC 6238) per app authenticator (Google Authenticator, Authy, 1Password, Microsoft Authenticator...).
Implementazione con la sola libreria standard: nessuna dipendenza aggiuntiva."""
from __future__ import annotations

import base64
import hmac
import secrets
import struct
import time
from hashlib import sha1
from urllib.parse import quote

DIGITS = 6
PERIOD = 30


def new_secret() -> str:
    """Segreto base32 (20 byte casuali = 32 caratteri), formato accettato da tutte le app."""
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _code(secret: str, counter: int) -> str:
    key = base64.b32decode(secret.upper() + "=" * (-len(secret) % 8))
    digest = hmac.new(key, struct.pack(">Q", counter), sha1).digest()
    offset = digest[-1] & 0x0F
    num = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(num % (10 ** DIGITS)).zfill(DIGITS)


def verify(secret: str, code: str, window: int = 1, now: float | None = None) -> bool:
    """Accetta il codice corrente e quelli adiacenti (+/- window periodi) per tollerare orologi non precisi."""
    code = "".join(ch for ch in str(code) if ch.isdigit())
    if len(code) != DIGITS or not secret:
        return False
    counter = int((now if now is not None else time.time()) // PERIOD)
    return any(hmac.compare_digest(_code(secret, counter + d), code) for d in range(-window, window + 1))


def provisioning_uri(secret: str, account: str, issuer: str = "FamilAI") -> str:
    label = quote(f"{issuer}:{account}", safe="")
    return f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer)}&algorithm=SHA1&digits={DIGITS}&period={PERIOD}"


def qr_svg(data: str) -> str | None:
    """QR code come SVG inline (libreria 'qrcode' se presente; altrimenti None e si mostra il segreto da digitare)."""
    try:
        import qrcode
        import qrcode.image.svg as svg
    except Exception:
        return None
    img = qrcode.make(data, image_factory=svg.SvgPathImage, box_size=6, border=2)
    return img.to_string(encoding="unicode")
