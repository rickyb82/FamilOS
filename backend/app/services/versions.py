"""Cronologia delle versioni dei file: snapshot prima di ogni sovrascrittura, elenco, ripristino."""
from __future__ import annotations

import logging
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..models import FileAsset, FileVersion, User

log = logging.getLogger(__name__)
settings = get_settings()
KEEP = 30               # versioni per file
COALESCE_S = 5 * 60     # salvataggi dello stesso autore entro 5 minuti: una sola versione (come Drive)


def versions_dir(file_id: str) -> Path:
    p = settings.data_dir / "versions" / file_id
    p.mkdir(parents=True, exist_ok=True)
    return p


async def snapshot(db: AsyncSession, asset: FileAsset, author: User | None, note: str) -> FileVersion | None:
    """Mette da parte il contenuto ATTUALE del file (prima che venga sovrascritto)."""
    if not asset or not asset.path or not Path(asset.path).is_file():
        return None
    from . import sync as _sync
    await _sync.touch_file(db, asset)   # Craft Sync: il contenuto sta cambiando (anche se la versione viene accorpata)
    src = Path(asset.path)
    now = datetime.now(timezone.utc)
    last = (await db.execute(select(FileVersion).where(FileVersion.file_id == asset.id).order_by(FileVersion.created_at.desc()).limit(1))).scalars().first()
    if last and author and last.author_id == author.id and (now - last.created_at).total_seconds() < COALESCE_S and last.note == note:
        # salvataggi ravvicinati dello stesso autore: la versione "di partenza" resta quella gia' salvata
        return last
    ext = "".join(src.suffixes[-2:]) if src.name.lower().endswith(".ideacraft.json") else src.suffix
    dst = versions_dir(asset.id) / f"{int(time.time() * 1000)}{ext}"
    try:
        shutil.copy2(src, dst)
    except OSError as e:
        log.warning("Snapshot di %s fallito: %s", asset.filename, e)
        return None
    v = FileVersion(id=uuid.uuid4().hex, file_id=asset.id, author_id=author.id if author else None,
                    author_name=author.display_name if author else "", note=note, path=str(dst), size=dst.stat().st_size)
    db.add(v)
    await db.flush()
    # ritenzione
    olds = (await db.execute(select(FileVersion).where(FileVersion.file_id == asset.id).order_by(FileVersion.created_at.desc()).offset(KEEP))).scalars().all()
    for o in olds:
        try:
            Path(o.path).unlink(missing_ok=True)
        except OSError:
            pass
        await db.delete(o)
    return v


async def list_versions(db: AsyncSession, file_id: str) -> list[FileVersion]:
    return list((await db.execute(select(FileVersion).where(FileVersion.file_id == file_id).order_by(FileVersion.created_at.desc()))).scalars().all())


async def restore(db: AsyncSession, asset: FileAsset, version: FileVersion, author: User) -> None:
    """Riporta il file alla versione scelta; lo stato attuale finisce in cronologia come 'prima del ripristino'."""
    await snapshot(db, asset, author, "prima del ripristino")
    shutil.copy2(version.path, asset.path)
    asset.size = Path(asset.path).stat().st_size
    from .sync import file_sha256
    asset.sha256 = file_sha256(asset.path)   # subito, cosi' nessun lettore la calcola a meta' strada
    await db.commit()
