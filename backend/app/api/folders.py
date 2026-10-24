"""Cartelle di Documenti (stile Finder): create dall'utente, annidabili; i file si spostano dentro con folder_id.

Oltre alle cartelle personali ci sono quelle speciali (services/workspace.py): l'archivio dell'organizzazione (radice con il
nome dell'azienda + cartelle di reparto, scrive solo l'amministratore, vede chi e' del reparto) e CHAT (una sottocartella
automatica per ogni conversazione con file). Le radici speciali non si rinominano, spostano o eliminano.
"""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import Conversation, FileAsset, Folder, Role
from ..security.sessions import AuthContext, require_auth
from ..services import workspace
from ..services import sync as _sync

router = APIRouter(prefix="/folders", tags=["folders"])


class FolderIn(BaseModel):
    name: str
    parent_id: str | None = None
    color: str = ""


class FolderPatch(BaseModel):
    name: str | None = None
    parent_id: str | None = None   # "" = radice
    color: str | None = None


def _clean(name: str) -> str:
    n = re.sub(r"[\x00-\x1f/\\]", " ", name or "").strip()[:120]
    if not n:
        raise HTTPException(400, "Nome cartella non valido")
    return n


def _out(f: Folder, ctx: AuthContext, files: int = 0, subfolders: int = 0, root_id: str | None = None) -> dict:
    return {"id": f.id, "name": f.name, "parent_id": f.parent_id, "color": f.color or "", "created_at": f.created_at,
            "files": files, "subfolders": subfolders, "kind": f.kind or "", "dept": f.dept or "",
            "conversation_id": f.conversation_id, "can_write": workspace.can_write_folder(f, ctx.user),
            "system": _is_system(f, root_id)}


def _is_system(f: Folder, root_id: str | None) -> bool:
    """Radici gestite dal sistema: organizzazione, CHAT e le cartelle di reparto direttamente sotto la radice org."""
    return f.kind in ("org", "chat", "msg") or (f.kind == "dept" and bool(f.dept) and f.parent_id == root_id)


async def _system(db: AsyncSession, f: Folder) -> bool:
    root = await workspace.org_root(db, f.family_id)
    return _is_system(f, root.id if root else None)


async def _editable(db: AsyncSession, fid: str, ctx: AuthContext) -> Folder:
    """Cartella su cui l'utente puo' agire: le sue; quelle dell'organizzazione solo se admin."""
    f = await db.get(Folder, fid)
    if not f or not workspace.can_see_folder(f, ctx.user):
        raise HTTPException(404, "Cartella non trovata")
    if not workspace.can_write_folder(f, ctx.user):
        raise HTTPException(403, "Le cartelle condivise della famiglia le gestisce l'amministratore")
    return f


@router.get("")
async def list_folders(ctx: AuthContext = Depends(require_auth), db: AsyncSession = Depends(get_db)):
    """Le cartelle dell'utente + quelle dell'organizzazione che puo' vedere (l'albero lo ricostruisce il client),
    con il numero di file e sottocartelle. L'amministratore trova l'archivio dell'organizzazione gia' creato."""
    if ctx.user.role == Role.admin:   # l'albero dell'organizzazione nasce con il super amministratore
        await workspace.ensure_org_tree(db, ctx.user)
    await workspace.chat_root(db, ctx.user.id, ctx.user.family_id)
    await db.commit()
    own = (await db.execute(select(Folder).where(Folder.owner_id == ctx.user.id, Folder.kind.notin_(workspace.ORG_KINDS)))).scalars().all()
    org = await workspace.visible_org_folders(db, ctx.user)
    rows = sorted({f.id: f for f in [*own, *org]}.values(), key=lambda f: (f.kind != "org", f.kind != "chat", f.name.lower()))
    ids = [f.id for f in rows]
    nfiles = dict((await db.execute(select(FileAsset.folder_id, func.count()).where(FileAsset.folder_id.in_(ids or ["-"]))
                                    .group_by(FileAsset.folder_id))).all())
    nsub = dict((await db.execute(select(Folder.parent_id, func.count()).where(Folder.parent_id.in_(ids or ["-"]))
                                  .group_by(Folder.parent_id))).all())
    root_id = next((f.id for f in rows if f.kind == "org"), None)
    return [_out(f, ctx, nfiles.get(f.id, 0), nsub.get(f.id, 0), root_id) for f in rows]


@router.post("")
async def create_folder(body: FolderIn, ctx: AuthContext = Depends(require_auth), db: AsyncSession = Depends(get_db)):
    parent = await _editable(db, body.parent_id, ctx) if body.parent_id else None
    f = Folder(family_id=ctx.user.family_id, owner_id=ctx.user.id, parent_id=body.parent_id or None, name=_clean(body.name), color=body.color[:20])
    if parent and workspace.is_org(parent):
        # dentro l'organizzazione: la sottocartella eredita il reparto (sotto la radice: visibile a tutti i reparti)
        f.kind, f.dept = "dept", (parent.dept if parent.kind == "dept" else "")
    db.add(f)
    await _sync.touch_folder(db, f)
    await db.commit()
    return _out(f, ctx)


async def _descendants(db: AsyncSession, fid: str, family_id: str) -> set[str]:
    return await workspace.descendants(db, {fid}, family_id) - {fid}


@router.patch("/{fid}")
async def patch_folder(fid: str, body: FolderPatch, ctx: AuthContext = Depends(require_auth), db: AsyncSession = Depends(get_db)):
    f = await _editable(db, fid, ctx)
    system = await _system(db, f)
    if body.name is not None:
        if system:
            raise HTTPException(400, "Questa cartella e' gestita dal sistema: il nome della famiglia si cambia dal pannello Famiglia")
        f.name = _clean(body.name)
        if f.kind == "conv" and f.conversation_id:
            # rinominare la cartella della chat rinomina la chat
            conv = await db.get(Conversation, f.conversation_id)
            if conv and conv.owner_id == ctx.user.id:
                conv.title = f.name[:200]
    if body.color is not None:
        f.color = body.color[:20]
    if body.parent_id is not None:
        if system or f.kind == "conv":
            raise HTTPException(400, "Questa cartella non si puo' spostare")
        new_parent = body.parent_id or None
        parent = None
        if new_parent:
            parent = await _editable(db, new_parent, ctx)
            if new_parent == fid or new_parent in await _descendants(db, fid, ctx.user.family_id):
                raise HTTPException(400, "Non puoi spostare una cartella dentro se stessa")
        if workspace.is_org(f) != workspace.is_org(parent):
            raise HTTPException(400, "Le cartelle non si spostano tra lo spazio famiglia e le cartelle personali: sposta i file")
        f.parent_id = new_parent
        if workspace.is_org(f):
            # cambio di reparto: anche le sottocartelle seguono
            dept = parent.dept if parent.kind == "dept" else ""
            subs = await _descendants(db, fid, ctx.user.family_id)
            f.dept = dept
            if subs:
                await db.execute(update(Folder).where(Folder.id.in_(subs)).values(dept=dept))
    await _sync.touch_folder(db, f)
    await db.commit()
    return _out(f, ctx)


@router.delete("/{fid}")
async def delete_folder(fid: str, ctx: AuthContext = Depends(require_auth), db: AsyncSession = Depends(get_db)):
    """Elimina la cartella: i file e le sottocartelle risalgono nella cartella superiore (niente cancellazioni a cascata)."""
    f = await _editable(db, fid, ctx)
    if await _system(db, f):
        raise HTTPException(400, "Questa cartella e' gestita dal sistema e non si elimina")
    moved = (await db.execute(select(FileAsset).where(FileAsset.folder_id == fid))).scalars().all()
    for a in moved:
        a.folder_id = f.parent_id
        await workspace.move_chunks(db, a.id, f.parent_id)
        await _sync.touch_file(db, a, content_changed=False, announce=False)   # Craft Sync: il file ha cambiato cartella
    subs = (await db.execute(select(Folder).where(Folder.parent_id == fid))).scalars().all()
    for s in subs:
        s.parent_id = f.parent_id
        await _sync.touch_folder(db, s, announce=False)
    await _sync.tombstone(db, "folder", f.id, f.family_id, f.owner_id, workspace.is_org(f), f.dept or "")   # una notifica per tutti
    await db.delete(f)
    await db.commit()
    return {"ok": True}
