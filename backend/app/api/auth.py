from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import delete, select, update, case
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import get_db
from ..models import AuditLog, Family, Role, Session, TrustedDevice, User, WebAuthnCredential
from ..policy.permissions import effective_policy
from pydantic import BaseModel, Field
from ..schemas import (
    ChangePasswordRequest,
    LoginRequest,
    MeOut,
    PinLoginRequest,
    RecoveryLoginRequest,
    TrustDeviceRequest,
    UserOut,
    WebAuthnVerifyRequest,
)
from ..security import totp as totp_svc
from ..security import webauthn_svc
from ..security.passwords import consume_recovery_code, generate_recovery_codes, hash_secret, verify_secret
from ..security.sessions import (
    AuthContext,
    clear_session_cookie,
    client_ip,
    create_session,
    get_trusted_device,
    login_limiter,
    pin_limiter,
    require_adult,
    require_adult_for_enrollment,
    require_auth,
    require_pre_mfa,
    set_device_cookie,
    user_has_passkey,
    user_has_totp,
    user_has_second_factor,
    passkey_enrollment_needed,
    _csrf_check,
    complete_mfa,
    revoke_user_access,
)
from ..providers.registry import available_providers

settings = get_settings()
router = APIRouter(prefix="/auth", tags=["auth"], dependencies=[Depends(_csrf_check)])


async def audit(db: AsyncSession, event: str, request: Request, user: User | None = None, detail: str = "") -> None:
    from ..services.activity import event_ok
    db.add(AuditLog(family_id=user.family_id if user else None, user_id=user.id if user else None, app="emily",
                    ok=event_ok(event), event=event, detail=detail[:500], ip=client_ip(request)))
    await db.commit()

    from ..services import security_alerts
    try:
        await security_alerts.enqueue(db, event, user, client_ip(request), detail)
    except Exception:
        import logging
        await db.rollback()
        logging.getLogger(__name__).exception("Impossibile accodare avviso sicurezza")


def locked(user: User) -> bool:
    return bool(user.locked_until and user.locked_until.replace(tzinfo=timezone.utc) > datetime.now(timezone.utc))


async def register_failure(db: AsyncSession, user: User) -> None:
    await db.execute(update(User).where(User.id == user.id).values(
        locked_until=case((User.failed_logins >= 4, datetime.now(timezone.utc)+timedelta(minutes=15)), else_=User.locked_until),
        failed_logins=case((User.failed_logins >= 4, 0), else_=User.failed_logins+1)
    ).execution_options(synchronize_session=False))
    await db.commit()
    await db.refresh(user)


async def user_out(db: AsyncSession, user: User) -> UserOut:
    if user.departments is None:   # colonna aggiunta dopo: le righe vecchie sono NULL
        user.departments = []
    if user.admin_apps is None:
        user.admin_apps = []
    out = UserOut.model_validate(user)
    from ..services.apps import role_label
    from ..services.quota import quota_bytes as _qb, used_bytes as _ub
    out.role_label = role_label(user)
    out.quota_effective = _qb(user)
    out.storage_used = await _ub(db, user.id)
    out.has_passkey = await user_has_passkey(db, user)
    out.has_totp = user_has_totp(user)
    out.effective_policy = effective_policy(user)
    return out


# ---------------- login adulti: password -> passkey ----------------
@router.post("/login")
async def login(body: LoginRequest, request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    login_limiter.check(client_ip(request))
    user = (await db.execute(select(User).where(User.email == body.email.lower()))).scalar_one_or_none()
    if not user or not user.is_active or user.role == Role.child or locked(user):
        await audit(db, "login_unknown" if not user else "login_refused", request, user, body.email[:120] if not user else
                    ("account bloccato" if locked(user) else "account disattivato" if not user.is_active else "bambino: accesso con PIN"))
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Credenziali non valide")
    if not verify_secret(user.password_hash, body.password):
        await register_failure(db, user)
        await audit(db, "login_failed", request, user)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Credenziali non valide")
    user.failed_logins = 0
    has_pk = await user_has_passkey(db, user)
    has_totp = user_has_totp(user)
    has_2fa = has_pk or has_totp
    # Se ha un secondo fattore deve usarlo; se non ce l'ha entra ma dovra' attivarne uno (se REQUIRE_MFA_FOR_ADULTS).
    await create_session(db, response, user, request, mfa_verified=not has_2fa)
    await audit(db, "login_password", request, user, "mfa_required" if has_2fa else "no_second_factor")
    from ..config import mfa_optional_on
    must_enroll = (not has_2fa) and settings.require_mfa_for_adults and not (mfa_optional_on() and (user.mfa_choice or "") == "skip")
    return {"status": "mfa_required" if has_2fa else "ok", "must_enroll_passkey": must_enroll,
            "methods": {"passkey": has_pk, "totp": has_totp, "passkeys_enabled": __import__("app.config", fromlist=["passkeys_on"]).passkeys_on()}}


# ---------------- accesso con la sola passkey (Face ID / impronta), soprattutto dall'app sul telefono ----------------
class PasskeyLoginIn(BaseModel):
    key: str = Field(max_length=64)
    credential: dict


@router.post("/passkey/options")
async def passkey_login_options(request: Request):
    from ..config import passkeys_on
    login_limiter.check(client_ip(request))
    if not passkeys_on():
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Le passkey non sono attive su questo server")
    key, options = webauthn_svc.discoverable_options()
    return {"key": key, "options": options}


@router.post("/passkey/verify")
async def passkey_login_verify(body: PasskeyLoginIn, request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    from ..config import passkeys_on
    login_limiter.check(client_ip(request))
    if not passkeys_on():
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Le passkey non sono attive su questo server")
    try:
        user, _cred = await webauthn_svc.discoverable_verify(db, body.key, body.credential)
    except Exception as e:
        await audit(db, "login_passkey_failed", request, None, str(e)[:200])
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"Accesso con la passkey non riuscito: {e}")
    if not user.is_active or user.role == Role.child or locked(user):
        await audit(db, "login_refused", request, user, "account bloccato" if locked(user) else "account non ammesso")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Accesso non consentito per questo account")
    user.failed_logins = 0
    await create_session(db, response, user, request, mfa_verified=True, auth_method="passkey")
    await audit(db, "login_passkey", request, user)
    return {"status": "ok"}


@router.post("/webauthn/authenticate/options")
async def webauthn_auth_options(ctx: AuthContext = Depends(require_pre_mfa), db: AsyncSession = Depends(get_db)):
    return await webauthn_svc.authentication_options(db, ctx.user, ctx.session.id)


@router.post("/webauthn/authenticate/verify")
async def webauthn_auth_verify(body: WebAuthnVerifyRequest, request: Request, response: Response,
                               ctx: AuthContext = Depends(require_pre_mfa), db: AsyncSession = Depends(get_db)):
    try:
        await webauthn_svc.authentication_verify(db, ctx.user, ctx.session.id, body.credential)
    except Exception as e:
        await audit(db, "mfa_failed", request, ctx.user, str(e))
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"Verifica passkey fallita: {e}")
    await complete_mfa(db, response, ctx, request)
    await db.commit()
    await audit(db, "mfa_passkey_ok", request, ctx.user)
    return {"status": "ok"}


@router.post("/recovery")
async def recovery_login(body: RecoveryLoginRequest, request: Request, response: Response,
                         ctx: AuthContext = Depends(require_pre_mfa), db: AsyncSession = Depends(get_db)):
    login_limiter.check("rec:" + client_ip(request))
    remaining = consume_recovery_code(ctx.user.recovery_codes or [], body.code)
    if remaining is None:
        await audit(db, "recovery_failed", request, ctx.user)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Codice di recupero non valido")
    result = await db.execute(update(User).where(User.id == ctx.user.id,
        User.recovery_codes == ctx.user.recovery_codes).values(recovery_codes=remaining)
        .execution_options(synchronize_session=False))
    if result.rowcount != 1:
        await db.rollback()
        raise HTTPException(401, "Codice di recupero già utilizzato")
    await complete_mfa(db, response, ctx, request)
    await db.commit()
    await audit(db, "recovery_ok", request, ctx.user, f"rimasti {len(remaining)}")
    return {"status": "ok", "remaining_codes": len(remaining)}


# ---------------- registrazione passkey (dopo il login) ----------------
@router.post("/webauthn/register/options")
async def webauthn_reg_options(ctx: AuthContext = Depends(require_adult_for_enrollment), db: AsyncSession = Depends(get_db)):
    from ..config import passkeys_on
    if not passkeys_on():
        raise HTTPException(400, "Le passkey sono disattivate su questo server: usa l'app authenticator")
    return await webauthn_svc.registration_options(db, ctx.user, ctx.session.id)


@router.post("/webauthn/register/verify")
async def webauthn_reg_verify(body: WebAuthnVerifyRequest, request: Request,
                              ctx: AuthContext = Depends(require_adult_for_enrollment), db: AsyncSession = Depends(get_db)):
    completing_login = passkey_enrollment_needed(ctx, await user_has_second_factor(db, ctx.user))
    try:
        cred = await webauthn_svc.registration_verify(db, ctx.user, ctx.session.id, body.credential, body.name)
    except Exception as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Registrazione passkey fallita: {e}")
    ctx.session.mfa_verified = True
    codes: list[str] = []
    if not ctx.user.recovery_codes:
        codes, hashed = generate_recovery_codes()
        ctx.user.recovery_codes = hashed
    await db.commit()
    await audit(db, "passkey_registered", request, ctx.user, cred.name)
    if completing_login:
        await audit(db, "login_enrollment_completed", request, ctx.user)
    return {"status": "ok", "credential_id": cred.id, "recovery_codes": codes}


@router.get("/webauthn/credentials")
async def list_credentials(ctx: AuthContext = Depends(require_adult), db: AsyncSession = Depends(get_db)):
    creds = (await db.execute(select(WebAuthnCredential).where(WebAuthnCredential.user_id == ctx.user.id))).scalars().all()
    return [{"id": c.id, "name": c.name, "created_at": c.created_at, "last_used_at": c.last_used_at} for c in creds]


@router.delete("/webauthn/credentials/{cred_id}")
async def delete_credential(cred_id: str, request: Request, ctx: AuthContext = Depends(require_adult),
                            db: AsyncSession = Depends(get_db)):
    cred = await db.get(WebAuthnCredential, cred_id)
    if not cred or cred.user_id != ctx.user.id:
        raise HTTPException(404)
    await db.delete(cred)
    await db.commit()
    await audit(db, "passkey_deleted", request, ctx.user, cred.name)
    return {"status": "ok"}


@router.post("/recovery-codes/regenerate")
async def regenerate_recovery(request: Request, ctx: AuthContext = Depends(require_adult), db: AsyncSession = Depends(get_db)):
    codes, hashed = generate_recovery_codes()
    ctx.user.recovery_codes = hashed
    await db.commit()
    await audit(db, "recovery_regenerated", request, ctx.user)
    return {"recovery_codes": codes}


@router.post("/password")
async def change_password(body: ChangePasswordRequest, request: Request, ctx: AuthContext = Depends(require_adult),
                          db: AsyncSession = Depends(get_db)):
    if not verify_secret(ctx.user.password_hash, body.current_password):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Password attuale errata")
    ctx.user.password_hash = hash_secret(body.new_password)
    # invalida le altre sessioni
    await revoke_user_access(db, ctx.user.id, keep_session=ctx.session.id)
    await db.commit()
    await audit(db, "password_changed", request, ctx.user)
    return {"status": "ok"}


# ---------------- dispositivi fidati + login bambini con PIN ----------------
@router.get("/device")
async def device_status(request: Request, db: AsyncSession = Depends(get_db)):
    dev = await get_trusted_device(request, db)
    await db.commit()
    if not dev:
        return {"trusted": False, "children": []}
    kids = (await db.execute(select(User).where(User.family_id == dev.family_id, User.role == Role.child,
                                                 User.is_active == True))).scalars().all()  # noqa: E712
    return {"trusted": True, "device_name": dev.name,
            "children": [{"id": k.id, "display_name": k.display_name, "avatar": k.avatar} for k in kids]}


@router.post("/device/trust")
async def trust_device(body: TrustDeviceRequest, request: Request, response: Response,
                       ctx: AuthContext = Depends(require_adult), db: AsyncSession = Depends(get_db)):
    dev = TrustedDevice(family_id=ctx.user.family_id, name=body.name, approved_by=ctx.user.id)
    db.add(dev)
    await db.commit()
    set_device_cookie(response, dev.id)
    await audit(db, "device_trusted", request, ctx.user, body.name)
    return {"status": "ok", "device_id": dev.id}


@router.post("/pin-login")
async def pin_login(body: PinLoginRequest, request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    pin_limiter.check(client_ip(request))
    dev = await get_trusted_device(request, db)
    if not dev:
        await audit(db, "pin_refused", request)
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Questo dispositivo non è autorizzato: chiedi a un genitore di approvarlo")
    user = await db.get(User, body.user_id)
    if not user or user.family_id != dev.family_id or user.role != Role.child or not user.is_active or locked(user):
        await audit(db, "pin_refused", request, user if user and user.family_id == dev.family_id else None)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "PIN errato")
    if not verify_secret(user.pin_hash, body.pin):
        await register_failure(db, user)
        await audit(db, "pin_failed", request, user)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "PIN errato")
    user.failed_logins = 0
    await create_session(db, response, user, request, mfa_verified=True, auth_method="pin")
    await audit(db, "login_pin", request, user, dev.name)
    return {"status": "ok"}


# ---------------- sessione ----------------
@router.get("/me", response_model=MeOut)
async def me(ctx: AuthContext = Depends(require_pre_mfa), db: AsyncSession = Depends(get_db)):
    fam = await db.get(Family, ctx.user.family_id)
    u = await user_out(db, ctx.user)
    pol = u.effective_policy
    from ..services import model_catalog
    provs = model_catalog.catalog(ctx.user, pol)   # utenti: solo i modelli abilitati dall'admin; admin: tutti (o il suo blocco)
    if ctx.user.role == Role.child and pol.get("provider"):
        provs = [p for p in provs if p["id"] == pol["provider"]] or provs
    mfa_required = ctx.user.role != Role.child and (
        not ctx.session.mfa_verified or passkey_enrollment_needed(ctx, u.has_passkey or u.has_totp)
    )
    from ..config import mfa_optional_on, org_mode_on, passkeys_on
    from ..tools.mcp_client import mcp_manager
    adult = ctx.user.role != Role.child
    from ..services.apps import admin_apps as _admin_apps, user_apps as _user_apps
    apps = _user_apps(ctx.user)   # software abilitati per questo utente (super admin: tutti)
    on = lambda app: app in apps  # noqa: E731
    features = {f"{app}_web": bool(getattr(settings, f"{app}_web")) and on(app) for app in ("wordcraft", "gridcraft", "pdfcraft", "deckcraft", "ideacraft")}
    features.update({f"{app}_mcp": mcp_manager.is_connected(app) and on(app) for app in ("wordcraft", "gridcraft", "pdfcraft", "deckcraft", "ideacraft", "mailcraft")})
    features.update({"mailcraft_web": bool(settings.mailcraft_url and settings.mailcraft_token) and on("mailcraft"),
                     "messaggi": on("messaggi"), "archivio": on("archivio"), "apps": sorted(apps),
                     "admin_apps": sorted(_admin_apps(ctx.user)), "org_mode": False,
                     "passkeys": passkeys_on(), "mfa_optional": mfa_optional_on(),
                     "debug_llm": str(settings.debug_llm).lower() == "all" or str(settings.debug_llm).lower() == "admins" and ctx.user.role == Role.admin})

    return MeOut(acting_as_child=bool(ctx.session.acting_child_id), session_locked=ctx.session.screen_locked, user=u, family_name=fam.name if fam else "", mfa_verified=ctx.session.mfa_verified,
                 mfa_required=mfa_required, providers=provs, tools=pol["allowed_tools"], features=features,
                 model_lock=model_catalog.admin_lock(ctx.user), prefs=ctx.user.prefs or {})


@router.post("/mfa/choice")
async def mfa_choice(body: dict, ctx: AuthContext = Depends(require_adult_for_enrollment), db: AsyncSession = Depends(get_db)):
    """Primo accesso con 2FA facoltativo: "skip" = entra senza secondo fattore (attivabile dopo dalle impostazioni),
    "" = torna a chiederlo al prossimo accesso."""
    from ..config import mfa_optional_on
    choice = str(body.get("choice") or "")
    if choice not in ("", "skip"):
        raise HTTPException(400, "Scelta non valida")
    if choice == "skip" and not mfa_optional_on():
        raise HTTPException(400, "Su questo server il secondo fattore e' obbligatorio")
    ctx.user.mfa_choice = choice
    await db.commit()
    return {"ok": True, "mfa_choice": choice}


@router.post("/logout")
async def logout(request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    from ..security.sessions import SESSION_COOKIE

    sid = request.cookies.get(SESSION_COOKIE)
    if sid:
        sess = await db.get(Session, sid)
        if sess:
            u = await db.get(User, sess.user_id)
            if u:
                await audit(db, "logout", request, u)
        await db.execute(delete(Session).where(Session.id == sid))
        await db.commit()
    clear_session_cookie(response)
    return {"status": "ok"}


@router.get("/sessions")
async def my_sessions(ctx: AuthContext = Depends(require_auth), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(Session).where(Session.user_id == ctx.user.id))).scalars().all()
    return [{"id": s.id[:8], "current": s.id == ctx.session.id, "created_at": s.created_at, "ip": s.ip,
             "user_agent": s.user_agent} for s in rows]


@router.post("/sessions/revoke-others")
async def revoke_others(ctx: AuthContext = Depends(require_auth), db: AsyncSession = Depends(get_db)):
    await db.execute(delete(Session).where(Session.user_id == ctx.user.id, Session.id != ctx.session.id))
    await db.commit()
    return {"status": "ok"}


# ---------------- email: password dimenticata, invito, codice come secondo fattore ----------------
from pydantic import BaseModel as _BM  # noqa: E402
from pydantic import EmailStr  # noqa: E402

from ..services import email_codes, mailer  # noqa: E402


class ForgotRequest(_BM):
    email: EmailStr


class ResetRequest(_BM):
    email: EmailStr
    code: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=10, max_length=200)


class CodeRequest(_BM):
    code: str = Field(min_length=1, max_length=128)


from typing import Literal

class AppearanceIn(BaseModel):
    theme: Literal["system","light","dark"] = "system"
    accent: Literal["lavender","blue","green","rose","amber"] = "lavender"
    desktop_style: Literal["aurora","oceano","tramonto"] = "aurora"
    effects: Literal["full","reduced","off"] = "full"
    wallpaper: Literal["aurora","oceano","tramonto","soft","ocean","forest","sunset","midnight","custom"] = "aurora"
    lock_after_minutes: Literal[0,1,5,15,30] = 5

class PrefsIn(BaseModel):
    default_chat_model: str | None = Field(default=None, max_length=300)
    tools_off: list[str] | None = Field(default=None,max_length=50)
    appearance: AppearanceIn | None = None


@router.put("/prefs")
async def set_prefs(body: PrefsIn, ctx: AuthContext = Depends(require_auth), db: AsyncSession = Depends(get_db)):
    """Preferenze personali: gli strumenti dell'assistente che l'utente tiene spenti (Impostazioni › Strumenti dell'assistente)."""
    prefs = dict(ctx.user.prefs or {})
    if body.default_chat_model is not None:
        if ctx.user.role != Role.admin:
            raise HTTPException(403, "Preferenza disponibile per gli amministratori")
        from ..services import model_catalog
        available = {model_catalog.key(p["id"], m["id"]) for p in model_catalog.catalog(ctx.user, effective_policy(ctx.user)) for m in p["models"]}
        if body.default_chat_model and body.default_chat_model not in available:
            raise HTTPException(400, "Modello non disponibile")
        prefs["default_chat_model"] = body.default_chat_model
    if body.tools_off is not None:
        prefs["tools_off"] = sorted({t[:40] for t in body.tools_off if t})
    if body.appearance is not None:
        prefs["appearance"] = body.appearance.model_dump()
    ctx.user.prefs = prefs
    await db.commit()
    return {"prefs": prefs}


@router.get("/features")
async def features():
    """Funzioni disponibili alla schermata di login (nessuna autenticazione)."""
    from . import google_auth

    from ..config import mfa_optional_on, org_mode_on, passkeys_on
    return {"email": mailer.email_enabled(),
            "email_mfa": mailer.email_enabled() and str(settings.email_mfa_fallback).lower() in ("1", "true", "yes"),
            "google": google_auth.enabled(), "org_mode": org_mode_on(), "app_name": settings.app_name,
            "passkeys": passkeys_on(), "mfa_optional": mfa_optional_on()}


@router.post("/forgot")
async def forgot_password(body: ForgotRequest, request: Request, db: AsyncSession = Depends(get_db)):
    """Invia un codice di reset. Risponde sempre OK per non rivelare quali email esistono."""
    login_limiter.check("forgot:" + client_ip(request))
    if not mailer.email_enabled():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Invio email non configurato: chiedi all'amministratore")
    user = (await db.execute(select(User).where(User.email == body.email.lower()))).scalar_one_or_none()
    if user and user.is_active and user.role != Role.child:
        code = await email_codes.issue(db, user, "reset")
        try:
            await mailer.send_code(user.email, user.display_name, code, "reset")
        except Exception as e:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Invio email fallito: {e}")
        await audit(db, "reset_code_sent", request, user)
    return {"status": "ok"}


@router.post("/reset")
async def reset_password(body: ResetRequest, request: Request, db: AsyncSession = Depends(get_db)):
    """Imposta una nuova password con il codice ricevuto via email (reset o invito)."""
    login_limiter.check("reset:" + client_ip(request))
    if len(body.new_password) < 10:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "La password deve avere almeno 10 caratteri")
    user = (await db.execute(select(User).where(User.email == body.email.lower()))).scalar_one_or_none()
    if not user or not user.is_active or user.role == Role.child or not await email_codes.verify(db, user, ("reset", "invite"), body.code):
        await audit(db, "reset_failed", request, user)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Codice non valido o scaduto")
    user.password_hash = hash_secret(body.new_password)
    user.failed_logins, user.locked_until = 0, None
    await revoke_user_access(db, user.id)
    await db.commit()
    await audit(db, "password_reset", request, user)
    return {"status": "ok"}


@router.post("/mfa/email/send")
async def mfa_email_send(request: Request, ctx: AuthContext = Depends(require_pre_mfa), db: AsyncSession = Depends(get_db)):
    """Secondo fattore alternativo alla passkey: codice via email (se abilitato)."""
    if not (mailer.email_enabled() and str(settings.email_mfa_fallback).lower() in ("1", "true", "yes")):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Codice via email non abilitato")
    login_limiter.check("mfa:" + ctx.user.id)
    code = await email_codes.issue(db, ctx.user, "mfa")
    try:
        await mailer.send_code(ctx.user.email, ctx.user.display_name, code, "mfa")
    except Exception as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Invio email fallito: {e}")
    return {"status": "ok", "hint": mask_email(ctx.user.email)}


@router.post("/mfa/email/verify")
async def mfa_email_verify(body: CodeRequest, request: Request, response: Response, ctx: AuthContext = Depends(require_pre_mfa),
                           db: AsyncSession = Depends(get_db)):
    if not (mailer.email_enabled() and str(settings.email_mfa_fallback).lower() in ("1", "true", "yes")):
        raise HTTPException(403, "Codice via email non abilitato")
    login_limiter.check("mfa-verify:" + ctx.user.id)
    if not await email_codes.verify(db, ctx.user, ("mfa",), body.code):
        await audit(db, "mfa_email_failed", request, ctx.user)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Codice non valido o scaduto")
    await complete_mfa(db, response, ctx, request)
    await db.commit()
    await audit(db, "mfa_email_ok", request, ctx.user)
    return {"status": "ok"}


def mask_email(e: str | None) -> str:
    if not e or "@" not in e:
        return ""
    name, dom = e.split("@", 1)
    return f"{name[:2]}…@{dom}"


# ---------------- app authenticator (TOTP): alternativa alla passkey ----------------
@router.post("/totp/setup")
async def totp_setup(ctx: AuthContext = Depends(require_adult_for_enrollment), db: AsyncSession = Depends(get_db)):
    """Genera un nuovo segreto (in attesa di conferma) e restituisce QR + segreto da digitare."""
    from ..services.settings_store import encrypt
    secret = totp_svc.new_secret()
    ctx.user.totp_pending = encrypt(secret)
    await db.commit()
    account = ctx.user.email or ctx.user.username
    fam = await db.get(Family, ctx.user.family_id)
    issuer = f"{settings.app_name} {fam.name}" if fam and fam.name else settings.app_name
    uri = totp_svc.provisioning_uri(secret, account, issuer)
    return {"secret": secret, "uri": uri, "qr_svg": totp_svc.qr_svg(uri)}


@router.post("/totp/confirm")
async def totp_confirm(body: CodeRequest, request: Request, ctx: AuthContext = Depends(require_adult_for_enrollment),
                       db: AsyncSession = Depends(get_db)):
    """Conferma il segreto con il primo codice generato dall'app: da qui in poi e' un secondo fattore valido."""
    from ..services.settings_store import decrypt
    completing_login = passkey_enrollment_needed(ctx, await user_has_second_factor(db, ctx.user))
    login_limiter.check("totp-confirm:" + ctx.user.id)
    if not ctx.user.totp_pending:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Nessuna configurazione in corso: ricomincia")
    if not totp_svc.verify(decrypt(ctx.user.totp_pending), body.code):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Codice non valido: controlla l'ora del telefono e riprova")
    ctx.user.totp_secret = ctx.user.totp_pending
    ctx.user.totp_pending = None
    ctx.user.totp_last_counter = -1
    ctx.session.mfa_verified = True
    codes: list[str] = []
    if not ctx.user.recovery_codes:
        codes, hashed = generate_recovery_codes()
        ctx.user.recovery_codes = hashed
    await db.commit()
    await totp_svc.consume(db, ctx.user, body.code)
    await audit(db, "totp_enabled", request, ctx.user)
    if completing_login:
        await audit(db, "login_enrollment_completed", request, ctx.user)
    return {"status": "ok", "recovery_codes": codes}


@router.post("/totp/verify")
async def totp_verify(body: CodeRequest, request: Request, response: Response, ctx: AuthContext = Depends(require_pre_mfa),
                      db: AsyncSession = Depends(get_db)):
    """Secondo fattore al login: codice a 6 cifre dell'app authenticator."""
    from ..services.settings_store import decrypt
    if not ctx.user.totp_secret:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "App authenticator non configurata")
    login_limiter.check("totp:" + ctx.user.id)
    if not await totp_svc.consume(db, ctx.user, body.code):
        await audit(db, "mfa_totp_failed", request, ctx.user)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Codice non valido")
    await complete_mfa(db, response, ctx, request)
    await db.commit()
    await audit(db, "mfa_totp_ok", request, ctx.user)
    return {"status": "ok"}


@router.delete("/totp")
async def totp_disable(request: Request, ctx: AuthContext = Depends(require_adult), db: AsyncSession = Depends(get_db)):
    """Disattiva l'app authenticator (se resta senza secondo fattore dovra' riattivarne uno)."""
    ctx.user.totp_secret = None
    ctx.user.totp_pending = None
    await db.commit()
    await audit(db, "totp_disabled", request, ctx.user)
    return {"status": "ok"}


# ---------------- memoria: cosa l'assistente sa di me (trasparenza e controllo) ----------------
@router.get("/memory/me")
async def my_memory(ctx: AuthContext = Depends(require_auth), db: AsyncSession = Depends(get_db)):
    from ..models import MemoryItem
    rows = (await db.execute(select(MemoryItem).where(MemoryItem.family_id == ctx.user.family_id,
                                                       MemoryItem.status.in_(["approved", "pending"]))
                             .order_by(MemoryItem.created_at.desc()))).scalars().all()
    mine = [r for r in rows if r.user_id == ctx.user.id or r.subject_user_id == ctx.user.id or
            (r.scope == "family" and ctx.user.role != Role.child)]
    return [{"id": r.id, "text": r.text, "category": r.category, "scope": r.scope, "status": r.status,
             "mine": r.user_id == ctx.user.id or r.subject_user_id == ctx.user.id, "created_at": r.created_at} for r in mine]


@router.delete("/memory/{mid}")
async def delete_my_memory(mid: str, request: Request, ctx: AuthContext = Depends(require_auth), db: AsyncSession = Depends(get_db)):
    """Ognuno puo' cancellare le voci che lo riguardano (i bambini solo le proprie)."""
    from ..models import MemoryItem
    m = await db.get(MemoryItem, mid)
    if not m or m.family_id != ctx.user.family_id:
        raise HTTPException(404)
    if not (m.user_id == ctx.user.id or m.subject_user_id == ctx.user.id or ctx.user.role == Role.admin):
        raise HTTPException(403, "Puoi cancellare solo le voci che ti riguardano")
    await db.delete(m)
    await db.commit()
    await audit(db, "memory_self_deleted", request, ctx.user, m.text[:120])
    return {"status": "ok"}


# ---------------- il mio utilizzo (ogni utente vede i propri consumi) ----------------
@router.get("/usage")
async def my_usage(days: int = 30, ctx: AuthContext = Depends(require_auth), db: AsyncSession = Depends(get_db)):
    """Token e costi stimati dell'utente che chiede: totali, per modello, per giorno. Stesse righe (usage_log) e stesso listino
    del pannello Costi dell'amministratore, filtrate sul proprio id: nessuno vede i consumi degli altri."""
    from sqlalchemy import func

    from ..models import UsageLog
    from ..services import costs as _costs

    days = max(1, min(int(days or 30), 365))
    since = datetime.now(timezone.utc) - timedelta(days=days)
    table = _costs.pricing_table()
    rows = (await db.execute(
        select(UsageLog.provider, UsageLog.model, UsageLog.kind, func.count(),
               func.sum(UsageLog.input_tokens), func.sum(UsageLog.output_tokens),
               func.sum(UsageLog.cache_read_tokens), func.sum(UsageLog.cache_write_tokens))
        .where(UsageLog.user_id == ctx.user.id, UsageLog.created_at >= since)
        .group_by(UsageLog.provider, UsageLog.model, UsageLog.kind))).all()
    tot = {"messages": 0, "images": 0, "videos": 0, "searches": 0, "tools": 0, "input_tokens": 0, "output_tokens": 0,
           "cache_read_tokens": 0, "cache_write_tokens": 0, "cost": 0.0}
    by_model: dict[str, dict] = {}
    for prov, model, kind, n, tin, tout, tcr, tcw in rows:
        tin, tout, tcr, tcw, n = int(tin or 0), int(tout or 0), int(tcr or 0), int(tcw or 0), int(n or 0)
        cost = _costs.estimate_cost(table, prov or "", model or "", kind, tin, tout, n, tcr, tcw)
        tot["cost"] += cost
        key = {"message": "messages", "image": "images", "video": "videos", "web_search": "searches"}.get(kind, "tools")
        tot[key] += n
        if kind == "message":
            tot["input_tokens"] += tin; tot["output_tokens"] += tout
            tot["cache_read_tokens"] += tcr; tot["cache_write_tokens"] += tcw
            m = by_model.setdefault(f"{prov or '?'}/{model or '?'}", {"provider": prov or "?", "model": model or "?", "messages": 0,
                                                                     "input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cost": 0.0})
            m["messages"] += n; m["input_tokens"] += tin + tcw; m["output_tokens"] += tout; m["cache_read_tokens"] += tcr; m["cost"] += cost
    # serie giornaliera (solo messaggi: e' li' che stanno i token)
    daily = (await db.execute(
        select(func.date(UsageLog.created_at), func.count(),
               func.sum(UsageLog.input_tokens + UsageLog.cache_read_tokens + UsageLog.cache_write_tokens), func.sum(UsageLog.output_tokens))
        .where(UsageLog.user_id == ctx.user.id, UsageLog.created_at >= since, UsageLog.kind == "message")
        .group_by(func.date(UsageLog.created_at)).order_by(func.date(UsageLog.created_at)))).all()
    tot["cost"] = round(tot["cost"], 4)
    tot["tokens"] = tot["input_tokens"] + tot["output_tokens"] + tot["cache_read_tokens"] + tot["cache_write_tokens"]
    return {"days": days, "totals": tot,
            "by_model": sorted(({**m, "cost": round(m["cost"], 4)} for m in by_model.values()), key=lambda m: -m["cost"]),
            "daily": [{"day": str(d), "messages": int(n or 0), "input_tokens": int(i or 0), "output_tokens": int(o or 0)} for d, n, i, o in daily],
            "pricing_note": "Costi stimati con il listino del server (pannello Costi): orientativi, non una fattura."}


# ---------------- blocco personale del modello (solo amministratori) ----------------
class ModelLock(_BM):
    model: str = ""   # "provider/modello" oppure "" per sbloccare


@router.post("/model-lock")
async def set_model_lock(body: ModelLock, ctx: AuthContext = Depends(require_auth), db: AsyncSession = Depends(get_db)):
    """L'amministratore si fissa un modello: il menu della chat mostra solo quello e ogni sua chat lo usa, finche' non lo sblocca.
    Gli altri utenti non hanno questa scelta: usano l'elenco deciso nel pannello."""
    if ctx.user.role != Role.admin:
        raise HTTPException(403, "Solo gli amministratori possono bloccare un modello per se stessi")
    from ..services import model_catalog
    want = body.model.strip()
    if want:
        p, m = model_catalog.split(want)
        ok = any(pr["id"] == p and any(mm["id"] == m for mm in pr["models"]) for pr in available_providers())
        if not ok:
            raise HTTPException(400, "Modello non disponibile")
    pol = dict(ctx.user.policy or {})
    if want:
        pol["locked_model"] = want
    else:
        pol.pop("locked_model", None)
    ctx.user.policy = pol
    await db.commit()
    return {"model_lock": want}


from ..security.sessions import require_admin

class ActingChildIn(BaseModel):
    user_id: str = Field(min_length=1, max_length=32)

@router.post('/acting-child/start')
async def acting_child_start(body: ActingChildIn, request: Request, ctx: AuthContext = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    if ctx.session.acting_child_id:
        raise HTTPException(409, "Esci prima dalla modalità bambino")
    child = await db.get(User, body.user_id)
    if not child or not child.is_active or child.role != Role.child or child.family_id != ctx.user.family_id:
        raise HTTPException(404, "Profilo bambino non disponibile")
    ctx.session.acting_child_id = child.id
    await db.commit()
    await audit(db, 'acting_child_start', request, ctx.user, 'child_id='+child.id)
    return {'ok': True}

@router.post('/acting-child/stop')
async def acting_child_stop(request: Request, ctx: AuthContext = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    child_id = ctx.session.acting_child_id
    ctx.session.acting_child_id = None
    await db.commit()
    if child_id:
        await audit(db, 'acting_child_stop', request, ctx.user, 'child_id='+child_id)
    return {'ok': True}
