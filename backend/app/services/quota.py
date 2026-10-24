"""Quota di spazio per utente: somma dei file di cui e' proprietario (upload, generati dalle app e dalla chat) piu' le versioni
conservate. Il limite e' `User.quota_bytes` se impostato dal super amministratore, altrimenti `default_quota_gb` (50 GB)."""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..models import FileAsset, FileVersion, User

settings = get_settings()
GB = 1024 ** 3


def default_quota_bytes() -> int:
    try:
        return int(float(getattr(settings, "default_quota_gb", "50") or 50) * GB)
    except Exception:
        return 50 * GB


def quota_bytes(user: User) -> int:
    """0 = illimitato."""
    q = getattr(user, "quota_bytes", None)
    return int(q) if q is not None else default_quota_bytes()


async def used_bytes(db: AsyncSession, user_id: str) -> int:
    files = (await db.execute(select(func.coalesce(func.sum(FileAsset.size), 0)).where(FileAsset.owner_id == user_id))).scalar() or 0
    versions = (await db.execute(select(func.coalesce(func.sum(FileVersion.size), 0)).join(FileAsset, FileAsset.id == FileVersion.file_id)
                                 .where(FileAsset.owner_id == user_id))).scalar() or 0
    return int(files) + int(versions)


async def used_by_user(db: AsyncSession, family_id: str) -> dict[str, int]:
    """Spazio usato da ogni membro della famiglia/organizzazione (per il pannello)."""
    files = dict((await db.execute(select(FileAsset.owner_id, func.sum(FileAsset.size)).where(FileAsset.family_id == family_id)
                                   .group_by(FileAsset.owner_id))).all())
    versions = dict((await db.execute(select(FileAsset.owner_id, func.sum(FileVersion.size)).join(FileAsset, FileAsset.id == FileVersion.file_id)
                                      .where(FileAsset.family_id == family_id).group_by(FileAsset.owner_id))).all())
    out: dict[str, int] = {}
    for k, v in files.items():
        out[k] = out.get(k, 0) + int(v or 0)
    for k, v in versions.items():
        out[k] = out.get(k, 0) + int(v or 0)
    return out


def human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


async def ensure_space(db: AsyncSession, user: User, extra: int) -> None:
    """413 se aggiungere `extra` byte supererebbe la quota dell'utente (0 = illimitata)."""
    limit = quota_bytes(user)
    if limit <= 0 or extra <= 0:
        return
    used = await used_bytes(db, user.id)
    if used + extra > limit:
        raise HTTPException(413, f"Spazio esaurito: usati {human(used)} di {human(limit)}, servono altri {human(extra)}. "
                                 "Elimina qualche file (o chiedi all'amministratore di alzare la quota).")
