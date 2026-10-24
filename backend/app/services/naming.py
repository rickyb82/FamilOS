"""Nomi di file unici, come nel Finder: "documento.docx" → "documento 2.docx" → "documento 3.docx"."""
from __future__ import annotations

import re
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import FileAsset


def split_ext(name: str) -> tuple[str, str]:
    low = name.lower()
    if low.endswith(".ideacraft.json"):
        return name[:-15], name[-15:]
    suf = Path(name).suffix
    return (name[: -len(suf)] if suf else name), suf


def next_name(name: str, taken: set[str]) -> str:
    """Primo nome libero nella serie nome, nome 2, nome 3… (confronto senza maiuscole)."""
    if name.lower() not in taken:
        return name
    stem, ext = split_ext(name)
    stem = re.sub(r" \d+$", "", stem) or "file"
    n = 2
    while f"{stem} {n}{ext}".lower() in taken:
        n += 1
    return f"{stem} {n}{ext}"


async def unique_filename(db: AsyncSession, owner_id: str, folder_id: str | None, filename: str, exclude_id: str | None = None) -> str:
    """Nome non ancora usato dall'utente nella stessa cartella di Documenti."""
    q = select(FileAsset.filename).where(FileAsset.owner_id == owner_id)
    q = q.where(FileAsset.folder_id == folder_id) if folder_id else q.where(FileAsset.folder_id.is_(None))
    if exclude_id:
        q = q.where(FileAsset.id != exclude_id)
    taken = {n.lower() for (n,) in (await db.execute(q)).all()}
    return next_name(filename, taken)


def unique_path(path: Path) -> Path:
    """Percorso libero su disco con lo stesso schema (nome 2.ext)."""
    if not path.exists():
        return path
    stem, ext = split_ext(path.name)
    stem = re.sub(r" \d+$", "", stem) or "file"
    n = 2
    while (path.parent / f"{stem} {n}{ext}").exists():
        n += 1
    return path.parent / f"{stem} {n}{ext}"


async def unique_folder_name(db: AsyncSession, owner_id: str, parent_id: str | None, name: str, exclude_id: str | None = None) -> str:
    """Nome di cartella non ancora usato dall'utente nello stesso livello (stessa serie: nome, nome 2, nome 3…)."""
    from ..models import Folder
    q = select(Folder.name).where(Folder.owner_id == owner_id)
    q = q.where(Folder.parent_id == parent_id) if parent_id else q.where(Folder.parent_id.is_(None))
    if exclude_id:
        q = q.where(Folder.id != exclude_id)
    taken = {n.lower() for (n,) in (await db.execute(q)).all()}
    if name.lower() not in taken:
        return name
    stem = re.sub(r" \d+$", "", name) or "cartella"
    n = 2
    while f"{stem} {n}".lower() in taken:
        n += 1
    return f"{stem} {n}"
