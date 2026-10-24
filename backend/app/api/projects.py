from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import FileAsset, Project, Share, User
from ..policy.permissions import effective_policy
from ..schemas import ProjectCreate, ProjectOut, ProjectUpdate
from ..security.sessions import AuthContext, require_auth
from ..services import rag
from ..services.access import require_access

router = APIRouter(prefix="/projects", tags=["projects"])


@router.get("", response_model=list[ProjectOut])
async def list_projects(ctx: AuthContext = Depends(require_auth), db: AsyncSession = Depends(get_db)):
    own = (await db.execute(select(Project).where(Project.owner_id == ctx.user.id).order_by(Project.updated_at.desc()))).scalars().all()
    out = [ProjectOut.model_validate(p) for p in own]
    shared_ids = (await db.execute(select(Share.resource_id).where(
        Share.resource_type == "project", Share.family_id == ctx.user.family_id,
        or_(Share.target_user_id == ctx.user.id, Share.target_user_id.is_(None))))).scalars().all()
    if shared_ids:
        shared = (await db.execute(select(Project).where(Project.id.in_(shared_ids), Project.owner_id != ctx.user.id))).scalars().all()
        for p in shared:
            o = ProjectOut.model_validate(p)
            o.shared = True
            owner = await db.get(User, p.owner_id)
            o.owner_name = owner.display_name if owner else None
            out.append(o)
    return out


@router.post("", response_model=ProjectOut)
async def create_project(body: ProjectCreate, ctx: AuthContext = Depends(require_auth), db: AsyncSession = Depends(get_db)):
    if not effective_policy(ctx.user).get("can_create_projects", True):
        raise HTTPException(403, "Non puoi creare progetti")
    p = Project(family_id=ctx.user.family_id, owner_id=ctx.user.id, **body.model_dump())
    db.add(p)
    await db.commit()
    return ProjectOut.model_validate(p)


@router.get("/{pid}", response_model=ProjectOut)
async def get_project(pid: str, ctx: AuthContext = Depends(require_auth), db: AsyncSession = Depends(get_db)):
    p, level = await require_access(db, "project", pid, ctx.user)
    o = ProjectOut.model_validate(p)
    o.shared = level != "owner"
    return o


@router.patch("/{pid}", response_model=ProjectOut)
async def update_project(pid: str, body: ProjectUpdate, ctx: AuthContext = Depends(require_auth), db: AsyncSession = Depends(get_db)):
    p, _ = await require_access(db, "project", pid, ctx.user, write=True)
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(p, k, v)
    await db.commit()
    return ProjectOut.model_validate(p)


@router.delete("/{pid}")
async def delete_project(pid: str, ctx: AuthContext = Depends(require_auth), db: AsyncSession = Depends(get_db)):
    p, level = await require_access(db, "project", pid, ctx.user)
    if level not in ("owner", "parent"):
        raise HTTPException(403)
    await db.delete(p)
    await db.commit()
    return {"status": "ok"}


@router.post("/{pid}/reindex")
async def reindex_project(pid: str, ctx: AuthContext = Depends(require_auth), db: AsyncSession = Depends(get_db)):
    """Reindicizza tutti i file del progetto (es. dopo aver configurato le chiavi di embedding)."""
    await require_access(db, "project", pid, ctx.user, write=True)
    files = (await db.execute(select(FileAsset).where(FileAsset.project_id == pid))).scalars().all()
    total = 0
    for f in files:
        total += await rag.index_file(f.id, db)
    return {"files": len(files), "chunks": total, "embeddings": rag.embeddings_available()}
