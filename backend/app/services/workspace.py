"""Spazio condiviso della famiglia e cartelle personali delle conversazioni."""
from __future__ import annotations

import logging
import asyncio

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Chunk, Conversation, Family, FileAsset, Folder, Role, User

log = logging.getLogger(__name__)

DEPARTMENTS: dict[str, str] = {}  # retained only for schema compatibility
LEGACY_DEPARTMENTS = {"tecnico", "commerciale", "amministrativo"}
_tree_lock = asyncio.Lock()
ORG_KINDS = ("org", "dept")
CHAT_ROOT_NAME = "CHAT"


def is_org(f: Folder | None) -> bool:
    return bool(f and f.kind in ORG_KINDS)


def user_departments(user: User) -> list[str]:
    return []


def can_see_folder(f: Folder, user: User) -> bool:
    """Chi vede una cartella: le proprie; quelle dell'organizzazione se admin, oppure se il reparto coincide (o e' per tutti)."""
    if f.owner_id == user.id and not is_org(f):
        return True
    if is_org(f):
        if user.role == Role.child or f.family_id != user.family_id:
            return False
        from .apps import app_enabled
        return app_enabled(user, "archivio")
    return False


def can_write_folder(f: Folder | None, user: User) -> bool:
    """Chi puo' creare/caricare/spostare dentro una cartella: nelle proprie tutti, nell'organizzazione solo l'amministratore."""
    if f is None:
        return True
    if is_org(f):
        from .apps import is_app_admin
        return is_app_admin(user, "archivio") and f.family_id == user.family_id
    return f.owner_id == user.id


async def folder_for_write(db: AsyncSession, folder_id: str | None, user: User) -> Folder | None:
    """La cartella di destinazione di un upload/spostamento, o 404/403."""
    if not folder_id:
        return None
    f = await db.get(Folder, folder_id)
    if not f or not can_see_folder(f, user):
        raise HTTPException(404, "Cartella non trovata")
    if not can_write_folder(f, user):
        raise HTTPException(403, "Nello spazio condiviso della famiglia carica solo l'amministratore")
    return f


# ---------------------------------------------------------------- organizzazione ----
async def org_root(db: AsyncSession, family_id: str) -> Folder | None:
    return (await db.execute(select(Folder).where(Folder.family_id == family_id, Folder.kind == "org"))).scalars().first()


async def ensure_org_tree(db: AsyncSession, admin: User) -> Folder:
    """Crea lo spazio famiglia e migra i vecchi reparti senza eliminare documenti."""
    async with _tree_lock:
        from . import sync
        from .naming import unique_filename, unique_folder_name
        root = await org_root(db, admin.family_id)
        family = await db.get(Family, admin.family_id)
        name = (family.name if family else "") or "Famiglia"
        if not root:
            root = Folder(family_id=admin.family_id, owner_id=admin.id, parent_id=None, name=name, kind="org", color="#7c9b86")
            db.add(root)
            await db.flush()
            await sync.touch_folder(db, root, announce=False)
        elif root.name != name:
            root.name = name
            await sync.touch_folder(db, root, announce=False)
        legacy = (await db.execute(select(Folder).where(Folder.family_id == admin.family_id,
                  Folder.parent_id == root.id, Folder.kind == "dept", Folder.dept.in_(LEGACY_DEPARTMENTS)))).scalars().all()
        for folder in legacy:
            assets = (await db.execute(select(FileAsset).where(FileAsset.folder_id == folder.id))).scalars().all()
            for asset in assets:
                await sync.folder_left(db, asset, folder, asset.owner_id, root)
                asset.folder_id = root.id
                asset.filename = await unique_filename(db, asset.owner_id, root.id, asset.filename, exclude_id=asset.id)
                await move_chunks(db, asset.id, root.id)
                await sync.touch_file(db, asset, content_changed=False, announce=False)
                await db.flush()
            children = (await db.execute(select(Folder).where(Folder.parent_id == folder.id))).scalars().all()
            for child in children:
                child.parent_id = root.id
                child.name = await unique_folder_name(db, child.owner_id, root.id, child.name, exclude_id=child.id)
                await sync.touch_folder(db, child, announce=False)
                await db.flush()
            await sync.tombstone(db, "folder", folder.id, folder.family_id, folder.owner_id, True, "")
            await db.delete(folder)
        descendants_ = (await db.execute(select(Folder).where(Folder.family_id == admin.family_id,
                         Folder.kind == "dept", Folder.dept != ""))).scalars().all()
        for folder in descendants_:
            folder.dept = ""
            await sync.touch_folder(db, folder, announce=False)
        await db.commit()
        return root


async def visible_org_folders(db: AsyncSession, user: User) -> list[Folder]:
    from .apps import app_enabled
    if user.role == Role.child or not app_enabled(user, "archivio"):
        return []   # senza il software "Archivio" l'utente non vede le cartelle dell'organizzazione
    rows = (await db.execute(select(Folder).where(Folder.family_id == user.family_id, Folder.kind.in_(ORG_KINDS)))).scalars().all()
    return [f for f in rows if can_see_folder(f, user)]


async def dept_folder_ids(db: AsyncSession, user: User) -> set[str]:
    """Le cartelle di reparto (con sottocartelle) che fanno da base di conoscenza per questo utente."""
    return {f.id for f in await visible_org_folders(db, user) if f.kind in ORG_KINDS}


# ---------------------------------------------------------------- CHAT ----
async def chat_root(db: AsyncSession, user_id: str, family_id: str, create: bool = True) -> Folder | None:
    root = (await db.execute(select(Folder).where(Folder.owner_id == user_id, Folder.kind == "chat"))).scalars().first()
    if not root and create:
        root = Folder(family_id=family_id, owner_id=user_id, parent_id=None, name=CHAT_ROOT_NAME, kind="chat", color="#2563eb")
        db.add(root)
        await db.flush()
    return root


def _title_name(title: str) -> str:
    import re
    n = re.sub(r"[\x00-\x1f/\\]", " ", title or "").strip()[:80]
    return n or "Chat"


async def conv_folder(db: AsyncSession, conv: Conversation, create: bool = True) -> Folder | None:
    """La sottocartella CHAT/<titolo> della conversazione (creata al primo file)."""
    f = (await db.execute(select(Folder).where(Folder.conversation_id == conv.id, Folder.kind == "conv"))).scalars().first()
    if f or not create:
        return f
    root = await chat_root(db, conv.owner_id, conv.family_id)
    from .naming import unique_folder_name
    name = await unique_folder_name(db, conv.owner_id, root.id, _title_name(conv.title))
    f = Folder(family_id=conv.family_id, owner_id=conv.owner_id, parent_id=root.id, name=name, kind="conv", conversation_id=conv.id)
    db.add(f)
    await db.flush()
    return f


async def sync_conv_title(db: AsyncSession, conv: Conversation) -> None:
    """Il titolo della chat e' cambiato: rinomina la sua cartella (se esiste)."""
    f = await conv_folder(db, conv, create=False)
    if not f:
        return
    wanted = _title_name(conv.title)
    if f.name == wanted:
        return
    from .naming import unique_folder_name
    f.name = await unique_folder_name(db, conv.owner_id, f.parent_id, wanted, exclude_id=f.id)


async def place_in_conversation(db: AsyncSession, asset: FileAsset) -> None:
    """Un file nato o caricato in una chat senza cartella esplicita finisce in CHAT/<titolo chat>."""
    if asset.folder_id or not asset.conversation_id or not asset.path:
        return
    conv = await db.get(Conversation, asset.conversation_id)
    if not conv or conv.owner_id != asset.owner_id:
        return
    f = await conv_folder(db, conv)
    asset.folder_id = f.id
    from .naming import unique_filename
    asset.filename = await unique_filename(db, asset.owner_id, f.id, asset.filename, exclude_id=asset.id)


async def descendants(db: AsyncSession, folder_ids: set[str], family_id: str) -> set[str]:
    rows = (await db.execute(select(Folder.id, Folder.parent_id).where(Folder.family_id == family_id))).all()
    kids: dict[str | None, list[str]] = {}
    for i, p in rows:
        kids.setdefault(p, []).append(i)
    out, stack = set(folder_ids), list(folder_ids)
    while stack:
        cur = stack.pop()
        for k in kids.get(cur, []):
            if k not in out:
                out.add(k)
                stack.append(k)
    return out


async def context_folder_ids(db: AsyncSession, user: User, conv: Conversation | None) -> set[str]:
    """Le cartelle il cui contenuto e' contesto della chat: CHAT/<titolo> (con sottocartelle) + reparti dell'utente."""
    ids: set[str] = set()
    if conv:
        f = await conv_folder(db, conv, create=False)
        if f:
            ids.add(f.id)
    ids |= await dept_folder_ids(db, user)
    return await descendants(db, ids, user.family_id) if ids else set()


async def move_chunks(db: AsyncSession, file_id: str, folder_id: str | None) -> None:
    await db.execute(update(Chunk).where(Chunk.file_id == file_id).values(folder_id=folder_id))


async def protected_template(db: AsyncSession, asset: FileAsset) -> bool:
    """I file dell'organizzazione non si sovrascrivono mai dalla chat: il salvataggio viene dirottato su una copia."""
    if not asset.folder_id:
        return False
    f = await db.get(Folder, asset.folder_id)
    return is_org(f)


async def backfill_chat_folders(db: AsyncSession) -> int:
    """Una tantum (ma idempotente): i file nati o caricati nelle chat prima della cartella CHAT, ancora alla radice di Documenti,
    vanno in CHAT/<titolo della chat>. Tocca solo file con conversation_id e senza cartella."""
    rows = (await db.execute(select(FileAsset).where(FileAsset.conversation_id.isnot(None), FileAsset.folder_id.is_(None),
                                                     FileAsset.path.isnot(None), FileAsset.kind != "chat_history"))).scalars().all()
    n = 0
    for a in rows:
        before = a.folder_id
        await place_in_conversation(db, a)
        if a.folder_id != before:
            await move_chunks(db, a.id, a.folder_id)
            n += 1
        if n % 100 == 0:
            await db.commit()
    await db.commit()
    return n
