"""Eventi in tempo reale verso il browser (SSE su /api/events) e notifiche persistenti.

`publish(user_id, kind, data)` consegna un evento a tutte le schede aperte di quell'utente (code in memoria, per processo);
`notify(db, user_id, kind, text, link)` salva anche una notifica (campanella) e la pubblica. Chi non e' collegato la trova
all'accesso successivo. Eventi: "notification" (nuova notifica), "files" (i Documenti sono cambiati: ricarica),
"ping" (battito ogni 25 s per tenere viva la connessione dietro il proxy).
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import defaultdict

from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Notification

log = logging.getLogger(__name__)

_queues: dict[str, set[asyncio.Queue]] = defaultdict(set)
MAX_QUEUE = 200


def subscribe(user_id: str) -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=MAX_QUEUE)
    _queues[user_id].add(q)
    return q


def unsubscribe(user_id: str, q: asyncio.Queue) -> None:
    _queues[user_id].discard(q)
    if not _queues[user_id]:
        _queues.pop(user_id, None)


def publish(user_id: str, kind: str, data: dict | None = None) -> None:
    """Non blocca mai: se una scheda e' indietro di 200 eventi, perde i piu' vecchi."""
    payload = {"kind": kind, "ts": time.time(), **(data or {})}
    for q in list(_queues.get(user_id, ())):
        try:
            q.put_nowait(payload)
        except asyncio.QueueFull:
            try:
                q.get_nowait()
                q.put_nowait(payload)
            except Exception:
                pass


def online(user_id: str) -> bool:
    return bool(_queues.get(user_id))


async def notify(db: AsyncSession, user_id: str, kind: str, text: str, link: str = "", actor: str = "") -> Notification:
    n = Notification(user_id=user_id, kind=kind, text=text[:300], link=link[:300], actor=actor[:120])
    db.add(n)
    await db.flush()
    publish(user_id, "notification", {"id": n.id, "nkind": kind, "text": n.text, "link": n.link, "actor": n.actor})
    from . import push
    push.send_later(user_id, n.actor or "Emily", n.text, n.link or "/app", kind)   # anche sul telefono, ad app chiusa
    return n


def sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
