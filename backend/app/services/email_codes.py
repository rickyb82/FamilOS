"""Generazione e verifica dei codici monouso inviati via email."""
from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import EmailToken, User

TTL = {"invite": timedelta(hours=48), "reset": timedelta(minutes=10), "mfa": timedelta(minutes=10)}
MAX_ATTEMPTS = 5


def _h(code: str) -> str:
    return hashlib.sha256(code.strip().replace(" ", "").lower().encode()).hexdigest()


async def issue(db: AsyncSession, user: User, purpose: str) -> str:
    """Invalida i codici precedenti dello stesso tipo e ne crea uno nuovo. Ritorna il codice in chiaro (da inviare)."""
    await db.execute(update(EmailToken).where(EmailToken.user_id == user.id, EmailToken.purpose == purpose,
                                               EmailToken.used == False).values(used=True))  # noqa: E712
    code = secrets.token_urlsafe(24) if purpose == "invite" else f"{secrets.randbelow(1_000_000):06d}"
    db.add(EmailToken(user_id=user.id, purpose=purpose, code_hash=_h(code),
                      expires_at=datetime.now(timezone.utc) + TTL[purpose]))
    await db.commit()
    return code


async def verify(db: AsyncSession, user: User, purposes: tuple[str, ...], code: str) -> bool:
    """Verifica e consuma il codice. Dopo MAX_ATTEMPTS tentativi il codice viene bruciato."""
    now = datetime.now(timezone.utc)
    rows = (await db.execute(select(EmailToken).where(EmailToken.user_id == user.id, EmailToken.purpose.in_(purposes),
                                                      EmailToken.used == False))).scalars().all()  # noqa: E712
    target = _h(code)
    ok = False
    for t in rows:
        if t.expires_at.replace(tzinfo=timezone.utc) < now:
            t.used = True
            continue
        if hmac.compare_digest(t.code_hash, target):
            t.used = True
            ok = True
        else:
            t.attempts += 1
            if t.attempts >= MAX_ATTEMPTS:
                t.used = True
    await db.commit()
    return ok
