"""Craft Sync: i Documenti di Emily sincronizzati con una cartella del computer (api/sync.py).

Il modello e' quello di OneDrive/Drive, ma semplice e verificabile:
- ogni file e ogni cartella portano un **numero di cambiamento** `seq` preso da un contatore globale (`sync_counter`): ogni
  creazione, sovrascrittura, rinomina, spostamento o eliminazione lo fa avanzare. Un client chiede `/api/sync/changes?since=N`
  e riceve tutto cio' che e' cambiato dopo N (file, cartelle e lapidi di cio' che e' stato eliminato) con il nuovo cursore;
- il **contenuto** ha un'impronta `sha256` (calcolata quando serve e azzerata a ogni sovrascrittura): il client la confronta
  con quella del file locale e la manda in `If-Match` quando carica, cosi' una scrittura su un file cambiato nel frattempo
  viene rifiutata (412) invece di sovrascrivere il lavoro di un collega;
- i **dispositivi** si collegano con un codice mostrato in Impostazioni e ricevono un token portatore (`cs_…`), di cui il server
  conserva solo l'hash; il token si revoca da Impostazioni. Mai nei log.
Le righe nate prima di questa versione non hanno `seq`: lo ricevono la prima volta che un client le chiede (`backfill`).
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import set_committed_value

from ..db import get_db
from ..models import (
    FileAsset,
    Folder,
    Role,
    SyncCounter,
    SyncDevice,
    SyncPairingCode,
    SyncTombstone,
    User,
    now,
)
from . import events, workspace

log = logging.getLogger(__name__)

PAIRING_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # niente 0/O, 1/I: si legge e si ricopia senza errori
PAIRING_MINUTES = 10
TOKEN_PREFIX = "cs_"
CHANGES_LIMIT = 500          # righe per risposta di /changes (il client continua con il cursore se `more`)
HASH_PER_REQUEST = 200       # impronte calcolate al volo in una sola risposta (file nati prima di Craft Sync)
SEEN_THROTTLE_S = 60         # last_seen_at del dispositivo: aggiornato al massimo una volta al minuto


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


# ---------------------------------------------------------------- contatore e marcature ----
async def next_seq(db: AsyncSession) -> int:
    """Il prossimo numero di cambiamento (atomico: UPDATE … RETURNING)."""
    for _ in range(2):
        res = await db.execute(update(SyncCounter).where(SyncCounter.id == 1).values(value=SyncCounter.value + 1)
                               .returning(SyncCounter.value))
        v = res.scalar_one_or_none()
        if v is not None:
            return int(v)
        db.add(SyncCounter(id=1, value=0))
        await db.flush()
    raise RuntimeError("sync counter unavailable")


async def current_seq(db: AsyncSession) -> int:
    v = (await db.execute(select(SyncCounter.value).where(SyncCounter.id == 1))).scalar_one_or_none()
    return int(v or 0)


async def touch_file(db: AsyncSession, asset: FileAsset, content_changed: bool = True, announce: bool = True) -> None:
    """Il file e' cambiato (o sta per esserlo): nuovo seq, data e, se cambia il contenuto, impronta da ricalcolare."""
    asset.seq = await next_seq(db)
    asset.updated_at = now()
    if content_changed:
        asset.sha256 = ""
    if announce:
        await _announce(db, asset.owner_id, asset.family_id, asset.folder_id)


async def touch_folder(db: AsyncSession, folder: Folder, announce: bool = True) -> None:
    folder.seq = await next_seq(db)
    folder.updated_at = now()
    if announce:
        await _announce(db, folder.owner_id, folder.family_id, folder.id if workspace.is_org(folder) else None)


async def tombstone(db: AsyncSession, kind: str, resource_id: str, family_id: str, owner_id: str, org: bool, dept: str = "") -> None:
    """Lapide: anche quando un file esce dal perimetro di chi lo vedeva (altro reparto, cartella personale dell'admin),
    cosi' il suo computer lo toglie. Un client che riceve lapide e riga viva dello stesso id applica nell'ordine dei seq."""
    db.add(SyncTombstone(seq=await next_seq(db), kind=kind, resource_id=resource_id, family_id=family_id,
                         owner_id=owner_id, org=org, dept=dept or ""))
    await _announce_users(await _audience(db, owner_id, family_id, org))


async def folder_left(db: AsyncSession, asset: FileAsset, old_folder: Folder | None, old_owner: str, new_folder: Folder | None) -> None:
    """Un file spostato fra l'archivio e le cartelle personali, fra reparti, o che cambia proprietario, sparisce dal perimetro
    di qualcuno: lapide per il vecchio perimetro (chi lo vede ancora riceve subito dopo la riga viva, con seq maggiore)."""
    old_org, new_org = workspace.is_org(old_folder), workspace.is_org(new_folder)
    old_dept = (old_folder.dept or "") if old_folder is not None else ""
    new_dept = (new_folder.dept or "") if new_folder is not None else ""
    if old_org != new_org or (old_org and old_dept != new_dept) or old_owner != asset.owner_id:
        await tombstone(db, "file", asset.id, asset.family_id, old_owner, old_org, old_dept)


async def _audience(db: AsyncSession, owner_id: str, family_id: str, org: bool) -> list[str]:
    """A chi dire che qualcosa e' cambiato: al proprietario; per l'archivio dell'organizzazione a tutti gli adulti
    (ognuno poi riceve da /changes solo cio' che puo' vedere)."""
    if not org:
        return [owner_id]
    rows = (await db.execute(select(User.id).where(User.family_id == family_id, User.role != Role.child,
                                                    User.is_active.is_(True)))).scalars().all()
    return list(rows)


async def _announce(db: AsyncSession, owner_id: str, family_id: str, folder_id: str | None) -> None:
    org = False
    if folder_id:
        f = await db.get(Folder, folder_id)
        org = workspace.is_org(f)
    await _announce_users(await _audience(db, owner_id, family_id, org))


async def _announce_users(user_ids: list[str]) -> None:
    """Evento "sync" alle schede e agli agenti collegati. Con un piccolo ritardo: chi chiama ha ancora la transazione aperta,
    e un client che chiedesse /changes subito non vedrebbe nulla. Il client rilegge comunque ogni qualche minuto."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    for uid in user_ids:
        loop.call_later(0.5, events.publish, uid, "sync", {})


def file_sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


async def ensure_sha(db: AsyncSession, asset: FileAsset) -> str:
    """L'impronta del contenuto, calcolata se manca. Chi legge non la salva alla cieca: l'UPDATE vale solo se nel frattempo
    nessuno ha toccato la riga (stesso seq, impronta ancora vuota), altrimenti un'impronta del contenuto vecchio
    resterebbe salvata accanto a un seq nuovo e un If-Match la riterrebbe valida."""
    if asset.sha256:
        return asset.sha256
    if not asset.path or not Path(asset.path).is_file():
        return ""
    try:
        digest = file_sha256(asset.path)
        size = Path(asset.path).stat().st_size
    except OSError as e:
        log.warning("Impronta di %s non calcolata: %s", asset.filename, e)
        return ""
    await db.execute(update(FileAsset).where(FileAsset.id == asset.id, FileAsset.sha256 == "", FileAsset.seq == asset.seq)
                     .values(sha256=digest, size=size))
    set_committed_value(asset, "sha256", digest)   # per questa risposta, senza sporcare la riga nella sessione
    set_committed_value(asset, "size", size)
    return digest


# ---------------------------------------------------------------- cosa vede un utente ----
async def org_folder_ids(db: AsyncSession, user: User) -> set[str]:
    return {f.id for f in await workspace.visible_org_folders(db, user)}


def file_access(asset: FileAsset, user: User, org_ids: set[str]) -> str:
    """owner | write | read per un file nel perimetro di Craft Sync (i propri e quelli dell'archivio visibile)."""
    if asset.owner_id == user.id and asset.folder_id not in org_ids:
        return "owner"
    from .apps import is_app_admin
    return "write" if is_app_admin(user, "archivio") else "read"


def file_item(asset: FileAsset, user: User, org_ids: set[str]) -> dict:
    return {"type": "file", "id": asset.id, "seq": asset.seq, "folder_id": asset.folder_id, "filename": asset.filename,
            "size": asset.size, "mime": asset.mime, "sha256": asset.sha256 or "", "kind": asset.kind,
            "owner_id": asset.owner_id, "access": file_access(asset, user, org_ids),
            "project_id": asset.project_id, "conversation_id": asset.conversation_id,
            "created_at": asset.created_at, "updated_at": asset.updated_at or asset.created_at}


def folder_item(folder: Folder, user: User) -> dict:
    return {"type": "folder", "id": folder.id, "seq": folder.seq, "parent_id": folder.parent_id, "name": folder.name,
            "folder_kind": folder.kind or "", "dept": folder.dept or "", "owner_id": folder.owner_id,
            "conversation_id": folder.conversation_id, "can_write": workspace.can_write_folder(folder, user),
            "created_at": folder.created_at, "updated_at": folder.updated_at or folder.created_at}


def _file_scope(user: User, org_ids: set[str]):
    return (FileAsset.family_id == user.family_id, FileAsset.path.isnot(None),
            or_(FileAsset.owner_id == user.id, FileAsset.folder_id.in_(org_ids or ["-"])))


def _folder_scope(user: User, org_ids: set[str]):
    own = (Folder.owner_id == user.id) & Folder.kind.notin_(workspace.ORG_KINDS)
    return (Folder.family_id == user.family_id, or_(own, Folder.id.in_(org_ids or ["-"])))


async def backfill(db: AsyncSession, user: User, org_ids: set[str]) -> None:
    """Le righe senza seq (nate prima di Craft Sync) lo ricevono ora, in ordine di creazione, cosi' compaiono in /changes."""
    files = (await db.execute(select(FileAsset).where(*_file_scope(user, org_ids), FileAsset.seq.is_(None))
                              .order_by(FileAsset.created_at.asc()).limit(CHANGES_LIMIT))).scalars().all()
    folders = (await db.execute(select(Folder).where(*_folder_scope(user, org_ids), Folder.seq.is_(None))
                                .order_by(Folder.created_at.asc()).limit(CHANGES_LIMIT))).scalars().all()
    for f in folders:
        f.seq = await next_seq(db)
    for a in files:
        a.seq = await next_seq(db)
    if files or folders:
        await db.commit()


async def tree(db: AsyncSession, user: User) -> list[dict]:
    """Tutte le cartelle che l'utente vede: le sue (CHAT compresa) e quelle dell'archivio del suo reparto."""
    if user.role == Role.admin:
        await workspace.ensure_org_tree(db, user)
        await db.commit()
    org_ids = await org_folder_ids(db, user)
    await backfill(db, user, org_ids)
    rows = (await db.execute(select(Folder).where(*_folder_scope(user, org_ids)))).scalars().all()
    return [folder_item(f, user) for f in sorted(rows, key=lambda f: (f.kind != "org", f.kind != "chat", f.name.lower()))]


async def changes(db: AsyncSession, user: User, since: int, limit: int = CHANGES_LIMIT) -> dict:
    """Tutto cio' che e' cambiato dopo il cursore `since`, in ordine di seq: file, cartelle e lapidi."""
    limit = max(1, min(int(limit or CHANGES_LIMIT), CHANGES_LIMIT))
    org_ids = await org_folder_ids(db, user)
    await backfill(db, user, org_ids)
    files = (await db.execute(select(FileAsset).where(*_file_scope(user, org_ids), FileAsset.seq > since)
                              .order_by(FileAsset.seq.asc()).limit(limit + 1))).scalars().all()
    folders = (await db.execute(select(Folder).where(*_folder_scope(user, org_ids), Folder.seq > since)
                                .order_by(Folder.seq.asc()).limit(limit + 1))).scalars().all()
    from .apps import is_app_admin
    if not org_ids:
        org_tombs = SyncTombstone.id < 0           # niente archivio: niente lapidi dell'archivio
    elif is_app_admin(user, "archivio"):
        org_tombs = SyncTombstone.org.is_(True)
    else:
        org_tombs = and_(SyncTombstone.org.is_(True), SyncTombstone.dept.in_(["", *workspace.user_departments(user)]))
    tombs = (await db.execute(select(SyncTombstone).where(
        SyncTombstone.family_id == user.family_id, SyncTombstone.seq > since,
        or_(SyncTombstone.owner_id == user.id, org_tombs)).order_by(SyncTombstone.seq.asc()).limit(limit + 1))).scalars().all()
    rows: list[tuple[int, str, object]] = [(a.seq or 0, "file", a) for a in files] + [(f.seq or 0, "folder", f) for f in folders] \
        + [(t.seq, "tomb", t) for t in tombs]
    rows.sort(key=lambda r: r[0])
    more = len(rows) > limit
    rows = rows[:limit]
    hashed = 0
    out: list[dict] = []
    alive = {obj.id for _, what, obj in rows if what != "tomb"}
    for seq, what, obj in rows:
        if what == "file":
            if not obj.sha256 and hashed < HASH_PER_REQUEST:
                await ensure_sha(db, obj)
                hashed += 1
            out.append(file_item(obj, user, org_ids))
        elif what == "folder":
            out.append(folder_item(obj, user))
        elif obj.resource_id not in alive:   # spostato e ancora visibile: la riga viva (seq maggiore) basta
            out.append({"type": obj.kind, "id": obj.resource_id, "seq": obj.seq, "deleted": True, "deleted_at": obj.deleted_at})
    if hashed:
        await db.commit()
    cursor = rows[-1][0] if rows else max(since, await current_seq(db))
    return {"since": since, "cursor": cursor, "more": more, "items": out}


# ---------------------------------------------------------------- dispositivi ----
def new_pairing_code() -> str:
    raw = "".join(secrets.choice(PAIRING_ALPHABET) for _ in range(8))
    return f"{raw[:4]}-{raw[4:]}"


def normalize_code(code: str) -> str:
    return "".join(ch for ch in (code or "").upper() if ch in PAIRING_ALPHABET)


async def create_pairing_code(db: AsyncSession, user: User) -> tuple[str, datetime]:
    """Un codice nuovo (quelli precedenti non usati decadono): vale PAIRING_MINUTES, una volta sola."""
    from sqlalchemy import delete as _delete
    await db.execute(_delete(SyncPairingCode).where(SyncPairingCode.user_id == user.id))   # uno solo alla volta, niente righe vecchie
    code = new_pairing_code()
    exp = now() + timedelta(minutes=PAIRING_MINUTES)
    db.add(SyncPairingCode(user_id=user.id, code_hash=_hash(normalize_code(code)), expires_at=exp))
    await db.commit()
    return code, exp


async def redeem_pairing_code(db: AsyncSession, code: str, name: str, platform: str, app_version: str, ip: str) -> tuple[SyncDevice, str, User]:
    """Scambia il codice con un dispositivo nuovo e il suo token (restituito una volta sola)."""
    norm = normalize_code(code)
    if len(norm) != 8:
        raise HTTPException(400, "Codice non valido o scaduto")
    row = (await db.execute(select(SyncPairingCode).where(SyncPairingCode.code_hash == _hash(norm), SyncPairingCode.used.is_(False)))).scalars().first()
    if not row or row.expires_at.replace(tzinfo=timezone.utc) < now():
        raise HTTPException(400, "Codice non valido o scaduto")
    user = await db.get(User, row.user_id)
    if not user or not user.is_active or user.role == Role.child:
        raise HTTPException(400, "Codice non valido o scaduto")
    burned = await db.execute(update(SyncPairingCode).where(SyncPairingCode.id == row.id, SyncPairingCode.used.is_(False)).values(used=True))
    if burned.rowcount != 1:   # due richieste insieme con lo stesso codice: una sola vince
        raise HTTPException(400, "Codice non valido o scaduto")
    dev, token = await create_device(db, user, name, platform, app_version, ip)
    return dev, token, user


async def create_device(db: AsyncSession, user: User, name: str, platform: str, app_version: str, ip: str) -> tuple[SyncDevice, str]:
    """Un dispositivo nuovo per l'utente e il suo token (restituito una volta sola; qui resta solo l'hash). Salva anche
    cio' che e' in sospeso nella sessione (p.es. l'azzeramento dei tentativi falliti dopo un login riuscito)."""
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    dev = SyncDevice(family_id=user.family_id, user_id=user.id, name=(name or "Computer").strip()[:120],
                     platform=(platform or "").strip().lower()[:20], app_version=(app_version or "")[:40],
                     token_hash=_hash(token), last_seen_at=now(), last_ip=ip[:64])
    db.add(dev)
    await db.commit()
    return dev, token


def device_item(d: SyncDevice, names: dict[str, str] | None = None) -> dict:
    out = {"id": d.id, "name": d.name, "platform": d.platform, "app_version": d.app_version, "created_at": d.created_at,
           "last_seen_at": d.last_seen_at, "last_ip": d.last_ip, "revoked": d.revoked, "user_id": d.user_id}
    if names is not None:
        out["user_name"] = names.get(d.user_id, "")
    return out


class DeviceContext:
    """Chi fa la richiesta con un token di Craft Sync: l'utente e il suo dispositivo."""

    def __init__(self, user: User, device: SyncDevice):
        self.user = user
        self.device = device
        self.session = None   # per riusare le funzioni dei router che leggono solo ctx.user


async def require_device(request: Request, db: AsyncSession = Depends(get_db)) -> DeviceContext:
    auth = request.headers.get("authorization", "")
    scheme, _, token = auth.partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token.startswith(TOKEN_PREFIX) or len(token) > 200:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token di Craft Sync mancante", headers={"WWW-Authenticate": "Bearer"})
    dev = (await db.execute(select(SyncDevice).where(SyncDevice.token_hash == _hash(token)))).scalars().first()
    if not dev or dev.revoked:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Dispositivo non collegato o revocato", headers={"WWW-Authenticate": "Bearer"})
    user = await db.get(User, dev.user_id)
    if not user or not user.is_active or user.role == Role.child:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Dispositivo non collegato o revocato", headers={"WWW-Authenticate": "Bearer"})
    t = now()
    if not dev.last_seen_at or (t - dev.last_seen_at.replace(tzinfo=timezone.utc)).total_seconds() > SEEN_THROTTLE_S:
        dev.last_seen_at = t
        from ..security.sessions import client_ip
        dev.last_ip = client_ip(request)[:64]
        await db.commit()
    from .activity import remember_actor
    remember_actor(request, user.id, user.family_id)
    return DeviceContext(user, dev)
