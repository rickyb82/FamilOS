"""Notifiche native per l'app Emily sul telefono: APNs (iPhone) e Firebase Cloud Messaging (Android).

Si accendono con le chiavi nel .env (APNS_KEY_P8_PATH + APNS_KEY_ID + APNS_TEAM_ID per Apple, FCM_SERVICE_ACCOUNT_PATH per
Google); senza, l'app riceve comunque le notifiche Web Push come il browser. Il testo e' quello della campanella; `url` e'
la pagina da aprire al tocco (lo fa la web app dentro l'app, vedi frontend/src/lib/native.ts).
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..models import NativeDevice

log = logging.getLogger(__name__)
settings = get_settings()

MAX_PER_USER = 10
_apns_jwt: tuple[str, float] = ("", 0.0)
_fcm_token: tuple[str, float] = ("", 0.0)
_fcm_project: str = ""


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def apns_enabled() -> bool:
    return bool(settings.apns_key_p8_path and settings.apns_key_id and settings.apns_team_id and Path(settings.apns_key_p8_path).is_file())


def fcm_enabled() -> bool:
    return bool(settings.fcm_service_account_path and Path(settings.fcm_service_account_path).is_file())


def enabled() -> bool:
    return apns_enabled() or fcm_enabled()


def _apns_token() -> str:
    """JWT ES256 firmato con la chiave .p8; APNs lo accetta per un'ora, lo rinnoviamo ogni 50 minuti."""
    global _apns_jwt
    tok, made = _apns_jwt
    if tok and time.time() - made < 50 * 60:
        return tok
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
    key = serialization.load_pem_private_key(Path(settings.apns_key_p8_path).read_bytes(), password=None)
    header = _b64url(json.dumps({"alg": "ES256", "kid": settings.apns_key_id}).encode())
    claims = _b64url(json.dumps({"iss": settings.apns_team_id, "iat": int(time.time())}).encode())
    signing = f"{header}.{claims}".encode()
    der = key.sign(signing, ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der)
    sig = _b64url(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
    tok = f"{header}.{claims}.{sig}"
    _apns_jwt = (tok, time.time())
    return tok


async def _send_apns(cli: httpx.AsyncClient, token: str, title: str, body: str, url: str, tag: str) -> int:
    host = "api.sandbox.push.apple.com" if settings.apns_sandbox else "api.push.apple.com"
    payload = {"aps": {"alert": {"title": title, "body": body}, "sound": "default", "thread-id": tag or "emily", "mutable-content": 0},
               "url": url, "tag": tag}
    headers = {"authorization": f"bearer {_apns_token()}", "apns-topic": settings.apns_bundle_id, "apns-push-type": "alert",
               "apns-priority": "10", "apns-expiration": str(int(time.time()) + 24 * 3600)}
    if tag:
        headers["apns-collapse-id"] = tag[:64]
    r = await cli.post(f"https://{host}/3/device/{token}", json=payload, headers=headers)
    if r.status_code == 410 or (r.status_code == 400 and "BadDeviceToken" in r.text):
        return 410
    return r.status_code


async def _fcm_access_token(cli: httpx.AsyncClient) -> str:
    """OAuth2 con il service account (JWT RS256 scambiato con un access token), rinnovato ogni 50 minuti."""
    global _fcm_token, _fcm_project
    tok, made = _fcm_token
    if tok and time.time() - made < 50 * 60:
        return tok
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    sa = json.loads(Path(settings.fcm_service_account_path).read_text())
    _fcm_project = str(sa.get("project_id") or "")
    key = serialization.load_pem_private_key(str(sa["private_key"]).encode(), password=None)
    now = int(time.time())
    header = _b64url(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    claims = _b64url(json.dumps({"iss": sa["client_email"], "scope": "https://www.googleapis.com/auth/firebase.messaging",
                                 "aud": "https://oauth2.googleapis.com/token", "iat": now, "exp": now + 3600}).encode())
    signing = f"{header}.{claims}".encode()
    sig = _b64url(key.sign(signing, padding.PKCS1v15(), hashes.SHA256()))
    r = await cli.post("https://oauth2.googleapis.com/token",
                       data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": f"{header}.{claims}.{sig}"})
    r.raise_for_status()
    tok = str(r.json().get("access_token") or "")
    _fcm_token = (tok, time.time())
    return tok


async def _send_fcm(cli: httpx.AsyncClient, token: str, title: str, body: str, url: str, tag: str) -> int:
    access = await _fcm_access_token(cli)
    msg = {"message": {"token": token, "notification": {"title": title, "body": body},
                       "data": {"url": url, "tag": tag},
                       "android": {"priority": "high", "collapse_key": (tag or "emily")[:64], "ttl": "86400s",
                                   "notification": {"channel_id": "emily", "tag": tag or None}}}}
    r = await cli.post(f"https://fcm.googleapis.com/v1/projects/{_fcm_project}/messages:send", json=msg,
                       headers={"authorization": f"Bearer {access}"})
    if r.status_code == 404 or (r.status_code == 400 and "UNREGISTERED" in r.text) or "UNREGISTERED" in r.text:
        return 410
    return r.status_code


async def send_to_user(db: AsyncSession, user_id: str, title: str, body: str, url: str = "/app", tag: str = "") -> int:
    """Manda la notifica ai telefoni dell'utente con l'app; restituisce quanti l'hanno accettata. Mai un'eccezione."""
    if not enabled():
        return 0
    try:
        devices = (await db.execute(select(NativeDevice).where(NativeDevice.user_id == user_id))).scalars().all()
        if not devices:
            return 0
        ok, gone = 0, []
        async with httpx.AsyncClient(http2=True, timeout=10) as cli:
            for d in devices:
                try:
                    if d.platform == "ios" and apns_enabled():
                        code = await _send_apns(cli, d.token, title[:80], body[:240], url[:300] or "/app", tag[:60])
                    elif d.platform == "android" and fcm_enabled():
                        code = await _send_fcm(cli, d.token, title[:80], body[:240], url[:300] or "/app", tag[:60])
                    else:
                        continue
                except Exception as e:
                    log.info("notifica nativa non inviata (%s): %s", d.platform, e)
                    continue
                if 200 <= code < 300:
                    ok += 1
                    d.last_ok_at = datetime.now(timezone.utc)
                elif code == 410:
                    gone.append(d.id)
                else:
                    log.info("notifica nativa rifiutata (%s): HTTP %s", d.platform, code)
        if gone:
            await db.execute(delete(NativeDevice).where(NativeDevice.id.in_(gone)))
        await db.commit()
        return ok
    except Exception:
        log.exception("notifica nativa all'utente %s non riuscita", user_id)
        return 0


async def register(db: AsyncSession, user_id: str, platform: str, token: str, device: str, app_version: str) -> None:
    platform = platform.strip().lower()
    token = token.strip()
    if platform not in ("ios", "android") or not token or len(token) > 400 or not all(c.isalnum() or c in "-_:." for c in token):
        raise ValueError("dispositivo non valido")
    row = (await db.execute(select(NativeDevice).where(NativeDevice.token == token))).scalar_one_or_none()
    if row:
        row.user_id, row.platform, row.device, row.app_version = user_id, platform, device[:120], app_version[:40]
    else:
        db.add(NativeDevice(user_id=user_id, platform=platform, token=token, device=device[:120], app_version=app_version[:40]))
    await db.flush()
    rows = (await db.execute(select(NativeDevice).where(NativeDevice.user_id == user_id).order_by(NativeDevice.created_at.desc()))).scalars().all()
    for old in rows[MAX_PER_USER:]:
        await db.delete(old)
    await db.commit()


async def unregister(db: AsyncSession, user_id: str, token: str) -> None:
    await db.execute(delete(NativeDevice).where(NativeDevice.user_id == user_id, NativeDevice.token == token[:400]))
    await db.commit()
