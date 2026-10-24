"""Craft Sync: le API con cui l'agente sul computer tiene una cartella locale uguale ai Documenti di Emily.

Due famiglie di rotte:
- **/api/sync/devices…** (sessione del browser, adulti): i computer collegati, il codice per collegarne uno, la revoca;
- **tutto il resto** (token portatore `Authorization: Bearer cs_…`, ottenuto con `POST /api/sync/pair` o `POST /api/sync/login`): albero delle cartelle,
  elenco dei cambiamenti con cursore, contenuto dei file (GET, PUT con `If-Match`), creazione, rinomina, spostamento ed
  eliminazione di file e cartelle, cronologia delle versioni, eventi SSE. Le operazioni riusano le stesse funzioni dei
  Documenti nel browser, cosi' permessi, nomi unici, versioni e indicizzazione sono identici. Documentazione: CRAFT-SYNC.md.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import mimetypes
import re
import uuid
from pathlib import Path

import os

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import get_db
from ..models import Chunk, Family, FileAsset, Folder, Role, SyncDevice, User
from ..policy.permissions import effective_policy
from ..security import totp as totp_svc
from ..security.passwords import verify_secret
from ..security.sessions import AuthContext, RateLimiter, client_ip, require_adult, user_has_passkey, user_has_totp
from ..services import events, rag, sync, workspace
from ..services.access import require_access
from ..services.naming import unique_filename
from ..services.sync import DeviceContext, require_device
from ..tools.documents import extract_text
from . import files as files_api
from . import folders as folders_api
from .auth import audit, locked, register_failure
from ..services.file_locks import serialize_file_write

settings = get_settings()
log = logging.getLogger(__name__)
router = APIRouter(prefix="/sync", tags=["sync"])
pair_limiter = RateLimiter(max_hits=10, window_s=300)
login_limiter = RateLimiter(max_hits=10, window_s=300)   # per IP, per email e per utente (codice TOTP)
API_VERSION = 1


def _user_out(u: User) -> dict:
    return {"id": u.id, "username": u.username, "display_name": u.display_name, "role": u.role.value,
            "departments": list(u.departments or [])}


async def _server_out(db: AsyncSession, user: User) -> dict:
    fam = await db.get(Family, user.family_id)
    from ..main import app as _app
    return {"name": (fam.name if fam else "") or "Emily", "version": _app.version, "api": API_VERSION,
            "pairing_minutes": sync.PAIRING_MINUTES, "changes_limit": sync.CHANGES_LIMIT}


def _clean_name(name: str) -> str:
    return re.sub(r"[^\w.\- ()]", "_", (name or "").strip())[:200].strip(". ")


# ---------------------------------------------------------------- dispositivi (sessione del browser) ----
@router.get("/devices")
async def list_devices(all: bool = False, ctx: AuthContext = Depends(require_adult), db: AsyncSession = Depends(get_db)):
    """I computer collegati dall'utente; l'amministratore con `all=1` vede quelli di tutta l'organizzazione."""
    q = select(SyncDevice).where(SyncDevice.family_id == ctx.user.family_id)
    everyone = bool(all) and ctx.user.role == Role.admin
    if not everyone:
        q = q.where(SyncDevice.user_id == ctx.user.id)
    rows = (await db.execute(q.order_by(SyncDevice.created_at.desc()))).scalars().all()
    names = None
    if everyone:
        names = dict((await db.execute(select(User.id, User.display_name).where(User.family_id == ctx.user.family_id))).all())
    return [sync.device_item(d, names) for d in rows]


@router.post("/devices/pairing-code")
async def pairing_code(request: Request, ctx: AuthContext = Depends(require_adult), db: AsyncSession = Depends(get_db)):
    """Un codice da scrivere nell'agente sul computer: vale dieci minuti, una volta sola, e collega quel computer a questo utente."""
    code, exp = await sync.create_pairing_code(db, ctx.user)
    await audit(db, "sync_pairing_code", request, ctx.user)
    return {"code": code, "expires_at": exp, "minutes": sync.PAIRING_MINUTES}


class DevicePatch(BaseModel):
    name: str


async def _own_device(db: AsyncSession, did: str, ctx: AuthContext) -> SyncDevice:
    d = await db.get(SyncDevice, did)
    if not d or d.family_id != ctx.user.family_id or (d.user_id != ctx.user.id and ctx.user.role != Role.admin):
        raise HTTPException(404, "Dispositivo non trovato")
    return d


@router.patch("/devices/{did}")
async def rename_device(did: str, body: DevicePatch, ctx: AuthContext = Depends(require_adult), db: AsyncSession = Depends(get_db)):
    d = await _own_device(db, did, ctx)
    name = body.name.strip()[:120]
    if not name:
        raise HTTPException(400, "Nome non valido")
    d.name = name
    await db.commit()
    return sync.device_item(d)


@router.delete("/devices/{did}")
async def revoke_device(did: str, request: Request, ctx: AuthContext = Depends(require_adult), db: AsyncSession = Depends(get_db)):
    """Revoca: il token di quel computer smette di funzionare subito (la riga resta, per il registro)."""
    d = await _own_device(db, did, ctx)
    d.revoked = True
    await db.commit()
    await audit(db, "sync_device_revoked", request, ctx.user, detail=d.name)
    return {"ok": True}


# ---------------------------------------------------------------- collegamento ----
class PairIn(BaseModel):
    code: str
    name: str = "Computer"
    platform: str = ""
    app_version: str = ""


@router.post("/pair")
async def pair(body: PairIn, request: Request, db: AsyncSession = Depends(get_db)):
    """L'agente scambia il codice con il token del dispositivo (mostrato una volta sola: lo conserva nel portachiavi)."""
    pair_limiter.check(client_ip(request))
    dev, token, user = await sync.redeem_pairing_code(db, body.code, body.name, body.platform, body.app_version, client_ip(request))
    await audit(db, "sync_device_paired", request, user, detail=f"{dev.name} ({dev.platform or '?'})")
    return {"token": token, "device": sync.device_item(dev), "user": _user_out(user), "server": await _server_out(db, user)}


class LoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=200)
    totp: str | None = Field(default=None, max_length=12)
    name: str = Field(default="Computer", max_length=120)
    platform: str = Field(default="", max_length=20)
    app_version: str = Field(default="", max_length=40)


def _coded(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code, detail={"code": code, "message": message})


@router.post("/login")
async def login(body: LoginIn, request: Request, db: AsyncSession = Depends(get_db)):
    """Collegamento con le credenziali di Emily, senza passare da Impostazioni: stesse regole di `POST /api/auth/login`
    (account attivo, adulto, non bloccato; cinque password sbagliate bloccano per un quarto d'ora). Chi ha l'app
    authenticator manda anche `totp`; chi ha solo la passkey usa il codice di Impostazioni (l'agente non fa WebAuthn).
    Stessa risposta di `/pair`."""
    ip, email = client_ip(request), body.email.strip().lower()
    login_limiter.check(ip)
    login_limiter.check("login:" + email)
    user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if not user or not user.is_active or user.role == Role.child or locked(user):
        await audit(db, "login_unknown" if not user else "login_refused", request, user, email[:120] if not user else
                    ("account bloccato" if locked(user) else "account disattivato" if not user.is_active else "bambino: accesso con PIN"))
        raise HTTPException(401, "Credenziali non valide")
    if not verify_secret(user.password_hash, body.password):
        await register_failure(db, user)
        await audit(db, "login_failed", request, user, "craft sync")
        raise HTTPException(401, "Credenziali non valide")
    if user_has_totp(user):
        code = (body.totp or "").strip()
        if not code:
            raise _coded(401, "totp_required", "Inserisci il codice dell'app authenticator")
        login_limiter.check("totp:" + user.id)
        from ..services.settings_store import decrypt
        if not totp_svc.verify(decrypt(user.totp_secret), code):
            await audit(db, "mfa_totp_failed", request, user, "craft sync")
            raise _coded(401, "totp_invalid", "Codice non valido")
        await audit(db, "mfa_totp_ok", request, user, "craft sync")
    elif await user_has_passkey(db, user):
        raise _coded(403, "passkey_only",
                     "Questo account usa solo la passkey: collega il computer con il codice di Impostazioni › Craft Sync")
    user.failed_logins = 0
    dev, token = await sync.create_device(db, user, body.name, body.platform, body.app_version, ip)
    await audit(db, "sync_device_login", request, user, detail=f"{dev.name} ({dev.platform or '?'})")
    return {"token": token, "device": sync.device_item(dev), "user": _user_out(user), "server": await _server_out(db, user)}


# ---------------------------------------------------------------- con il token del dispositivo ----
@router.get("/me")
async def me(dctx: DeviceContext = Depends(require_device), db: AsyncSession = Depends(get_db)):
    return {"user": _user_out(dctx.user), "device": sync.device_item(dctx.device), "server": await _server_out(db, dctx.user)}


@router.get("/tree")
async def tree(dctx: DeviceContext = Depends(require_device), db: AsyncSession = Depends(get_db)):
    """Tutte le cartelle che l'utente vede (le sue, CHAT, l'archivio del suo reparto). L'albero lo ricostruisce il client."""
    return {"folders": await sync.tree(db, dctx.user), "cursor": await sync.current_seq(db)}


@router.get("/changes")
async def changes(since: int = Query(0, ge=0, le=2**62), limit: int = Query(sync.CHANGES_LIMIT, ge=1, le=sync.CHANGES_LIMIT),
                  dctx: DeviceContext = Depends(require_device), db: AsyncSession = Depends(get_db)):
    """Cosa e' cambiato dopo il cursore: file, cartelle e lapidi, in ordine. `since=0` e' lo stato completo."""
    return await sync.changes(db, dctx.user, since, limit)


@router.get("/events")
async def stream(request: Request, dctx: DeviceContext = Depends(require_device)):
    """Flusso SSE per l'agente: solo "sync" e "files" (mai messaggi, chiamate o notifiche: il token di un computer non e'
    una sessione del browser). Battito ogni 25 s; a ogni battito si verifica che il dispositivo non sia stato revocato."""
    from ..db import SessionLocal
    q = events.subscribe(dctx.user.id)
    device_id = dctx.device.id

    async def still_allowed() -> bool:
        async with SessionLocal() as s:
            d = await s.get(SyncDevice, device_id)
            return bool(d and not d.revoked)

    async def gen():
        try:
            yield events.sse({"kind": "hello", "device": device_id})
            while True:
                if await request.is_disconnected():
                    break
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=25)
                    if ev.get("kind") in ("sync", "files"):
                        yield events.sse(ev)
                except asyncio.TimeoutError:
                    if not await still_allowed():
                        break
                    yield ": ping\n\n"
        finally:
            events.unsubscribe(dctx.user.id, q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"})


async def _item(db: AsyncSession, asset: FileAsset, user: User) -> dict:
    return sync.file_item(asset, user, await sync.org_folder_ids(db, user))


def _json(status: int, payload: dict) -> JSONResponse:
    return JSONResponse(status_code=status, content=jsonable_encoder(payload))


@router.get("/files/{fid}")
async def file_meta(fid: str, dctx: DeviceContext = Depends(require_device), db: AsyncSession = Depends(get_db)):
    asset, _ = await require_access(db, "file", fid, dctx.user)
    await sync.ensure_sha(db, asset)
    await db.commit()
    return await _item(db, asset, dctx.user)


@router.get("/files/{fid}/content")
async def get_content(fid: str, dctx: DeviceContext = Depends(require_device), db: AsyncSession = Depends(get_db)):
    """Il contenuto del file, con `ETag` = sha256 e `X-Sync-Seq`."""
    asset, _ = await require_access(db, "file", fid, dctx.user)
    if not asset.path or not Path(asset.path).is_file():
        raise HTTPException(404, "Contenuto non disponibile")
    sha = await sync.ensure_sha(db, asset)
    await db.commit()
    headers = {"ETag": f'"{sha}"', "X-Sync-Seq": str(asset.seq or 0), "Cache-Control": "no-store, private",
               "X-Content-Type-Options": "nosniff"}
    return FileResponse(asset.path, media_type=asset.mime or "application/octet-stream", filename=asset.filename,
                        content_disposition_type="attachment", headers=headers)


def _max_bytes(user: User) -> int:
    return int(effective_policy(user).get("max_upload_mb", 20)) * 1024 * 1024


@router.put("/files/{fid}/content")
@serialize_file_write("fid")
async def put_content(fid: str, request: Request, dctx: DeviceContext = Depends(require_device), db: AsyncSession = Depends(get_db)):
    """Sovrascrive il contenuto. `If-Match` obbligatorio: lo sha256 della versione da cui il client e' partito (o `*` per
    forzare). Se il file e' cambiato nel frattempo risponde 412 con lo stato attuale: il client tiene la copia locale
    come "(conflitto di …)" e riparte da quello del server. Il contenuto precedente va in cronologia."""
    asset, _ = await require_access(db, "file", fid, dctx.user, write=True)
    if not asset.path:
        raise HTTPException(400, "Questo file non e' modificabile")
    match = request.headers.get("if-match", "").strip().strip('"')
    if not match:
        raise HTTPException(428, "Serve l'header If-Match con lo sha256 della versione di partenza (o *)")
    data = await request.body()
    if len(data) > _max_bytes(dctx.user):
        raise HTTPException(413, "File troppo grande")
    current = await sync.ensure_sha(db, asset)
    if match != "*" and match.lower() != current:
        await db.commit()
        return _json(412, {"detail": "Il file e' cambiato sul server", "file": await _item(db, asset, dctx.user)})
    new_sha = hashlib.sha256(data).hexdigest()
    if new_sha == current:
        await db.commit()
        return await _item(db, asset, dctx.user)
    from ..services import quota as _quota
    await _quota.ensure_space(db, dctx.user, len(data) - (asset.size or 0))
    # antivirus (se configurato) sul contenuto nuovo prima che tocchi il file, poi sostituzione atomica
    tmp = Path(f"{asset.path}.craftsync-{uuid.uuid4().hex[:8]}.tmp")
    tmp.write_bytes(data)
    verdict = await files_api._scan_upload(tmp)
    if verdict:
        tmp.unlink(missing_ok=True)
        raise HTTPException(422, f"File rifiutato dal controllo antivirus: {verdict}")
    from ..services import versions as _versions
    await _versions.snapshot(db, asset, dctx.user, f"Craft Sync · {dctx.device.name}")
    os.replace(tmp, asset.path)
    asset.size, asset.sha256 = len(data), new_sha
    pending = False
    if not (asset.mime or "").startswith(("image/", "video/", "audio/")):
        asset.extracted_text = extract_text(Path(asset.path), asset.mime) or ""
        asset.index_status = "pending" if rag.is_indexable(asset) else "none"
        pending = asset.index_status == "pending"
        await db.execute(delete(Chunk).where(Chunk.file_id == asset.id))
    await db.commit()
    if pending:
        files_api._schedule_reindex(asset.id)
    log.info("craft-sync: %s sovrascritto da %s/%s (%d byte)", asset.filename, dctx.user.username, dctx.device.name, len(data))
    return await _item(db, asset, dctx.user)


@router.post("/files", status_code=201)
async def create_file(request: Request, folder_id: str | None = None, filename: str = "", on_conflict: str = "fail",
                      dctx: DeviceContext = Depends(require_device), db: AsyncSession = Depends(get_db)):
    """Un file nuovo dal computer (corpo = contenuto). Nome gia' usato nella cartella: 409 con il file esistente, oppure
    `on_conflict=rename` per avere "nome 2.ext" come nel Finder."""
    from urllib.parse import unquote
    name = _clean_name(filename or unquote(request.headers.get("X-Filename", "")))
    if not name:
        raise HTTPException(400, "Nome del file mancante")
    ext = Path(name).suffix.lower()
    data = await request.body()
    if len(data) > _max_bytes(dctx.user):
        raise HTTPException(413, "File troppo grande")
    from ..services import quota as _quota
    await _quota.ensure_space(db, dctx.user, len(data))
    dest = await workspace.folder_for_write(db, folder_id, dctx.user)
    owner_id = dest.owner_id if dest is not None and workspace.is_org(dest) else dctx.user.id
    unique = await unique_filename(db, owner_id, folder_id or None, name)
    if unique != name:
        if on_conflict != "rename":
            q = select(FileAsset).where(FileAsset.owner_id == owner_id, func.lower(FileAsset.filename) == name.lower())
            q = q.where(FileAsset.folder_id == folder_id) if folder_id else q.where(FileAsset.folder_id.is_(None))
            existing = (await db.execute(q)).scalars().first()
            payload = {"detail": f"Esiste gia' un file chiamato \"{name}\" in questa cartella"}
            if existing is not None:
                payload["file"] = await _item(db, existing, dctx.user)
            return _json(409, payload)
        name = unique
    fid = uuid.uuid4().hex
    path = settings.uploads_dir / f"{fid}{ext or '.bin'}"
    path.write_bytes(data)
    verdict = await files_api._scan_upload(path)
    if verdict:
        path.unlink(missing_ok=True)
        raise HTTPException(422, f"File rifiutato dal controllo antivirus: {verdict}")
    mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
    asset = FileAsset(id=fid, family_id=dctx.user.family_id, owner_id=owner_id, filename=name, mime=mime, size=len(data),
                      path=str(path), kind="upload", folder_id=folder_id or None, sha256=hashlib.sha256(data).hexdigest())
    if not mime.startswith(("image/", "video/", "audio/")):
        asset.extracted_text = extract_text(path, mime) or ""
    asset.index_status = "pending" if rag.is_indexable(asset) else "none"
    db.add(asset)
    await sync.touch_file(db, asset, content_changed=False)
    await db.commit()
    if asset.index_status == "pending":
        files_api._schedule_reindex(asset.id)
    return await _item(db, asset, dctx.user)


@router.patch("/files/{fid}")
async def patch_file(fid: str, body: files_api.FileRename, dctx: DeviceContext = Depends(require_device), db: AsyncSession = Depends(get_db)):
    """Rinomina e/o sposta (stesse regole dei Documenti nel browser)."""
    await files_api.rename_file(fid, body, dctx, db)
    asset = await db.get(FileAsset, fid)
    return await _item(db, asset, dctx.user)


@router.delete("/files/{fid}")
async def delete_file(fid: str, dctx: DeviceContext = Depends(require_device), db: AsyncSession = Depends(get_db)):
    return await files_api.delete_file(fid, dctx, db)


@router.get("/files/{fid}/versions")
async def versions(fid: str, dctx: DeviceContext = Depends(require_device), db: AsyncSession = Depends(get_db)):
    return await files_api.file_versions(fid, dctx, db)


@router.get("/files/{fid}/versions/{vid}")
async def version_content(fid: str, vid: str, dctx: DeviceContext = Depends(require_device), db: AsyncSession = Depends(get_db)):
    return await files_api.file_version_download(fid, vid, dctx, db)


# ---------------------------------------------------------------- cartelle ----
async def _folder_out(db: AsyncSession, fid: str, user: User) -> dict:
    f = await db.get(Folder, fid)
    if f is None:
        raise HTTPException(404, "Cartella non trovata")
    return sync.folder_item(f, user)


@router.post("/folders", status_code=201)
async def create_folder(body: folders_api.FolderIn, dctx: DeviceContext = Depends(require_device), db: AsyncSession = Depends(get_db)):
    out = await folders_api.create_folder(body, dctx, db)
    return await _folder_out(db, out["id"], dctx.user)


@router.patch("/folders/{fid}")
async def patch_folder(fid: str, body: folders_api.FolderPatch, dctx: DeviceContext = Depends(require_device), db: AsyncSession = Depends(get_db)):
    await folders_api.patch_folder(fid, body, dctx, db)
    return await _folder_out(db, fid, dctx.user)


@router.delete("/folders/{fid}")
async def delete_folder(fid: str, dctx: DeviceContext = Depends(require_device), db: AsyncSession = Depends(get_db)):
    """Elimina la cartella: file e sottocartelle risalgono di un livello (come nei Documenti), e il client li sposta."""
    return await folders_api.delete_folder(fid, dctx, db)
