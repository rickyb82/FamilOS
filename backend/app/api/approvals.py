"""Richieste di permesso (compiti e simili): notifiche per gli adulti, decisione, stato per il bambino."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import ApprovalRequest, Conversation, Role, User
from ..security.sessions import AuthContext, require_adult, require_auth
from ..services import homework

router = APIRouter(prefix="/approvals", tags=["approvals"])


def _out(r: ApprovalRequest, names: dict[str, str]) -> dict:
    return {"id": r.id, "child": names.get(r.child_id, "?"), "child_id": r.child_id, "conversation_id": r.conversation_id,
            "kind": r.kind, "text": r.text, "reason": r.reason, "subject": r.subject, "status": r.status, "decision": r.decision,
            "decided_by": names.get(r.decided_by or "", None), "created_at": r.created_at, "decided_at": r.decided_at,
            "valid_until": r.valid_until}


@router.get("/notifications")
async def notifications(ctx: AuthContext = Depends(require_adult), db: AsyncSession = Depends(get_db)):
    """Per il campanello degli adulti: richieste in attesa + ultime decise. Polling leggero (ogni ~15 s)."""
    names = {u.id: u.display_name for u in (await db.execute(select(User).where(User.family_id == ctx.user.family_id))).scalars()}
    # richieste piu' vecchie di 12 ore scadono
    cutoff = datetime.now(timezone.utc) - timedelta(hours=12)
    stale = (await db.execute(select(ApprovalRequest).where(ApprovalRequest.family_id == ctx.user.family_id,
                                                           ApprovalRequest.status == "pending", ApprovalRequest.created_at < cutoff))).scalars().all()
    for r in stale:
        r.status = "expired"
    if stale:
        await db.commit()
    pending = (await db.execute(select(ApprovalRequest).where(ApprovalRequest.family_id == ctx.user.family_id, ApprovalRequest.status == "pending")
                                .order_by(ApprovalRequest.created_at.desc()))).scalars().all()
    recent = (await db.execute(select(ApprovalRequest).where(ApprovalRequest.family_id == ctx.user.family_id, ApprovalRequest.status != "pending")
                               .order_by(ApprovalRequest.created_at.desc()).limit(10))).scalars().all()
    return {"pending": [_out(r, names) for r in pending], "recent": [_out(r, names) for r in recent], "count": len(pending)}


class Decision(BaseModel):
    decision: str          # approve | approve_tutor | deny
    scope: str = "once"    # once | today


@router.post("/{rid}/decide")
async def decide(rid: str, body: Decision, request: Request, ctx: AuthContext = Depends(require_adult), db: AsyncSession = Depends(get_db)):
    r = await db.get(ApprovalRequest, rid)
    if not r or r.family_id != ctx.user.family_id:
        raise HTTPException(404)
    if r.status != "pending":
        raise HTTPException(400, "Richiesta gia' decisa")
    if body.decision not in ("approve", "approve_tutor", "deny"):
        raise HTTPException(400, "decisione sconosciuta")
    homework.decide(r, body.decision, body.scope if body.scope in ("once", "today") else "once", ctx.user)
    await db.commit()
    from .auth import audit
    await audit(db, f"approval_{body.decision}", request, ctx.user, f"{r.child_id}: {r.text[:100]} ({body.scope})")
    return {"status": "ok", "request": r.status}


@router.get("/mine/{rid}")
async def my_request(rid: str, ctx: AuthContext = Depends(require_auth), db: AsyncSession = Depends(get_db)):
    """Il bambino (o chi ha fatto la richiesta) controlla se il genitore ha risposto."""
    r = await db.get(ApprovalRequest, rid)
    if not r or r.child_id != ctx.user.id:
        raise HTTPException(404)
    names = {u.id: u.display_name for u in (await db.execute(select(User).where(User.family_id == ctx.user.family_id))).scalars()}
    return _out(r, names)


@router.get("/mine")
async def my_pending(ctx: AuthContext = Depends(require_auth), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(ApprovalRequest).where(ApprovalRequest.child_id == ctx.user.id, ApprovalRequest.status == "pending")
                             .order_by(ApprovalRequest.created_at.desc()))).scalars().all()
    return [{"id": r.id, "conversation_id": r.conversation_id, "text": r.text, "created_at": r.created_at} for r in rows]
