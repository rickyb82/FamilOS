"""Accesso con Google (OpenID Connect, Authorization Code + PKCE).

- Client OAuth configurato dal pannello (google_client_id / google_client_secret), indipendente dall'account SMTP.
- Nessuna auto-registrazione: l'email Google deve corrispondere a un membro adulto gia' creato dall'amministratore.
- L'accesso Google vale come secondo fattore (l'account Google ha la propria protezione): niente passkey richiesta.
"""
from __future__ import annotations

import base64
import hashlib
import secrets
import time
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import get_db
from ..models import Role, User
from ..security.sessions import client_ip, create_session, login_limiter
from .auth import audit

settings = get_settings()
router = APIRouter(prefix="/auth/google", tags=["auth"])

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"

_pending: dict[str, tuple[str, float]] = {}   # state -> (code_verifier, scadenza)


def enabled() -> bool:
    return bool(settings.google_client_id and settings.google_client_secret)


def redirect_uri() -> str:
    return f"{settings.webauthn_origin.rstrip('/')}/api/auth/google/callback"


def _fail(reason: str) -> RedirectResponse:
    return RedirectResponse(f"/login?error={reason}", status_code=302)


@router.get("/start")
async def start(request: Request):
    if not enabled():
        return _fail("google_non_configurato")
    login_limiter.check("google:" + client_ip(request))
    now = time.time()
    for k in [k for k, (_, exp) in _pending.items() if exp < now]:
        _pending.pop(k, None)
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    _pending[state] = (verifier, now + 600)
    params = {
        "client_id": settings.google_client_id, "redirect_uri": redirect_uri(), "response_type": "code",
        "scope": "openid email profile", "state": state, "code_challenge": challenge, "code_challenge_method": "S256",
        "prompt": "select_account", "access_type": "online",
    }
    return RedirectResponse(f"{AUTH_URL}?{urlencode(params)}", status_code=302)


@router.get("/callback")
async def callback(request: Request, db: AsyncSession = Depends(get_db), code: str = "", state: str = "", error: str = ""):
    if error:
        return _fail("google_annullato")
    item = _pending.pop(state, None)
    if not item or item[1] < time.time():
        return _fail("google_stato_non_valido")
    verifier = item[0]
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            tok = await c.post(TOKEN_URL, data={
                "code": code, "client_id": settings.google_client_id, "client_secret": settings.google_client_secret,
                "redirect_uri": redirect_uri(), "grant_type": "authorization_code", "code_verifier": verifier})
            tok.raise_for_status()
            access = tok.json()["access_token"]
            info = (await c.get(USERINFO_URL, headers={"Authorization": f"Bearer {access}"})).json()
    except Exception:
        return _fail("google_errore_token")
    email = (info.get("email") or "").lower()
    if not email or not info.get("email_verified", False):
        return _fail("google_email_non_verificata")
    user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if not user or not user.is_active or user.role == Role.child:
        await audit(db, "google_login_unknown", request, None, email)
        return _fail("google_utente_non_registrato")
    resp = RedirectResponse("/", status_code=302)
    await create_session(db, resp, user, request, mfa_verified=True, auth_method="google")
    user.failed_logins, user.locked_until = 0, None
    if not user.avatar or user.avatar == "🙂":
        pass  # potremmo salvare la foto del profilo in futuro
    await db.commit()
    await audit(db, "login_google", request, user, info.get("sub", ""))
    return resp
