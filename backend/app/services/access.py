"""Controllo accessi alle risorse (proprietario, condivisione, supervisione genitori)."""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Conversation, FileAsset, Project, Role, Share, User
from ..policy.permissions import effective_policy

RESOURCE_MODEL = {"conversation": Conversation, "project": Project, "file": FileAsset}


async def get_share(db: AsyncSession, rtype: str, rid: str, user: User) -> Share | None:
    return (await db.execute(select(Share).where(
        Share.resource_type == rtype, Share.resource_id == rid,
        (Share.target_user_id == user.id) | (Share.target_user_id.is_(None))
    ).order_by(Share.target_user_id.desc()))).scalars().first()


async def can_parent_view(db: AsyncSession, parent: User, owner_id: str) -> bool:
    if parent.role != Role.admin:
        return False
    owner = await db.get(User, owner_id)
    return bool(owner and owner.role == Role.child and owner.family_id == parent.family_id
                and effective_policy(owner).get("parent_can_view", True))


async def access_level(db: AsyncSession, rtype: str, resource, user: User) -> str | None:
    """Ritorna 'owner' | 'write' | 'read' | 'parent' | None."""
    if resource is None or resource.family_id != user.family_id:
        return None
    if resource.owner_id == user.id:
        return "owner"
    share = await get_share(db, rtype, resource.id, user)
    if share:
        return share.permission
    # file di progetto condiviso ereditano il permesso del progetto
    if rtype == "file" and getattr(resource, "project_id", None):
        pshare = await get_share(db, "project", resource.project_id, user)
        if pshare:
            return pshare.permission
    if rtype == "file" and getattr(resource, "folder_id", None):
        # file nell'archivio dell'organizzazione: lettura per chi e' del reparto, scrittura per l'amministratore
        from ..models import Folder
        from . import workspace
        fo = await db.get(Folder, resource.folder_id)
        if workspace.is_org(fo) and workspace.can_see_folder(fo, user):
            from .apps import is_app_admin
            return "write" if is_app_admin(user, "archivio") else "read"
    if rtype == "file" and getattr(resource, "conversation_id", None):
        conv = await db.get(Conversation, resource.conversation_id)
        if conv and conv.owner_id == user.id:
            return "read"
        cshare = await get_share(db, "conversation", resource.conversation_id, user)
        if cshare:
            return "read"
    if rtype == "conversation" and resource.project_id:
        pshare = await get_share(db, "project", resource.project_id, user)
        if pshare:
            return pshare.permission
    if await can_parent_view(db, user, resource.owner_id):
        return "parent"
    return None


async def require_access(db: AsyncSession, rtype: str, rid: str, user: User, write: bool = False):
    resource = await db.get(RESOURCE_MODEL[rtype], rid)
    level = await access_level(db, rtype, resource, user)
    if level is None:
        raise HTTPException(404, "Risorsa non trovata")
    if write and level not in ("owner", "write"):
        raise HTTPException(403, "Permesso di sola lettura")
    return resource, level
