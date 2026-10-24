"""Persistent, rate-limited administrator notices. Never include credentials or raw request bodies."""
from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from ..config import get_settings, mfa_optional_on
from ..db import SessionLocal
from ..models import Role, SecurityNotice, User
from . import mailer

log = logging.getLogger(__name__)
SUCCESS = {"login_pin", "login_google", "login_passkey", "mfa_passkey_ok", "mfa_totp_ok",
           "mfa_email_ok", "recovery_ok", "sync_device_login", "login_enrollment_completed"}
FAILURE = {"login_unknown", "login_refused", "login_failed", "login_passkey_failed", "pin_failed",
           "pin_refused", "mfa_failed", "mfa_totp_failed", "mfa_email_failed", "recovery_failed",
           "google_login_unknown", "google_login_failed", "screen_unlock_failed"}


async def enqueue(db, event: str, user, ip: str, detail: str = "") -> None:
    settings = get_settings()
    if not settings.security_login_alerts or not mailer.email_enabled():
        return
    success = event in SUCCESS
    if event == "login_password" and detail == "no_second_factor":
        success = not settings.require_mfa_for_adults or (mfa_optional_on() and user.mfa_choice == "skip")
    if event == "mfa_totp_ok" and detail == "craft sync":
        return  # The following sync_device_login is the completed login.
    if not success and event not in FAILURE:
        return
    now = datetime.now(timezone.utc)
    family = user.family_id if user else None
    identity = user.email or user.username if user else "Account non riconosciuto"
    # Unknown input (email, token, exception details) is never copied into the notice.
    key = hashlib.sha256(f"{family}:{user.id if user else '-'}:{success}:{ip}".encode()).hexdigest()
    recent = (await db.execute(select(SecurityNotice.id).where(
        SecurityNotice.fingerprint == key, SecurityNotice.created_at > now-timedelta(minutes=5)
    ).limit(1))).scalar_one_or_none()
    if recent:
        return
    count = (await db.execute(select(func.count()).select_from(SecurityNotice).where(
        SecurityNotice.family_id == family, SecurityNotice.success == success,
        SecurityNotice.created_at > now-timedelta(hours=1)
    ))).scalar_one()
    if count >= 20:
        return
    db.add(SecurityNotice(family_id=family, fingerprint=key, success=success, event=event,
                          account=identity[:200], ip=ip[:64], created_at=now, next_attempt_at=now))
    await db.commit()


async def deliver_pending() -> None:
    if not mailer.email_enabled() or not get_settings().security_login_alerts:
        return
    now = datetime.now(timezone.utc)
    async with SessionLocal() as db:
        rows = (await db.execute(select(SecurityNotice).where(
            SecurityNotice.sent_at.is_(None), SecurityNotice.attempts < 5,
            SecurityNotice.next_attempt_at <= now
        ).order_by(SecurityNotice.created_at).limit(10))).scalars().all()
        for notice in rows:
            query = select(User.email).where(User.role == Role.admin, User.is_active.is_(True), User.email.is_not(None))
            if notice.family_id:
                query = query.where(User.family_id == notice.family_id)
            recipients = set((await db.execute(query)).scalars().all())
            sent = list(notice.delivered_to or [])
            label = "Accesso riuscito" if notice.success else "Tentativo di accesso respinto"
            text = (f"{label} a FamilOS.\n\nAccount: {notice.account}\n"
                    f"Data (UTC): {notice.created_at.isoformat()}\nIndirizzo IP: {notice.ip}\n"
                    f"Evento: {notice.event}\n\n"
                    "Consulta il registro accessi in Amministrazione. Un accesso riuscito indica credenziali "
                    "accettate: se non lo riconosci, revoca le sessioni e cambia le credenziali.\n"
                    "Gli eventi ripetuti sono raggruppati per 5 minuti; massimo 20 avvisi per esito ogni ora. "
                    "Il registro conserva anche gli eventi per cui non viene inviata una mail.")
            notice.attempts += 1
            notice.next_attempt_at = now + timedelta(minutes=2 ** notice.attempts)
            await db.commit()
            try:
                for recipient in sorted(recipients-set(sent)):
                    await mailer.send_email(recipient, f"[FamilOS sicurezza] {label}", text)
                    sent.append(recipient)
                    notice.delivered_to = list(sent)
                    await db.commit()
                if recipients:
                    notice.sent_at = datetime.now(timezone.utc)
                    await db.commit()
            except Exception:
                log.warning("Invio avviso sicurezza fallito; nuovo tentativo programmato", exc_info=False)
        from sqlalchemy import delete
        await db.execute(delete(SecurityNotice).where(SecurityNotice.created_at < now-timedelta(days=30)))
        await db.commit()


async def worker() -> None:
    while True:
        try:
            await deliver_pending()
        except Exception:
            log.exception("Errore nella coda degli avvisi sicurezza")
        await asyncio.sleep(10)
