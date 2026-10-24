from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import Share, User
from ..policy.permissions import effective_policy
from ..schemas import ShareOut, ShareRequest
from ..security.sessions import AuthContext, require_auth
from ..services.access import RESOURCE_MODEL, require_access

router = APIRouter(prefix="/shares", tags=["shares"])


@router.get("/members")
async def family_members(q: str = "", limit: int = 8, ctx: AuthContext = Depends(require_auth), db: AsyncSession = Depends(get_db)):
    """Membri con cui condividere. Con ?q= cerca in tempo reale per nome, username o email (prefisso di parola o sottostringa)."""
    rows = (await db.execute(select(User).where(User.family_id == ctx.user.family_id, User.is_active == True))).scalars().all()  # noqa: E712
    out = [{"id": u.id, "display_name": u.display_name, "avatar": u.avatar, "role": u.role.value,
            "email": u.email or "", "username": u.username} for u in rows if u.id != ctx.user.id]
    ql = q.strip().lower()
    if ql:
        def score(m: dict) -> int:
            hay = [m["display_name"].lower(), m["email"].lower(), m["username"].lower()]
            if any(h == ql for h in hay):
                return 0
            if any(h.startswith(ql) or any(w.startswith(ql) for w in h.replace(".", " ").replace("@", " ").split()) for h in hay):
                return 1
            if any(ql in h for h in hay):
                return 2
            return 9
        out = sorted((m for m in out if score(m) < 9), key=lambda m: (score(m), m["display_name"].lower()))[:max(1, min(limit, 50))]
    return out


@router.get("", response_model=list[ShareOut])
async def list_shares(resource_type: str, resource_id: str, ctx: AuthContext = Depends(require_auth),
                      db: AsyncSession = Depends(get_db)):
    await require_access(db, resource_type, resource_id, ctx.user)
    rows = (await db.execute(select(Share).where(Share.resource_type == resource_type, Share.resource_id == resource_id))).scalars().all()
    return [ShareOut.model_validate(s) for s in rows]


@router.post("", response_model=ShareOut)
async def share(body: ShareRequest, ctx: AuthContext = Depends(require_auth), db: AsyncSession = Depends(get_db)):
    if body.resource_type not in RESOURCE_MODEL:
        raise HTTPException(400, "Tipo risorsa non valido")
    if not effective_policy(ctx.user).get("can_share", True):
        raise HTTPException(403, "Non puoi condividere")
    resource, level = await require_access(db, body.resource_type, body.resource_id, ctx.user)
    if level != "owner":
        raise HTTPException(403, "Solo il proprietario può condividere")
    if body.target_user_id:
        target = await db.get(User, body.target_user_id)
        if not target or target.family_id != ctx.user.family_id:
            raise HTTPException(404, "Utente non trovato")
    perm = "write" if body.permission == "write" else "read"
    existing = (await db.execute(select(Share).where(
        Share.resource_type == body.resource_type, Share.resource_id == body.resource_id,
        Share.target_user_id == body.target_user_id))).scalar_one_or_none()
    if existing:
        existing.permission = perm
        await db.commit()
        return ShareOut.model_validate(existing)
    s = Share(family_id=ctx.user.family_id, resource_type=body.resource_type, resource_id=body.resource_id,
              owner_id=ctx.user.id, target_user_id=body.target_user_id, permission=perm)
    db.add(s)
    await _announce(db, ctx, body.resource_type, resource, body.target_user_id, perm)
    await db.commit()
    return ShareOut.model_validate(s)


async def _announce(db: AsyncSession, ctx: AuthContext, rtype: str, resource, target_user_id: str | None, perm: str) -> None:
    """Avvisa chi riceve la condivisione (campanella + evento in tempo reale): un utente, oppure tutti gli adulti della famiglia."""
    from ..models import Role as _Role
    from ..services import events
    if target_user_id:
        targets = [target_user_id]
    else:
        rows = (await db.execute(select(User.id).where(User.family_id == ctx.user.family_id, User.id != ctx.user.id,
                                                      User.is_active.is_(True), User.role != _Role.child))).scalars().all()
        targets = list(rows)
    label = {"file": "il file", "conversation": "la chat", "project": "il progetto"}.get(rtype, rtype)
    name = getattr(resource, "filename", None) or getattr(resource, "title", None) or getattr(resource, "name", None) or ""
    link = {"file": "/documents?shared=1", "conversation": f"/c/{resource.id}", "project": f"/documents?project={resource.id}"}.get(rtype, "")
    text = f"{ctx.user.display_name} ha condiviso con te {label} «{name}»" + (" (puoi modificarlo)" if perm == "write" else "")
    for uid in targets:
        await events.notify(db, uid, f"share_{rtype}", text, link, actor=ctx.user.display_name)
        events.publish(uid, "files", {"reason": "share", "resource_type": rtype, "resource_id": resource.id})


@router.delete("/{share_id}")
async def unshare(share_id: str, ctx: AuthContext = Depends(require_auth), db: AsyncSession = Depends(get_db)):
    s = await db.get(Share, share_id)
    if not s or (s.owner_id != ctx.user.id and ctx.user.role.value != "admin"):
        raise HTTPException(404)
    from ..services import events
    if s.target_user_id:
        events.publish(s.target_user_id, "files", {"reason": "unshare", "resource_type": s.resource_type, "resource_id": s.resource_id})
    await db.delete(s)
    await db.commit()
    return {"status": "ok"}
