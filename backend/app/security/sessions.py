"""Gestione sessioni via cookie HttpOnly + dispositivi fidati + rate limiting."""
from __future__ import annotations

import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import get_db
from ..models import Role, Session, TrustedDevice, User

settings = get_settings()
SESSION_COOKIE = "familai_session"
DEVICE_COOKIE = "familai_device"


# ---------- rate limiting (in memoria, sufficiente per uso domestico) ----------
class RateLimiter:
    def __init__(self, max_hits: int, window_s: int):
        self.max_hits = max_hits
        self.window_s = window_s
        self.hits: dict[str, deque] = defaultdict(deque)

    def check(self, key: str) -> None:
        now = time.monotonic()
        # Bound memory even when requests use many distinct identifiers.
        if len(self.hits) >= 10000:
            for old in [k for k, v in self.hits.items() if not v or v[-1] < now - self.window_s]:
                del self.hits[old]
            if key not in self.hits and len(self.hits) >= 10000:
                raise HTTPException(429, "Troppi tentativi, riprova tra qualche minuto")
        q = self.hits[key]
        while q and q[0] < now - self.window_s:
            q.popleft()
        if len(q) >= self.max_hits:
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Troppi tentativi, riprova tra qualche minuto")
        q.append(now)


login_limiter = RateLimiter(max_hits=10, window_s=300)
pin_limiter = RateLimiter(max_hits=8, window_s=300)


def client_ip(request: Request) -> str:
    # ASGI's peer is resolved by the server's trusted-proxy configuration.
    # Never accept an arbitrary client-supplied X-Real-IP for rate limiting.
    return request.client.host if request.client else ""


# ---------- cookie helpers ----------
def _set_cookie(response: Response, name: str, value: str, max_age: int) -> None:
    response.set_cookie(
        name,
        value,
        max_age=max_age,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        path="/",
    )


async def create_session(
    db: AsyncSession, response: Response, user: User, request: Request, mfa_verified: bool, auth_method: str = "password"
) -> Session:
    hours = settings.session_hours_child if user.role == Role.child else settings.session_hours_adult
    if auth_method == "passkey" and user.role != Role.child:
        hours = max(1, settings.session_days_passkey) * 24
    lifetime = timedelta(hours=hours) if mfa_verified else timedelta(minutes=10)
    sess = Session(
        user_id=user.id,
        auth_method=auth_method,
        expires_at=datetime.now(timezone.utc) + lifetime,
        mfa_verified=mfa_verified,
        user_agent=request.headers.get("user-agent", "")[:300],
        ip=client_ip(request),
        device_id=request.cookies.get(DEVICE_COOKIE),
    )
    db.add(sess)
    await db.commit()
    # con la passkey la scadenza vera e' quella del server, che si rinnova a ogni uso: il cookie dura quanto il browser concede
    _set_cookie(response, SESSION_COOKIE, sess.id, 400 * 24 * 3600 if auth_method == "passkey" else int(lifetime.total_seconds()))
    return sess


def set_device_cookie(response: Response, device_id: str) -> None:
    _set_cookie(response, DEVICE_COOKIE, device_id, 365 * 24 * 3600)


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")


async def get_trusted_device(request: Request, db: AsyncSession) -> TrustedDevice | None:
    dev_id = request.cookies.get(DEVICE_COOKIE)
    if not dev_id:
        return None
    dev = await db.get(TrustedDevice, dev_id)
    if not dev or dev.revoked:
        return None
    dev.last_seen_at = datetime.now(timezone.utc)
    return dev


# ---------- dipendenze FastAPI ----------
class AuthContext:
    def __init__(self, user: User, session: Session):
        self.user = user
        self.session = session


async def _load_session(request: Request, db: AsyncSession) -> AuthContext | None:
    sid = request.cookies.get(SESSION_COOKIE)
    if not sid:
        return None
    sess = await db.get(Session, sid)
    if not sess:
        return None
    now = datetime.now(timezone.utc)
    if sess.expires_at.replace(tzinfo=timezone.utc) < now:
        await db.delete(sess)
        await db.commit()
        return None
    user = await db.get(User, sess.user_id)
    if not user or not user.is_active:
        return None
    if sess.auth_method == "pin":
        dev = await db.get(TrustedDevice, sess.device_id) if sess.device_id else None
        if user.role != Role.child or not dev or dev.revoked or dev.family_id != user.family_id:
            return None
    if sess.auth_method == "passkey" and user.role != Role.child:
        # come un'app: chi entra con Face ID / impronta resta dentro finche' usa Emily (scade dopo N giorni di inattivita')
        full = timedelta(days=max(1, settings.session_days_passkey))
        if sess.expires_at.replace(tzinfo=timezone.utc) - now < full - timedelta(days=1):
            sess.expires_at = now + full
            await db.commit()
    if sess.acting_child_id:
        # The original administrator session remains the authority for returning.
        if user.role != Role.admin or not sess.mfa_verified:
            return None
        if not (request.method == "POST" and request.url.path == "/api/auth/acting-child/stop"):
            child = await db.get(User, sess.acting_child_id)
            if not child or not child.is_active or child.role != Role.child or child.family_id != user.family_id:
                raise HTTPException(403, "Profilo bambino non più disponibile: torna amministratore")
            user = child
    from ..services.activity import remember_actor
    remember_actor(request, user.id, user.family_id)
    return AuthContext(user, sess)


async def optional_auth(request: Request, db: AsyncSession = Depends(get_db)) -> AuthContext | None:
    return await _load_session(request, db)


def check_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    allowed = {settings.webauthn_origin.rstrip("/")}
    if not settings.cookie_secure:
        allowed.update({str(request.base_url).rstrip("/"), "http://localhost:5173", "http://127.0.0.1:5173"})
    if origin is not None and origin.rstrip("/") not in allowed:
        raise HTTPException(403, "Origine della richiesta non consentita")
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "Richiesta cross-site non consentita")


def _csrf_check(request: Request) -> None:
    # Difesa in profondità oltre a SameSite=Strict: le richieste mutanti devono avere l'header custom.
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        check_origin(request)
        if request.headers.get("x-requested-with") != "FamilAI":
            raise HTTPException(status.HTTP_403_FORBIDDEN, "CSRF check fallito")


async def require_auth(request: Request, db: AsyncSession = Depends(get_db)) -> AuthContext:
    """Utente autenticato, con MFA completata (se richiesta)."""
    ctx = await _load_session(request, db)
    if not ctx:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Non autenticato")
    _csrf_check(request)
    if ctx.session.screen_locked and not (request.method == "GET" and request.url.path.startswith("/api/branding/profile")):
        raise HTTPException(423,"Schermo bloccato")
    if ctx.user.role != Role.child and not ctx.session.mfa_verified:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Verifica a due fattori richiesta", headers={"X-MFA": "required"})
    if ctx.user.role != Role.child and settings.require_mfa_for_adults:
        from ..config import mfa_optional_on
        if not (mfa_optional_on() and (ctx.user.mfa_choice or "") == "skip") and not await user_has_second_factor(db, ctx.user):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Attiva un secondo fattore (passkey o app authenticator) prima di continuare",
                                headers={"X-MFA": "enroll"})
    return ctx


async def require_pre_mfa(request: Request, db: AsyncSession = Depends(get_db)) -> AuthContext:
    """Sessione creata dopo la password ma prima della passkey (solo per completare la MFA)."""
    ctx = await _load_session(request, db)
    if not ctx:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Non autenticato")
    _csrf_check(request)
    if ctx.session.screen_locked and request.url.path != "/api/auth/me" and not request.url.path.startswith("/api/auth/unlock/"):
        raise HTTPException(423,"Schermo bloccato")
    return ctx


async def require_adult_for_enrollment(ctx: AuthContext = Depends(require_pre_mfa)) -> AuthContext:
    """Adulto con password (e MFA) verificate ma senza obbligo di passkey: serve per registrare la passkey stessa."""
    if ctx.user.role == Role.child:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Funzione riservata agli adulti")
    if not ctx.session.mfa_verified:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Verifica a due fattori richiesta", headers={"X-MFA": "required"})
    return ctx


async def require_adult(ctx: AuthContext = Depends(require_auth)) -> AuthContext:
    if ctx.user.role == Role.child:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Funzione riservata agli adulti")
    return ctx


async def require_admin(ctx: AuthContext = Depends(require_auth)) -> AuthContext:
    if ctx.user.role != Role.admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Funzione riservata all'amministratore")
    return ctx


def passkey_enrollment_needed(ctx: "AuthContext", has_second_factor: bool) -> bool:
    """A Google login does not attest that Google's own second factor was used."""
    if ctx.user.role == Role.child or not settings.require_mfa_for_adults:
        return False
    from ..config import mfa_optional_on
    if mfa_optional_on() and (ctx.user.mfa_choice or "") == "skip":
        return False   # ha scelto di non usare il 2FA (puo' attivarlo dalle impostazioni)
    return not has_second_factor


async def revoke_user_access(db: AsyncSession, user_id: str, keep_session: str | None = None) -> None:
    from sqlalchemy import delete, update
    from ..models import SyncDevice, SyncPairingCode
    query = delete(Session).where(Session.user_id == user_id)
    if keep_session:
        query = query.where(Session.id != keep_session)
    await db.execute(query)
    await db.execute(update(SyncDevice).where(SyncDevice.user_id == user_id).values(revoked=True))
    await db.execute(delete(SyncPairingCode).where(SyncPairingCode.user_id == user_id))


async def complete_mfa(db: AsyncSession, response: Response, ctx: AuthContext, request: Request) -> None:
    """Do not upgrade a potentially copied pre-MFA session identifier in place."""
    if ctx.session.mfa_verified:
        return
    method = ctx.session.auth_method
    await db.delete(ctx.session)
    await db.flush()
    ctx.session = await create_session(db, response, ctx.user, request, True, method)


async def user_has_passkey(db: AsyncSession, user: User) -> bool:
    from ..models import WebAuthnCredential

    r = await db.execute(select(WebAuthnCredential.id).where(WebAuthnCredential.user_id == user.id).limit(1))
    return r.scalar_one_or_none() is not None


def user_has_totp(user: User) -> bool:
    return bool(user.totp_secret)


async def user_has_second_factor(db: AsyncSession, user: User) -> bool:
    return user_has_totp(user) or await user_has_passkey(db, user)
