from __future__ import annotations

import os

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import get_db
from ..models import Family, AgeGroup, AuditLog, Conversation, Message, Role, Session, TrustedDevice, UsageLog, User
from ..policy.permissions import DEFAULTS, effective_policy
from ..schemas import CreateMemberRequest, UpdateMemberRequest, UserOut
from ..security.passwords import hash_secret
from ..security.sessions import AuthContext, require_admin
from ..tools.mcp_client import mcp_manager
from .auth import audit, user_out

settings = get_settings()
router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/policy-defaults")
async def policy_defaults(ctx: AuthContext = Depends(require_admin)):
    return DEFAULTS


@router.get("/members", response_model=list[UserOut])
async def members(ctx: AuthContext = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Membri con l'attivita' di accesso: ultimo login, ultima uscita, sessioni aperte (per il controllo dell'amministratore)."""
    from datetime import datetime, timezone

    from sqlalchemy import func

    from ..models import AuditLog, Session as _S
    rows = (await db.execute(select(User).where(User.family_id == ctx.user.family_id).order_by(User.created_at))).scalars().all()
    logins = dict((await db.execute(select(AuditLog.user_id, func.max(AuditLog.created_at)).where(
        AuditLog.family_id == ctx.user.family_id, AuditLog.event.like("login_%"), AuditLog.event != "login_failed").group_by(AuditLog.user_id))).all())
    logouts = dict((await db.execute(select(AuditLog.user_id, func.max(AuditLog.created_at)).where(
        AuditLog.family_id == ctx.user.family_id, AuditLog.event == "logout").group_by(AuditLog.user_id))).all())
    now = datetime.now(timezone.utc)
    sessions = dict((await db.execute(select(_S.user_id, func.count()).where(_S.expires_at > now).group_by(_S.user_id))).all())
    out = []
    for u in rows:
        o = await user_out(db, u)
        o.last_login, o.last_logout, o.active_sessions = logins.get(u.id), logouts.get(u.id), int(sessions.get(u.id, 0))
        o.mfa_choice = u.mfa_choice or ""
        out.append(o)
    return out


class FamilyName(BaseModel):
    name: str


@router.patch("/family")
async def rename_family(body: FamilyName, request: Request, ctx: AuthContext = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Nome della famiglia / organizzazione mostrato sotto il nome utente e nel titolo del pannello."""
    name = body.name.strip()[:120]
    if not name:
        raise HTTPException(400, "Nome non valido")
    fam = await db.get(Family, ctx.user.family_id)
    fam.name = name
    await db.commit()
    from ..services import workspace
    await workspace.ensure_org_tree(db, ctx.user)   # la cartella dell'organizzazione in Documenti prende subito il nuovo nome
    await audit(db, "family_renamed", request, ctx.user, name)
    return {"name": name}


@router.post("/members", response_model=UserOut)
async def create_member(body: CreateMemberRequest, request: Request, ctx: AuthContext = Depends(require_admin),
                        db: AsyncSession = Depends(get_db)):
    role = Role(body.role)
    from ..config import org_mode_on
    if role == Role.child and org_mode_on():
        raise HTTPException(400, "In modalita' azienda non si creano utenti bambini")
    if role == Role.child:
        if not body.pin:
            raise HTTPException(400, "Per un bambino serve un PIN")
        age_group = AgeGroup(body.age_group) if body.age_group in ("young", "kid", "teen") else AgeGroup.kid
    else:
        from ..services import mailer

        if not body.email:
            raise HTTPException(400, "Per un adulto serve l'email")
        if not body.password and not mailer.email_enabled():
            raise HTTPException(400, "Serve una password (oppure configura l'email per inviare un invito)")
        age_group = AgeGroup.adult
    u = User(family_id=ctx.user.family_id, username=body.username.lower(), display_name=body.display_name,
             avatar=body.avatar, role=role, age_group=age_group, birth_year=body.birth_year,
             email=body.email.lower() if body.email else None,
             password_hash=hash_secret(body.password) if body.password else None,
             pin_hash=hash_secret(body.pin) if body.pin else None)
    db.add(u)
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        raise HTTPException(400, "Username o email già in uso")
    await audit(db, "member_created", request, ctx.user, f"{u.username} ({role.value})")
    out = await user_out(db, u)
    if role != Role.child and u.email:
        out_extra = await _send_invite(db, u, ctx.user.display_name)
        return {**out.model_dump(), "invite": out_extra}
    return out


async def _send_invite(db: AsyncSession, u: User, invited_by: str) -> dict:
    from ..services import email_codes, mailer

    if not mailer.email_enabled():
        return {"sent": False, "reason": "email non configurata"}
    try:
        code = await email_codes.issue(db, u, "invite")
        await mailer.send_invite(u.email, u.display_name, code, invited_by)
        return {"sent": True}
    except Exception as e:
        return {"sent": False, "reason": str(e)[:200]}


@router.post("/members/{uid}/invite")
async def resend_invite(uid: str, request: Request, ctx: AuthContext = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """(Ri)invia l'email di invito con il link per impostare la password."""
    u = await db.get(User, uid)
    if not u or u.family_id != ctx.user.family_id or u.role == Role.child or not u.email:
        raise HTTPException(404)
    res = await _send_invite(db, u, ctx.user.display_name)
    await audit(db, "invite_sent" if res["sent"] else "invite_failed", request, ctx.user, u.email)
    if not res["sent"]:
        raise HTTPException(502, f"Invio fallito: {res['reason']}")
    return {"status": "ok"}


class EmailTestIn(BaseModel):
    to: str | None = None


@router.post("/email/test")
async def email_test(body: EmailTestIn, request: Request, ctx: AuthContext = Depends(require_admin)):
    import smtplib

    from ..services import mailer

    to = (body.to or ctx.user.email or "").strip()
    if "@" not in to:
        raise HTTPException(400, "Indica un indirizzo destinatario valido")
    if not mailer.email_enabled():
        raise HTTPException(400, "Configura almeno Server SMTP e Mittente, poi Salva")
    try:
        await mailer.send_email(to, f"{settings.app_name}: email di prova", "Se leggi questo messaggio la configurazione SMTP funziona. 🎉")
    except smtplib.SMTPAuthenticationError as e:
        raise HTTPException(502, "Credenziali rifiutate dal server SMTP. Con Gmail serve una 'password per le app' "
                                 f"(non quella normale) e la verifica in due passaggi attiva. Dettaglio: {e.smtp_code} {e.smtp_error!r}"[:400])
    except smtplib.SMTPRecipientsRefused as e:
        raise HTTPException(502, f"Destinatario rifiutato: {e.recipients}"[:400])
    except (smtplib.SMTPConnectError, ConnectionRefusedError, TimeoutError, OSError) as e:
        raise HTTPException(502, f"Impossibile connettersi a {settings.smtp_host}:{settings.smtp_port or 'default'} ({e}). "
                                 "Controlla server, porta e sicurezza (Gmail: smtp.gmail.com, 587, STARTTLS).")
    except Exception as e:
        raise HTTPException(502, f"Invio fallito: {type(e).__name__}: {e}"[:400])
    return {"status": "ok", "to": to}


@router.patch("/members/{uid}", response_model=UserOut)
async def update_member(uid: str, body: UpdateMemberRequest, request: Request,
                        ctx: AuthContext = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    u = await db.get(User, uid)
    if not u or u.family_id != ctx.user.family_id:
        raise HTTPException(404)
    data = body.model_dump(exclude_none=True)
    # Existing sessions must not inherit newly granted roles or a changed identity.
    if any(k in data for k in ("role", "email", "password", "pin", "is_active")):
        from ..security.sessions import revoke_user_access
        await revoke_user_access(db, u.id)
    if "password" in data:
        u.password_hash = hash_secret(data.pop("password"))
        await db.execute(delete(Session).where(Session.user_id == u.id))
    if "pin" in data:
        u.pin_hash = hash_secret(data.pop("pin"))
    if "role" in data:
        if u.id == ctx.user.id and data["role"] != "admin":
            raise HTTPException(400, "Non puoi togliere a te stesso il ruolo admin")
        u.role = Role(data.pop("role"))
    if "age_group" in data:
        u.age_group = AgeGroup(data.pop("age_group"))
    if "departments" in data:
        from ..services.workspace import DEPARTMENTS
        u.departments = [d for d in data.pop("departments") if d in DEPARTMENTS]
    from ..services.apps import APP_IDS
    if "apps" in data:
        v = data.pop("apps")
        u.apps = None if "*" in v else [a for a in v if a in APP_IDS]
    if "admin_apps" in data:
        u.admin_apps = [a for a in data.pop("admin_apps") if a in APP_IDS]
    if "quota_gb" in data:
        g = float(data.pop("quota_gb"))
        u.quota_bytes = None if g < 0 else int(g * (1024 ** 3))
    if "is_active" in data and u.id == ctx.user.id and not data["is_active"]:
        raise HTTPException(400, "Non puoi disattivare te stesso")
    for k, v in data.items():
        setattr(u, k, v)
    if data.get("is_active") is False:   # account disattivato: fuori subito da tutti i dispositivi
        from sqlalchemy import delete as _delete

        from ..models import Session as _S
        await db.execute(_delete(_S).where(_S.user_id == u.id))
    await db.commit()
    await audit(db, "member_updated", request, ctx.user, f"{u.username}: {list(body.model_dump(exclude_none=True).keys())}")
    return await user_out(db, u)


@router.delete("/members/{uid}")
async def delete_member(uid: str, request: Request, ctx: AuthContext = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Elimina definitivamente un membro e tutto cio' che gli appartiene. Non si puo' eliminare se stessi
    ne' l'ultimo amministratore."""
    from pathlib import Path

    from ..models import Artifact, Chunk, EmailToken, FileAsset, Project, Share, WebAuthnCredential

    u = await db.get(User, uid)
    if not u or u.family_id != ctx.user.family_id:
        raise HTTPException(404)
    if u.id == ctx.user.id:
        raise HTTPException(400, "Non puoi eliminare l'account con cui sei collegato")
    if u.role == Role.admin:
        admins = (await db.execute(select(func.count()).select_from(User).where(
            User.family_id == ctx.user.family_id, User.role == Role.admin, User.is_active == True))).scalar_one()  # noqa: E712
        if admins <= 1:
            raise HTTPException(400, "Non puoi eliminare l'ultimo amministratore")

    # file su disco e record collegati
    files = (await db.execute(select(FileAsset).where(FileAsset.owner_id == u.id))).scalars().all()
    for f in files:
        Path(f.path).unlink(missing_ok=True)
        await db.execute(delete(Chunk).where(Chunk.file_id == f.id))
        await db.delete(f)
    conv_ids = (await db.execute(select(Conversation.id).where(Conversation.owner_id == u.id))).scalars().all()
    if conv_ids:
        await db.execute(delete(Artifact).where(Artifact.conversation_id.in_(conv_ids)))
        await db.execute(delete(Message).where(Message.conversation_id.in_(conv_ids)))
        await db.execute(delete(FileAsset).where(FileAsset.conversation_id.in_(conv_ids)))
        await db.execute(delete(Share).where(Share.resource_type == "conversation", Share.resource_id.in_(conv_ids)))
        await db.execute(delete(Conversation).where(Conversation.id.in_(conv_ids)))
    proj_ids = (await db.execute(select(Project.id).where(Project.owner_id == u.id))).scalars().all()
    if proj_ids:
        await db.execute(delete(Share).where(Share.resource_type == "project", Share.resource_id.in_(proj_ids)))
        await db.execute(delete(Project).where(Project.id.in_(proj_ids)))
    await db.execute(delete(Share).where((Share.owner_id == u.id) | (Share.target_user_id == u.id)))
    await db.execute(delete(Session).where(Session.user_id == u.id))
    await db.execute(delete(WebAuthnCredential).where(WebAuthnCredential.user_id == u.id))
    await db.execute(delete(EmailToken).where(EmailToken.user_id == u.id))
    await db.execute(delete(UsageLog).where(UsageLog.user_id == u.id))
    # messaggi scritti da lui in chat altrui: restano, senza autore
    await db.execute(Message.__table__.update().where(Message.author_id == u.id).values(author_id=None))
    await db.execute(TrustedDevice.__table__.update().where(TrustedDevice.approved_by == u.id).values(approved_by=ctx.user.id))
    name = u.display_name
    await db.delete(u)
    await db.commit()
    await audit(db, "member_deleted", request, ctx.user, f"{name} ({uid})")
    return {"status": "ok"}


# ---- dispositivi fidati ----
@router.get("/devices")
async def devices(ctx: AuthContext = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(TrustedDevice).where(TrustedDevice.family_id == ctx.user.family_id)
                             .order_by(TrustedDevice.created_at.desc()))).scalars().all()
    return [{"id": d.id[:10], "full_id": d.id, "name": d.name, "created_at": d.created_at, "last_seen_at": d.last_seen_at,
             "revoked": d.revoked} for d in rows]


@router.post("/devices/{did}/revoke")
async def revoke_device(did: str, request: Request, ctx: AuthContext = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    d = await db.get(TrustedDevice, did)
    if not d or d.family_id != ctx.user.family_id:
        raise HTTPException(404)
    d.revoked = True
    await db.execute(delete(Session).where(Session.device_id == d.id))
    await db.commit()
    await audit(db, "device_revoked", request, ctx.user, d.name)
    return {"status": "ok"}


# ---- supervisione ----
@router.get("/supervision/{uid}/conversations")
async def child_conversations(uid: str, ctx: AuthContext = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    child = await db.get(User, uid)
    if not child or child.family_id != ctx.user.family_id or child.role != Role.child:
        raise HTTPException(404)
    if not effective_policy(child).get("parent_can_view", True):
        raise HTTPException(403, "Supervisione disattivata per questo utente")
    convs = (await db.execute(select(Conversation).where(Conversation.owner_id == uid).order_by(Conversation.updated_at.desc()))).scalars().all()
    out = []
    for c in convs:
        flagged = (await db.execute(select(func.count()).select_from(Message).where(
            Message.conversation_id == c.id, Message.flagged == True))).scalar_one()  # noqa: E712
        out.append({"id": c.id, "title": c.title, "updated_at": c.updated_at, "provider": c.provider, "flagged": flagged})
    return out


@router.get("/supervision/flagged")
async def flagged_messages(days: int = 7, ctx: AuthContext = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = (await db.execute(
        select(Message, Conversation, User).join(Conversation, Message.conversation_id == Conversation.id)
        .join(User, Conversation.owner_id == User.id)
        .where(Conversation.family_id == ctx.user.family_id, Message.flagged == True, Message.created_at >= since)  # noqa: E712
        .order_by(Message.created_at.desc()).limit(200))).all()
    return [{"message_id": m.id, "conversation_id": c.id, "conversation_title": c.title, "user": u.display_name,
             "role": m.role, "reason": m.flag_reason, "created_at": m.created_at,
             "text": "".join(b.get("text", "") for b in m.content if b.get("type") == "text")[:500]} for m, c, u in rows]


# ---- utilizzo ----
@router.get("/usage")
async def usage(days: int = 30, ctx: AuthContext = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = (await db.execute(
        select(UsageLog.user_id, UsageLog.kind, UsageLog.provider, func.count(), func.sum(UsageLog.input_tokens),
               func.sum(UsageLog.output_tokens))
        .where(UsageLog.family_id == ctx.user.family_id, UsageLog.created_at >= since)
        .group_by(UsageLog.user_id, UsageLog.kind, UsageLog.provider))).all()
    users = {u.id: u.display_name for u in (await db.execute(select(User).where(User.family_id == ctx.user.family_id))).scalars()}
    return [{"user": users.get(uid, uid), "user_id": uid, "kind": kind, "provider": prov, "count": n,
             "input_tokens": int(i or 0), "output_tokens": int(o or 0)} for uid, kind, prov, n, i, o in rows]


def _log_time(text: str | None, end_of_day: bool = False) -> datetime | None:
    """`AAAA-MM-GG` (giorno intero, ora italiana) o ISO 8601; None se vuoto o non valido."""
    if not text:
        return None
    try:
        if len(text) == 10:
            from zoneinfo import ZoneInfo
            d = datetime.fromisoformat(text).replace(tzinfo=ZoneInfo(os.environ.get("TZ") or "Europe/Rome"))
            return d + timedelta(days=1) if end_of_day else d
        d = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


async def _log_search(ctx: AuthContext, db: AsyncSession, user_id: str | None, app: str | None, event: str | None,
                      ok: bool | None, since: str | None, until: str | None, q: str | None, before: str | None, limit: int):
    from sqlalchemy import func, or_

    fam = ctx.user.family_id
    members = (await db.execute(select(User).where(User.family_id == fam))).scalars().all()
    names = {u.id: u.display_name or u.username for u in members}
    t_since, t_until, t_before = _log_time(since), _log_time(until, end_of_day=True), _log_time(before)
    upper = min([t for t in (t_until, t_before) if t], default=None)
    # Eventi senza organizzazione (login con un indirizzo sconosciuto): visibili solo se il server ne ha una sola.
    single = (await db.execute(select(func.count()).select_from(Family))).scalar_one() <= 1
    stmt = select(AuditLog).where(or_(AuditLog.family_id == fam, AuditLog.family_id.is_(None)) if single else AuditLog.family_id == fam)
    if user_id:
        stmt = stmt.where(AuditLog.user_id == user_id)
    if app:
        stmt = stmt.where(AuditLog.app == app)
    if event:
        stmt = stmt.where(AuditLog.event.like(event.replace("%", "") + "%") if event.endswith(".") or event.endswith("_") else AuditLog.event == event)
    if ok is not None:
        stmt = stmt.where(AuditLog.ok == ok)
    if t_since:
        stmt = stmt.where(AuditLog.created_at >= t_since)
    if upper:
        stmt = stmt.where(AuditLog.created_at < upper)
    if q and q.strip():
        like = f"%{q.strip()}%"
        who = [uid for uid, n in names.items() if q.strip().lower() in (n or "").lower()]
        stmt = stmt.where(or_(AuditLog.event.ilike(like), AuditLog.detail.ilike(like), AuditLog.ip.ilike(like),
                              AuditLog.target.ilike(like), AuditLog.user_id.in_(who)))
    rows = (await db.execute(stmt.order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(limit))).scalars().all()
    events = [{"id": a.id, "at": a.created_at.isoformat().replace("+00:00", "Z"), "app": a.app or "emily", "event": a.event,
               "user_id": a.user_id, "user_name": names.get(a.user_id or "", ""), "target": a.target or "", "detail": a.detail,
               "ip": a.ip, "ok": a.ok if a.ok is not None else True} for a in rows]
    return events, ""


@router.get("/audit")
async def audit_log(user_id: str | None = None, app: str | None = None, event: str | None = None, ok: bool | None = None,
                    since: str | None = None, until: str | None = None, q: str | None = None, before: str | None = None,
                    limit: int = 100, ctx: AuthContext = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Registro accessi e attivita' della famiglia, dal piu' recente.
    Filtri: utente, app/sezione, evento (o prefisso che finisce con . o _), esito, dal/al (AAAA-MM-GG o ISO), testo libero.
    Pagine: `before` = il campo `at` dell'ultimo evento ricevuto (`next_before`)."""
    limit = max(1, min(limit, 500))
    events, warning = await _log_search(ctx, db, user_id, app, event, ok, since, until, q, before, limit)
    apps = sorted({a for (a,) in (await db.execute(select(AuditLog.app).where(AuditLog.family_id == ctx.user.family_id).distinct())).all() if a} )
    return {"events": events, "next_before": events[-1]["at"] if len(events) == limit else None, "apps": apps, "warning": warning}



@router.get("/audit.csv")
async def audit_csv(user_id: str | None = None, app: str | None = None, event: str | None = None, ok: bool | None = None,
                    since: str | None = None, until: str | None = None, q: str | None = None, *, request: Request,
                    ctx: AuthContext = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Lo stesso registro, filtrato, in CSV (fino a 5000 righe) per archiviarlo o aprirlo in un foglio di calcolo."""
    import csv
    import io

    events, _ = await _log_search(ctx, db, user_id, app, event, ok, since, until, q, None, 5000)
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["quando_utc", "utente", "app", "evento", "esito", "oggetto", "dettaglio", "ip"])
    def safe_cell(value):
        text = str(value or "")
        return "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) else text
    for e in events:
        w.writerow([safe_cell(value) for value in [e["at"], e["user_name"] or e["user_id"] or "", e["app"], e["event"], "ok" if e["ok"] else "fallito",
                    e["target"], e["detail"], e["ip"]]])
    await audit(db, "audit_exported", request, ctx.user, f"{len(events)} righe")
    name = f"log-{datetime.now(timezone.utc):%Y%m%d-%H%M}.csv"
    return Response(content="\ufeff" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})


# ---- server MCP (configurati dal pannello, connessione a caldo) ----
import json as _json  # noqa: E402

from ..models import McpServer  # noqa: E402


class McpServerIn(BaseModel):
    name: str
    type: str = "stdio"          # stdio | http
    command: str = ""
    args: list[str] = []
    url: str = ""
    headers: dict[str, str] | None = None   # None = non modificare
    env: dict[str, str] | None = None
    min_role: str = "adult"
    enabled: bool = True
    description: str = ""


def _mcp_out(row: McpServer) -> dict:
    from ..services import settings_store

    st = mcp_manager.status.get(row.name, {})
    cfg = mcp_manager.config_from_row(row)
    return {"id": row.id, "name": row.name, "type": row.type, "command": row.command, "args": row.args or [],
            "url": row.url, "min_role": row.min_role, "enabled": row.enabled, "description": row.description,
            "headers_keys": list(cfg.headers.keys()), "env_keys": list(cfg.env.keys()),
            "connected": st.get("connected", False), "error": st.get("error", ""), "tools": st.get("tools", []),
            "updated_at": row.updated_at}


def _builtin_mcp_out(name: str) -> dict:
    """Le app ArtCraft (./familai.sh <app> up) sono server MCP integrati: nel pannello si vedono in sola lettura."""
    conn = mcp_manager.conns[name]
    st = mcp_manager.status.get(name, {})
    return {"id": f"builtin:{name}", "name": name, "type": "stdio", "command": conn.cfg.command, "args": list(conn.cfg.args or []),
            "url": None, "min_role": conn.cfg.min_role, "enabled": conn.cfg.enabled, "builtin": True,
            "description": f"app integrata ({name}: ./familai.sh {name} up|build|upgrade|down)"
                           + (f" — sessioni utente attive: {sum(1 for k in mcp_manager.user_conns if k[0] == name)}" if conn.cfg.per_user else ""),
            "headers_keys": [], "env_keys": [],
            "connected": st.get("connected", False), "error": st.get("error", ""), "tools": st.get("tools", []), "updated_at": None}


def _builtin_names(rows) -> list[str]:
    named = {r.name for r in rows}
    return [n for n in mcp_manager.conns if n not in named]   # app integrate (ArtCraft, MailCraft): non sono righe del DB


@router.get("/mcp")
async def mcp_list(ctx: AuthContext = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(McpServer).order_by(McpServer.name))).scalars().all()
    return [_builtin_mcp_out(n) for n in _builtin_names(rows)] + [_mcp_out(r) for r in rows]


async def _save_mcp(db: AsyncSession, row: McpServer | None, body: McpServerIn, user_id: str) -> McpServer:
    from ..services import settings_store

    import re
    name = re.sub(r"[^a-zA-Z0-9_-]", "-", body.name.strip())[:60]
    if not name:
        raise HTTPException(400, "Nome non valido")
    if body.type not in ("stdio", "http") or body.min_role not in ("child", "adult", "admin"):
        raise HTTPException(400, "Tipo o ruolo non valido")
    if body.type == "stdio" and not body.command.strip():
        raise HTTPException(400, "Per un server stdio serve il comando")
    if body.type == "http" and not body.url.startswith(("http://", "https://")):
        raise HTTPException(400, "Per un server http serve un URL valido")
    if row is None:
        row = McpServer(name=name)
        db.add(row)
    elif row.name != name:
        await mcp_manager.disconnect(row.name)
        row.name = name
    row.type, row.command, row.args, row.url = body.type, body.command.strip(), body.args, body.url.strip()
    row.min_role, row.enabled, row.description = body.min_role, body.enabled, body.description[:300]
    if body.headers is not None:
        row.headers_enc = settings_store.encrypt(_json.dumps(body.headers))
    if body.env is not None:
        row.env_enc = settings_store.encrypt(_json.dumps(body.env))
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        raise HTTPException(400, "Esiste gia' un server con questo nome")
    return row


@router.post("/mcp")
async def mcp_create(body: McpServerIn, request: Request, ctx: AuthContext = Depends(require_admin),
                     db: AsyncSession = Depends(get_db)):
    row = await _save_mcp(db, None, body, ctx.user.id)
    await audit(db, "mcp_created", request, ctx.user, row.name)
    if row.enabled:
        await mcp_manager.connect(mcp_manager.config_from_row(row))
    return _mcp_out(row)


@router.put("/mcp/{sid}")
async def mcp_update(sid: str, body: McpServerIn, request: Request, ctx: AuthContext = Depends(require_admin),
                     db: AsyncSession = Depends(get_db)):
    row = await db.get(McpServer, sid)
    if not row:
        raise HTTPException(404)
    row = await _save_mcp(db, row, body, ctx.user.id)
    await audit(db, "mcp_updated", request, ctx.user, row.name)
    if row.enabled:
        await mcp_manager.connect(mcp_manager.config_from_row(row))   # riconnette con la nuova config
    else:
        await mcp_manager.disconnect(row.name)
    return _mcp_out(row)


@router.delete("/mcp/{sid}")
async def mcp_delete(sid: str, request: Request, ctx: AuthContext = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    row = await db.get(McpServer, sid)
    if not row:
        raise HTTPException(404)
    await mcp_manager.disconnect(row.name)
    await db.delete(row)
    await db.commit()
    await audit(db, "mcp_deleted", request, ctx.user, row.name)
    return {"status": "ok"}


@router.post("/mcp/{sid}/connect")
async def mcp_connect(sid: str, ctx: AuthContext = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    if sid.startswith("builtin:"):   # app integrata: riconnessione (es. dopo un rebuild del container)
        name = sid.split(":", 1)[1]
        if name not in mcp_manager.conns:
            raise HTTPException(404)
        conn = await mcp_manager.connect(mcp_manager.conns[name].cfg)
        return {**_builtin_mcp_out(name), "ok": conn.connected, "error": conn.error}
    row = await db.get(McpServer, sid)
    if not row:
        raise HTTPException(404)
    if not row.enabled:
        row.enabled = True
        await db.commit()
    conn = await mcp_manager.connect(mcp_manager.config_from_row(row))
    return {**_mcp_out(row), "ok": conn.connected, "error": conn.error}


@router.post("/mcp/{sid}/disconnect")
async def mcp_disconnect(sid: str, ctx: AuthContext = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    row = await db.get(McpServer, sid)
    if not row:
        raise HTTPException(404)
    row.enabled = False
    await db.commit()
    await mcp_manager.disconnect(row.name)
    return _mcp_out(row)


@router.post("/mcp/{sid}/call")
async def mcp_call(sid: str, body: dict, ctx: AuthContext = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Prova un tool del server dal pannello: {"tool": "nome", "args": {...}}."""
    from ..tools.registry import get_tool
    from ..tools.registry import ToolContext as TC

    if sid.startswith("builtin:"):
        name = sid.split(":", 1)[1]
    else:
        row = await db.get(McpServer, sid)
        if not row:
            raise HTTPException(404)
        name = row.name
    tool = get_tool(f"mcp__{name}__{body.get('tool', '')}")
    if not tool:
        raise HTTPException(404, "Tool non trovato (server connesso?)")
    res = await tool.fn(body.get("args") or {}, TC(user=ctx.user, policy=effective_policy(ctx.user), db=db))
    return {"ok": not res.is_error, "content": res.content[:5000]}


# ---- provider AI e chiavi (gestite dal pannello, cifrate nel DB) ----
from ..providers.registry import available_providers  # noqa: E402
from ..services import settings_store  # noqa: E402

PROVIDER_CARDS = [{'id': 'openai',
  'label': 'ChatGPT (OpenAI)',
  'key': 'openai_api_key',
  'model_key': 'openai_default_model',
  'hint': 'platform.openai.com/api-keys — usata anche per immagini, moderazione ed embedding',
  'testable': True},
 {'id': 'ollama',
  'label': 'Modello locale (Ollama)',
  'key': 'ollama_url',
  'model_key': 'ollama_default_model',
  'kind': 'url',
  'hint': 'Mac: installa Ollama (brew install ollama), poi `ollama pull qwen3:8b` e `ollama pull qwen3:4b`; '
          'indirizzo http://host.docker.internal:11434. Ubuntu: COMPOSE_PROFILES=ollama nel .env e indirizzo '
          'http://ollama:11434',
  'testable': True}]
OTHER_SETTINGS = ['user_models_json',
 'user_default_model',
 'default_quota_gb',
 'openai_reasoning_effort',
 'web_search_provider',
 'searxng_url',
 'web_search_summarize',
 'image_provider',
 'openai_cache_mode',
 'image_model',
 'embedding_provider',
 'embedding_model',
 'tts_provider',
 'tts_model_openai',
 'tts_voice_openai',
 'tts_style',
 'memory_enabled',
 'memory_auto_approve',
 'memory_model',
 'compaction_enabled',
 'auto_continue',
 'thinking_mode',
 'debug_llm',
 'ollama_temperature',
 'ollama_top_p',
 'ollama_seed',
 'ollama_think',
 'smtp_host',
 'smtp_port',
 'smtp_user',
 'smtp_password',
 'smtp_from',
 'smtp_from_name',
 'smtp_security',
 'email_mfa_fallback',
 'google_client_id',
 'google_client_secret',
 'mail_transport']


class SettingsUpdate(BaseModel):
    values: dict[str, str]  # chiave impostazione -> nuovo valore ("" = rimuovi override)


@router.get("/providers")
async def providers_status(ctx: AuthContext = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    st = await settings_store.describe(db)
    active = {p["id"]: p for p in available_providers()}
    cards = []
    for c in PROVIDER_CARDS:
        cards.append({**c, "key_state": st[c["key"]], "model_state": st[c["model_key"]] if c["model_key"] else None,
                      "active": c["id"] in active,
                      "models": active.get(c["id"], {}).get("models", [])})
    from .google_auth import redirect_uri
    from ..providers.registry import _providers

    oa = _providers().get("openai")
    image_models = list(getattr(oa, "image_models", []) or []) if oa else []
    if not image_models:
        image_models = ["gpt-image-2.5-flare", "gpt-image-2.5-sunburst", "gpt-image-1.5"]
    return {"providers": cards, "settings": {k: st[k] for k in OTHER_SETTINGS}, "image_models": image_models,
            "mock_active": "mock" in active, "only_mock": set(active) == {"mock"},
            "google_redirect_uri": redirect_uri()}


@router.put("/providers")
async def providers_update(body: SettingsUpdate, request: Request, ctx: AuthContext = Depends(require_admin),
                           db: AsyncSession = Depends(get_db)):
    changed = []
    if set(body.values) - set(settings_store.MANAGED_KEYS):
        raise HTTPException(400, "Impostazione non supportata in FamilOS")
    allowed_values = {"openai_cache_mode": {"auto", "stable"}, "embedding_provider": {"auto", "ollama", "openai", "none"}, "tts_provider": {"auto", "openai", "none"}, "image_provider": {"auto", "openai"}, "web_search_provider": {"auto", "openai", "searxng", "duckduckgo"}}
    for key, value in body.values.items():
        if value and key in allowed_values and value not in allowed_values[key]:
            raise HTTPException(400, "Provider non supportato: scegli OpenAI oppure Ollama")
    for k, v in body.values.items():
        if k not in settings_store.MANAGED_KEYS:
            raise HTTPException(400, f"Impostazione sconosciuta: {k}")
        await settings_store.set_value(db, k, v, ctx.user.id)
        changed.append(k)
    await settings_store.apply(db)
    await audit(db, "settings_changed", request, ctx.user, ", ".join(changed))
    return await providers_status(ctx, db)


@router.post("/providers/{pid}/test")
async def providers_test(pid: str, ctx: AuthContext = Depends(require_admin)):
    if pid not in {c["id"] for c in PROVIDER_CARDS}:
        raise HTTPException(404)
    return await settings_store.test_provider(pid)


# ---- costi e budget ----
from ..services import costs as _costs  # noqa: E402


@router.get("/costs")
async def costs_report(ctx: AuthContext = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Spesa stimata (dai log) e reale (se chiavi Admin), budget e rimanente per il mese corrente."""
    st = await settings_store.describe(db)
    rep = await _costs.full_report(db, ctx.user.family_id)
    rep["settings"] = {k: st[k] for k in ("pricing_json", "budget_openai", "openai_admin_key")}
    return rep


@router.get("/costs/users")
async def costs_by_user(period: str = "month", ctx: AuthContext = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Token per utente, provider per provider (cloud e locali) e modello per modello: month | prev | 7d | 30d."""
    return await _costs.users_report(db, ctx.user.family_id, period if period in ("month", "prev", "7d", "30d") else "month")


# ---- Gmail via OAuth ("Collega account Google" per l'invio email) ----
import secrets as _secrets  # noqa: E402
import time as _time  # noqa: E402
from urllib.parse import urlencode as _urlencode  # noqa: E402

import httpx as _httpx  # noqa: E402
from fastapi.responses import RedirectResponse as _Redirect  # noqa: E402

_gmail_pending: dict[str, tuple[str, float]] = {}  # state -> (admin user id, scadenza)


def gmail_redirect_uri() -> str:
    return f"{settings.webauthn_origin.rstrip('/')}/api/admin/email/google/callback"


@router.get("/email/google/start")
async def gmail_oauth_start(response: Response, ctx: AuthContext = Depends(require_admin)):
    """Avvia il consenso Google per inviare email dall'account di famiglia (scope gmail.send)."""
    if not (settings.google_client_id and settings.google_client_secret):
        raise HTTPException(400, "Configura prima Client ID e secret nella scheda 'Accesso con Google'")
    now = _time.time()
    for k in [k for k, (_, exp) in _gmail_pending.items() if exp < now]:
        _gmail_pending.pop(k, None)
    state = _secrets.token_urlsafe(32)
    _gmail_pending[state] = (ctx.session.id, now + 600)
    response.set_cookie("familos_gmail_state", state, max_age=600, httponly=True,
                        secure=settings.cookie_secure, samesite="lax", path="/api/admin/email/google/callback")
    params = {"client_id": settings.google_client_id, "redirect_uri": gmail_redirect_uri(), "response_type": "code",
              "scope": "https://www.googleapis.com/auth/gmail.send openid email",
              "access_type": "offline", "prompt": "consent select_account", "state": state}
    return {"url": f"https://accounts.google.com/o/oauth2/v2/auth?{_urlencode(params)}"}


@router.get("/email/google/callback")
async def gmail_oauth_callback(request: Request, code: str = "", state: str = "", error: str = "",
                               db: AsyncSession = Depends(get_db)):
    """Callback di Google: salva il refresh token (cifrato) e l'indirizzo collegato."""
    def back(status_: str) -> _Redirect:
        response = _Redirect(f"/admin?tab=providers&gmail={status_}", status_code=302)
        response.delete_cookie("familos_gmail_state", path="/api/admin/email/google/callback")
        return response

    binding = request.cookies.get("familos_gmail_state", "")
    if not binding or not state or not _secrets.compare_digest(binding, state):
        return back("scaduto")

    if error:
        return back("annullato")
    item = _gmail_pending.pop(state, None)
    if not item or item[1] < _time.time():
        return back("scaduto")
    sess = await db.get(Session, item[0])
    owner = await db.get(User, sess.user_id) if sess else None
    if (not sess or sess.screen_locked or not sess.mfa_verified
            or sess.expires_at.replace(tzinfo=timezone.utc) <= datetime.now(timezone.utc)
            or not owner or not owner.is_active or owner.role != Role.admin):
        return back("scaduto")
    admin_id = owner.id
    try:
        async with _httpx.AsyncClient(timeout=20) as c:
            tok = await c.post("https://oauth2.googleapis.com/token", data={
                "code": code, "client_id": settings.google_client_id, "client_secret": settings.google_client_secret,
                "redirect_uri": gmail_redirect_uri(), "grant_type": "authorization_code"})
            tok.raise_for_status()
            data = tok.json()
            info = (await c.get("https://openidconnect.googleapis.com/v1/userinfo",
                                headers={"Authorization": f"Bearer {data['access_token']}"})).json()
    except Exception:
        return back("errore")
    refresh = data.get("refresh_token")
    if not refresh:
        return back("norefresh")
    await settings_store.set_value(db, "gmail_oauth_refresh_token", refresh, admin_id)
    await settings_store.set_value(db, "gmail_oauth_email", (info.get("email") or "").lower(), admin_id)
    if not settings.smtp_from:
        await settings_store.set_value(db, "smtp_from", (info.get("email") or "").lower(), admin_id)
    await settings_store.apply(db)
    db.add(AuditLog(family_id=None, user_id=admin_id, event="gmail_oauth_linked", detail=info.get("email", ""), ip=client_ip_safe(request)))
    await db.commit()
    return back("ok")


def client_ip_safe(request: Request) -> str:
    from ..security.sessions import client_ip

    return client_ip(request)


@router.post("/email/google/unlink")
async def gmail_oauth_unlink(request: Request, ctx: AuthContext = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    await settings_store.set_value(db, "gmail_oauth_refresh_token", "", ctx.user.id)
    await settings_store.set_value(db, "gmail_oauth_email", "", ctx.user.id)
    await settings_store.apply(db)
    await audit(db, "gmail_oauth_unlinked", request, ctx.user)
    return {"status": "ok"}


@router.get("/email/status")
async def email_status(ctx: AuthContext = Depends(require_admin)):
    from ..services import mailer

    return {"transport": mailer.transport(), "sender": mailer.sender_address(),
            "gmail_linked": bool(settings.gmail_oauth_refresh_token), "gmail_email": settings.gmail_oauth_email,
            "gmail_redirect_uri": gmail_redirect_uri(), "google_client_ready": bool(settings.google_client_id and settings.google_client_secret)}


# ---- backup completo e ripristino ----
from fastapi import File as _File, Form as _Form, UploadFile as _UploadFile  # noqa: E402
from fastapi.responses import FileResponse as _FileResponse  # noqa: E402

from ..services import backup as _backup  # noqa: E402


class BackupRequest(BaseModel):
    password: str


@router.get("/backup/info")
async def backup_info(ctx: AuthContext = Depends(require_admin)):
    try:
        return _backup.info()
    except Exception as e:
        raise HTTPException(400, str(e))


@router.post("/backup/export")
async def backup_export(body: BackupRequest, request: Request, ctx: AuthContext = Depends(require_admin),
                        db: AsyncSession = Depends(get_db)):
    """Archivio cifrato con tutto: utenti, chat, progetti, file, impostazioni e chiavi API (in chiaro dentro, cifrate fuori)."""
    import asyncio
    from ..main import _VERSION
    try:
        path = await asyncio.to_thread(_backup.create, body.password, _VERSION)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"Backup fallito: {e}")
    await audit(db, "backup_exported", request, ctx.user, path.name)
    return _FileResponse(path, media_type="application/octet-stream", filename=path.name)


@router.post("/backup/import")
async def backup_import(request: Request, file: _UploadFile = _File(...), password: str = _Form(...),
                        ctx: AuthContext = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Prepara il ripristino e riavvia il backend: al riavvio DB e file vengono sostituiti e i segreti ricifrati.
    Tutte le sessioni vengono chiuse: dopo bisogna accedere di nuovo con le credenziali del backup."""
    import asyncio
    import os
    data = await file.read()
    try:
        manifest = await asyncio.to_thread(_backup.stage, data, password)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"Ripristino fallito: {e}")
    await audit(db, "backup_import_scheduled", request, ctx.user, f"{file.filename} ({manifest.get('created_at')})")

    async def _restart():
        await asyncio.sleep(1.5)
        os._exit(0)   # il container ha restart: unless-stopped → riparte e applica il ripristino

    asyncio.get_event_loop().create_task(_restart())
    return {"status": "scheduled", "manifest": manifest}


@router.delete("/backup/pending")
async def backup_cancel(ctx: AuthContext = Depends(require_admin)):
    _backup.cancel_pending()
    return {"status": "ok"}


# ---- memoria di famiglia: coda di approvazione e gestione ----
from ..models import MemoryItem as _MemoryItem  # noqa: E402
from ..services import memory as _memory  # noqa: E402


class MemoryEdit(BaseModel):
    text: str | None = None
    category: str | None = None
    scope: str | None = None
    pinned: bool | None = None
    subject_user_id: str | None = None


class MemoryCreate(BaseModel):
    text: str
    category: str = "altro"
    scope: str = "family"
    subject_user_id: str | None = None
    pinned: bool = False


def _mem_out(m: _MemoryItem, names: dict[str, str]) -> dict:
    return {"id": m.id, "text": m.text, "category": m.category, "scope": m.scope, "status": m.status, "pinned": m.pinned,
            "confidence": m.confidence, "created_at": m.created_at, "used_count": m.used_count,
            "author": names.get(m.user_id, "?"), "subject": names.get(m.subject_user_id or "", None),
            "subject_user_id": m.subject_user_id, "source_conversation_id": m.source_conversation_id}


@router.get("/memory")
async def memory_list(status_: str = "pending", q: str = "", ctx: AuthContext = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    names = {u.id: u.display_name for u in (await db.execute(select(User).where(User.family_id == ctx.user.family_id))).scalars()}
    stmt = select(_MemoryItem).where(_MemoryItem.family_id == ctx.user.family_id)
    if status_ != "all":
        stmt = stmt.where(_MemoryItem.status == status_)
    rows = (await db.execute(stmt.order_by(_MemoryItem.pinned.desc(), _MemoryItem.created_at.desc()))).scalars().all()
    if q.strip():
        ql = q.lower()
        rows = [r for r in rows if ql in r.text.lower()]
    counts = {}
    for st in ("pending", "approved", "rejected"):
        counts[st] = (await db.execute(select(func.count()).select_from(_MemoryItem).where(
            _MemoryItem.family_id == ctx.user.family_id, _MemoryItem.status == st))).scalar_one()
    return {"items": [_mem_out(r, names) for r in rows[:500]], "counts": counts,
            "enabled": _memory.enabled(), "members": [{"id": k, "name": v} for k, v in names.items()],
            "categories": _memory.CATEGORIES}


@router.post("/memory")
async def memory_create(body: MemoryCreate, request: Request, ctx: AuthContext = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    why = _memory.firewall(body.text)
    if why:
        raise HTTPException(400, f"Voce non consentita dal filtro ({why})")
    item = _MemoryItem(family_id=ctx.user.family_id, user_id=ctx.user.id, subject_user_id=body.subject_user_id if body.scope == "user" else None,
                       scope=body.scope, category=body.category if body.category in _memory.CATEGORIES else "altro",
                       text=body.text.strip()[:400], confidence=5, status="approved", pinned=body.pinned,
                       reviewed_by=ctx.user.id, reviewed_at=datetime.now(timezone.utc))
    db.add(item)
    await db.flush()
    await _memory.embed_items([item])
    await db.commit()
    await audit(db, "memory_added", request, ctx.user, item.text[:120])
    return {"status": "ok", "id": item.id}


@router.post("/memory/{mid}/{action}")
async def memory_review(mid: str, action: str, request: Request, ctx: AuthContext = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    m = await db.get(_MemoryItem, mid)
    if not m or m.family_id != ctx.user.family_id:
        raise HTTPException(404)
    if action not in ("approve", "reject", "pin", "unpin"):
        raise HTTPException(400, "azione sconosciuta")
    if action == "approve":
        m.status = "approved"
    elif action == "reject":
        m.status = "rejected"
    else:
        m.pinned = action == "pin"
        if m.pinned:
            m.status = "approved"
    m.reviewed_by, m.reviewed_at = ctx.user.id, datetime.now(timezone.utc)
    if m.status == "approved" and not m.embedding:
        await _memory.embed_items([m])
    await db.commit()
    await audit(db, f"memory_{action}", request, ctx.user, m.text[:120])
    return {"status": "ok"}


@router.post("/memory/bulk/{action}")
async def memory_bulk(action: str, body: dict, request: Request, ctx: AuthContext = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    ids = list(body.get("ids") or [])
    if action not in ("approve", "reject", "delete"):
        raise HTTPException(400, "azione sconosciuta")
    rows = (await db.execute(select(_MemoryItem).where(_MemoryItem.family_id == ctx.user.family_id, _MemoryItem.id.in_(ids)))).scalars().all()
    for m in rows:
        if action == "delete":
            await db.delete(m)
        else:
            m.status = "approved" if action == "approve" else "rejected"
            m.reviewed_by, m.reviewed_at = ctx.user.id, datetime.now(timezone.utc)
            if m.status == "approved" and not m.embedding:
                await _memory.embed_items([m])
    await db.commit()
    await audit(db, f"memory_bulk_{action}", request, ctx.user, f"{len(rows)} voci")
    return {"status": "ok", "count": len(rows)}


@router.patch("/memory/{mid}")
async def memory_edit(mid: str, body: MemoryEdit, ctx: AuthContext = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    m = await db.get(_MemoryItem, mid)
    if not m or m.family_id != ctx.user.family_id:
        raise HTTPException(404)
    if body.text is not None:
        why = _memory.firewall(body.text)
        if why:
            raise HTTPException(400, f"Voce non consentita dal filtro ({why})")
        m.text = body.text.strip()[:400]
        m.embedding = None
        await _memory.embed_items([m])
    if body.category in _memory.CATEGORIES:
        m.category = body.category
    if body.scope in ("family", "user"):
        m.scope = body.scope
        m.subject_user_id = body.subject_user_id if body.scope == "user" else None
    if body.pinned is not None:
        m.pinned = body.pinned
    await db.commit()
    return {"status": "ok"}


@router.delete("/memory/{mid}")
async def memory_delete(mid: str, ctx: AuthContext = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    m = await db.get(_MemoryItem, mid)
    if not m or m.family_id != ctx.user.family_id:
        raise HTTPException(404)
    await db.delete(m)
    await db.commit()
    return {"status": "ok"}


@router.post("/memory/extract/{cid}")
async def memory_extract_now(cid: str, ctx: AuthContext = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Forza l'estrazione su una conversazione (utile per riempire la memoria dalle chat passate)."""
    conv = await db.get(Conversation, cid)
    if not conv or conv.family_id != ctx.user.family_id:
        raise HTTPException(404)
    conv.settings = {**(conv.settings or {}), "memory": {"user_count": 0}}
    await db.commit()
    before = (await db.execute(select(func.count()).select_from(_MemoryItem).where(_MemoryItem.family_id == ctx.user.family_id))).scalar_one()
    await _memory.maybe_extract(cid, conv.owner_id)
    after = (await db.execute(select(func.count()).select_from(_MemoryItem).where(_MemoryItem.family_id == ctx.user.family_id))).scalar_one()
    return {"status": "ok", "new_items": int(after - before)}


@router.post("/memory/extract-all")
async def memory_extract_all(ctx: AuthContext = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Estrazione su tutte le chat della famiglia non ancora processate (in background)."""
    import asyncio
    convs = (await db.execute(select(Conversation).where(Conversation.family_id == ctx.user.family_id))).scalars().all()
    todo = [c for c in convs if not (c.settings or {}).get("memory")]

    async def run():
        for c in todo:
            await _memory.maybe_extract(c.id, c.owner_id)

    asyncio.get_event_loop().create_task(run())
    return {"status": "scheduled", "conversations": len(todo)}


# ---- software del framework (catalogo per il pannello Membri) ----
@router.get("/apps")
async def apps_catalog(ctx: AuthContext = Depends(require_admin)):
    """Il catalogo dei software con lo stato (attivo sul server) e cosa puo' fare un amministratore di quella app."""
    from ..services.apps import APPS
    from ..tools.mcp_client import mcp_manager
    active = {"wordcraft": bool(settings.wordcraft_web) or mcp_manager.is_connected("wordcraft"),
              "gridcraft": bool(settings.gridcraft_web) or mcp_manager.is_connected("gridcraft"),
              "pdfcraft": bool(settings.pdfcraft_web) or mcp_manager.is_connected("pdfcraft"),
              "deckcraft": bool(settings.deckcraft_web) or mcp_manager.is_connected("deckcraft"),
              "ideacraft": bool(settings.ideacraft_web) or mcp_manager.is_connected("ideacraft"),
              "mailcraft": bool(settings.mailcraft_url and settings.mailcraft_token),
              "messaggi": True,   # nativo: la pagina spiega come collegare l'account
              "archivio": True}
    from ..services.apps import available_app_ids
    return [{**a, "active": active.get(a["id"], False)} for a in APPS if a["id"] in available_app_ids()]


# ---- modelli per utente (griglia Organizzazione › Modelli) ----
class UserModelsRow(BaseModel):
    models: list[str] | None = None   # None = segue l'elenco predefinito
    default: str = ""


class UserModelsSave(BaseModel):
    users: dict[str, UserModelsRow]


@router.get("/user-models")
async def user_models_matrix(ctx: AuthContext = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Colonne (i modelli dei provider attivi, router Auto compreso), l'elenco predefinito e, per ogni adulto, il suo elenco."""
    from ..services import model_catalog as mc
    groups = [{"id": p["id"], "label": p["label"], "models": p["models"]} for p in available_providers()
              if p["id"] != "mock" and p.get("models")]
    users = (await db.execute(select(User).where(User.family_id == ctx.user.family_id).order_by(User.display_name))).scalars().all()
    return {"groups": groups, "default_models": mc.user_models(), "default_model": mc.user_default(),
            "users": [{"id": u.id, "display_name": u.display_name, "avatar": u.avatar, "role": u.role.value, "is_active": u.is_active,
                       "models": mc.personal_models(u), "default": str((u.policy or {}).get("user_default_model") or ""),
                       "effective_default": mc.user_default_for(u)} for u in users]}


@router.put("/user-models")
async def user_models_save(body: UserModelsSave, request: Request, ctx: AuthContext = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Salva gli elenchi personali (solo adulti non amministratori: gli admin vedono tutto, i bambini hanno il modello del genitore)."""
    from ..services import model_catalog as mc
    if len(body.users) > 1000:
        raise HTTPException(400, "Troppi utenti in una volta")
    changed = 0
    for uid, row in body.users.items():
        u = await db.get(User, uid[:40])
        if not u or u.family_id != ctx.user.family_id or u.role != Role.adult:
            continue
        pol = dict(u.policy or {})
        if row.models is None:
            pol.pop("user_models", None)
            pol.pop("user_default_model", None)
        else:
            models = list(dict.fromkeys(m.strip()[:160] for m in row.models[:mc.MAX_USER_MODELS] if isinstance(m, str) and "/" in m))
            if not models:
                raise HTTPException(400, f"{u.display_name}: abilita almeno un modello, oppure lascialo sul predefinito")
            pol["user_models"] = models
            pol["user_default_model"] = row.default if row.default in models else models[0]
        if pol != (u.policy or {}):
            u.policy = pol
            changed += 1
    await db.commit()
    if changed:
        await audit(db, "user_models_changed", request, ctx.user, f"{changed} utenti")
    return {"ok": True, "changed": changed}
