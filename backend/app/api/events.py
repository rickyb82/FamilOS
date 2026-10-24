"""Eventi in tempo reale (SSE) e notifiche dell'utente."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import Notification
from ..security.sessions import AuthContext, require_auth
from ..services import events

router = APIRouter(tags=["events"])


@router.get("/events")
async def stream(request: Request, ctx: AuthContext = Depends(require_auth)):
    """Flusso SSE per la scheda aperta: notifiche nuove e 'files' quando i Documenti dell'utente cambiano (es. una condivisione
    ricevuta), cosi' la pagina si aggiorna senza ricaricare. Battito ogni 25 s."""
    q = events.subscribe(ctx.user.id)

    async def gen():
        try:
            yield events.sse({"kind": "hello"})
            while True:
                if await request.is_disconnected():
                    break
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=25)
                    yield events.sse(ev)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            events.unsubscribe(ctx.user.id, q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"})


@router.get("/notifications")
async def list_notifications(limit: int = 30, ctx: AuthContext = Depends(require_auth), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(Notification).where(Notification.user_id == ctx.user.id)
                             .order_by(Notification.created_at.desc()).limit(min(limit, 100)))).scalars().all()
    unread = sum(1 for n in rows if not n.read)
    return {"unread": unread, "items": [{"id": n.id, "kind": n.kind, "text": n.text, "link": n.link, "actor": n.actor,
                                         "read": n.read, "created_at": n.created_at} for n in rows]}


@router.post("/notifications/read")
async def mark_read(ctx: AuthContext = Depends(require_auth), db: AsyncSession = Depends(get_db)):
    await db.execute(update(Notification).where(Notification.user_id == ctx.user.id, Notification.read.is_(False)).values(read=True))
    await db.commit()
    return {"ok": True}


@router.delete("/notifications/{notification_id}")
async def delete_notification(notification_id: str, ctx: AuthContext = Depends(require_auth), db: AsyncSession = Depends(get_db)):
    """Toglie una notifica dalla campanella (quando ci si clicca o con la ✕): solo le proprie."""
    res = await db.execute(delete(Notification).where(Notification.id == notification_id, Notification.user_id == ctx.user.id))
    await db.commit()
    return {"ok": True, "deleted": res.rowcount or 0}


@router.delete("/notifications")
async def clear_notifications(ctx: AuthContext = Depends(require_auth), db: AsyncSession = Depends(get_db)):
    """Svuota la campanella dell'utente."""
    res = await db.execute(delete(Notification).where(Notification.user_id == ctx.user.id))
    await db.commit()
    return {"ok": True, "deleted": res.rowcount or 0}
