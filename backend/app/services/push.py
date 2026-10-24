"""Notifiche push sul telefono (Web Push con chiavi VAPID): quello che arriva nella campanella arriva anche come notifica di
sistema su iPhone (app installata sulla schermata Home, iOS 16.4 o successivo), Android e computer, anche ad app chiusa.

Le chiavi VAPID le genera Emily al primo uso e le tiene in app_settings (la privata cifrata con SECRET_KEY): non c'e' niente da
configurare. Il testo della notifica e' quello della campanella (niente dati sanitari: PeopleCraft manda "assenza").
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..models import AppSetting, PushSubscription

log = logging.getLogger(__name__)
settings = get_settings()

KEY_PRIVATE, KEY_PUBLIC = "vapid_private_pem", "vapid_public_key"
MAX_PER_USER = 10
_keys: tuple[str, str] | None = None
_lock = asyncio.Lock()


async def keys(db: AsyncSession) -> tuple[str, str]:
    """(chiave privata PEM, chiave pubblica base64url per il browser); generate e salvate la prima volta."""
    global _keys
    if _keys:
        return _keys
    async with _lock:
        if _keys:
            return _keys
        from .settings_store import decrypt, encrypt
        priv = await db.get(AppSetting, KEY_PRIVATE)
        pub = await db.get(AppSetting, KEY_PUBLIC)
        if priv and pub:
            _keys = (decrypt(priv.value), pub.value)
            return _keys
        from cryptography.hazmat.primitives import serialization
        from py_vapid import Vapid01
        v = Vapid01()
        v.generate_keys()
        pem = v.private_pem().decode()
        raw = v.public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        public = base64.urlsafe_b64encode(raw).decode().rstrip("=")
        db.add(AppSetting(key=KEY_PRIVATE, value=encrypt(pem), secret=True))
        db.add(AppSetting(key=KEY_PUBLIC, value=public, secret=False))
        await db.commit()
        _keys = (pem, public)
        return _keys


def _subject() -> str:
    origin = (settings.webauthn_origin or "").rstrip("/")
    return origin if origin.startswith("https://") else "mailto:noreply@localhost"


def _send_sync(sub: dict, payload: str, pem: str) -> int:
    from py_vapid import Vapid01
    from pywebpush import WebPushException, webpush
    try:
        vapid = Vapid01.from_pem(pem.encode())
        r = webpush(subscription_info=sub, data=payload, vapid_private_key=vapid, vapid_claims={"sub": _subject()}, ttl=24 * 3600, timeout=10)
        return getattr(r, "status_code", 201) or 201
    except WebPushException as e:
        return getattr(e.response, "status_code", 0) or 0
    except Exception as e:
        log.info("push non inviata: %s", e)
        return 0


async def send_to_user(db: AsyncSession, user_id: str, title: str, body: str, url: str = "/app", tag: str = "") -> int:
    """Manda la notifica a ogni dispositivo dell'utente; restituisce quanti l'hanno accettata. Mai un'eccezione."""
    try:
        subs = (await db.execute(select(PushSubscription).where(PushSubscription.user_id == user_id))).scalars().all()
        if not subs:
            return 0
        pem, _ = await keys(db)
        payload = json.dumps({"title": title[:80], "body": body[:240], "url": url[:300] or "/app", "tag": tag[:60]})
        ok, gone = 0, []
        for s in subs:
            code = await asyncio.to_thread(_send_sync, {"endpoint": s.endpoint, "keys": {"p256dh": s.p256dh, "auth": s.auth}}, payload, pem)
            if 200 <= code < 300:
                ok += 1
                s.last_ok_at = datetime.now(timezone.utc)
            elif code in (404, 410):
                gone.append(s.id)
        if gone:
            await db.execute(delete(PushSubscription).where(PushSubscription.id.in_(gone)))
        await db.commit()
        return ok
    except Exception:
        log.exception("push all'utente %s non riuscita", user_id)
        return 0


def send_later(user_id: str, title: str, body: str, url: str = "/app", tag: str = "") -> None:
    """In un task a parte con la sua sessione del database: chi notifica non aspetta il servizio push."""
    async def run() -> None:
        from ..db import SessionLocal
        from . import native_push
        async with SessionLocal() as db:
            await send_to_user(db, user_id, title, body, url, tag)
            await native_push.send_to_user(db, user_id, title, body, url, tag)   # l'app Emily sul telefono (APNs / FCM)
    try:
        asyncio.get_running_loop().create_task(run())
    except RuntimeError:
        pass


async def subscribe(db: AsyncSession, user_id: str, sub: dict, device: str) -> None:
    endpoint = str(sub.get("endpoint") or "")
    k = sub.get("keys") or {}
    p256dh, auth = str(k.get("p256dh") or ""), str(k.get("auth") or "")
    if not endpoint.startswith("https://") or len(endpoint) > 1000 or not p256dh or not auth or len(p256dh) > 200 or len(auth) > 100:
        raise ValueError("sottoscrizione non valida")
    row = (await db.execute(select(PushSubscription).where(PushSubscription.endpoint == endpoint))).scalar_one_or_none()
    if row:
        row.user_id, row.p256dh, row.auth, row.device = user_id, p256dh, auth, device[:120]
    else:
        db.add(PushSubscription(user_id=user_id, endpoint=endpoint, p256dh=p256dh, auth=auth, device=device[:120]))
    await db.flush()
    # al massimo MAX_PER_USER dispositivi: i piu' vecchi lasciano il posto
    rows = (await db.execute(select(PushSubscription).where(PushSubscription.user_id == user_id)
                             .order_by(PushSubscription.created_at.desc()))).scalars().all()
    for old in rows[MAX_PER_USER:]:
        await db.delete(old)
    await db.commit()


async def unsubscribe(db: AsyncSession, user_id: str, endpoint: str) -> None:
    await db.execute(delete(PushSubscription).where(PushSubscription.user_id == user_id, PushSubscription.endpoint == endpoint[:1000]))
    await db.commit()
