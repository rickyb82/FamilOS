"""Messaggi tra persone: conversazioni dirette e di gruppo, file nella cartella personale di ciascuno, chiamate WebRTC.

- Tempo reale: ogni evento arriva ai membri con services.events.publish (flusso SSE gia' aperto da ogni scheda):
  "msg" (nuovo messaggio o modificato), "msg_read" (letto fino a), "msg_typing", "msg_room" (conversazione cambiata),
  "call" (squillo, ingresso/uscita dalla chiamata, segnali WebRTC tra due browser).
- File: chi invia carica una volta; ogni membro ne riceve una copia in Documenti › Messaggi › <conversazione>
  (cartella kind="msg" e sottocartella kind="msgroom" con conversation_id = id della conversazione).
- Chiamate: "mesh" (ogni browser si collega agli altri), al massimo CALL_MAX persone; lo stato sta in memoria.
  Il server TURN (coturn, ./familai.sh turn up) usa credenziali temporanee per utente (static-auth-secret).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import time
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..models import Folder, MsgMember, MsgMessage, MsgRoom, Role, User
from . import events

settings = get_settings()

APP_ID = "messaggi"
ROOT_NAME = "Messaggi"
CALL_MAX = 5
MAX_GROUP = 100
TURN_TTL_S = 12 * 3600


def can_use(user: User) -> bool:
    from .apps import app_enabled
    return user.is_active and app_enabled(user, APP_ID)


def display_room_name(room: MsgRoom, members: list[User], me_id: str) -> str:
    if room.kind == "group":
        return room.name or "Gruppo"
    other = next((u for u in members if u.id != me_id), None)
    return other.display_name if other else "Conversazione"


async def members_of(db: AsyncSession, room_id: str) -> list[MsgMember]:
    return list((await db.execute(select(MsgMember).where(MsgMember.room_id == room_id))).scalars())


async def member_ids(db: AsyncSession, room_id: str) -> list[str]:
    return [m.user_id for m in await members_of(db, room_id)]


def publish_all(user_ids: list[str], kind: str, data: dict, except_id: str | None = None) -> None:
    for uid in user_ids:
        if uid != except_id:
            events.publish(uid, kind, data)


# ------------------------------------------------------------------ cartelle Documenti › Messaggi › <conversazione>
async def room_folder(db: AsyncSession, user: User, room: MsgRoom, title: str) -> Folder:
    from .naming import unique_folder_name
    root = (await db.execute(select(Folder).where(Folder.owner_id == user.id, Folder.kind == "msg"))).scalars().first()
    if not root:
        root = Folder(family_id=user.family_id, owner_id=user.id, parent_id=None, name=ROOT_NAME, kind="msg", color="#16a34a")
        db.add(root)
        await db.flush()
    f = (await db.execute(select(Folder).where(Folder.owner_id == user.id, Folder.kind == "msgroom",
                                               Folder.conversation_id == room.id))).scalars().first()
    if f:
        return f
    import re
    clean = re.sub(r"[\x00-\x1f/\\]", " ", title).strip()[:80] or "Conversazione"
    f = Folder(family_id=user.family_id, owner_id=user.id, parent_id=root.id, kind="msgroom", conversation_id=room.id,
               name=await unique_folder_name(db, user.id, root.id, clean))
    db.add(f)
    await db.flush()
    return f


# ------------------------------------------------------------------ messaggi
def message_out(m: MsgMessage, viewer_id: str, names: dict[str, str]) -> dict:
    fid = (m.file_ids or {}).get(viewer_id) if m.kind == "file" else None
    return {"id": m.id, "room_id": m.room_id, "sender_id": m.sender_id, "sender_name": names.get(m.sender_id or "", ""),
            "created_at": m.created_at.replace(tzinfo=timezone.utc).isoformat() if m.created_at else None,
            "kind": m.kind, "text": "" if m.deleted else m.text, "deleted": m.deleted, "reply_to": m.reply_to,
            "file": None if m.kind != "file" or m.deleted else
            {"id": fid, "name": m.file_name, "mime": m.file_mime, "size": m.file_size, "saved": bool(fid)}}


async def names_for(db: AsyncSession, ids: set[str]) -> dict[str, str]:
    if not ids:
        return {}
    return {u.id: u.display_name for u in (await db.execute(select(User).where(User.id.in_(ids)))).scalars()}


async def post(db: AsyncSession, room: MsgRoom, sender: User | None, kind: str, text: str = "", **file) -> MsgMessage:
    """Salva un messaggio e lo consegna in tempo reale a tutti i membri (ognuno con la sua copia del file)."""
    m = MsgMessage(room_id=room.id, sender_id=sender.id if sender else None, kind=kind, text=text[:8000], **file)
    room.last_at = datetime.now(timezone.utc)
    db.add(m)
    if sender:   # chi scrive ha letto tutto
        me = await db.get(MsgMember, (room.id, sender.id))
        if me:
            me.last_read_at = room.last_at
    await db.commit()
    ids = await member_ids(db, room.id)
    names = await names_for(db, {sender.id} if sender else set())
    for uid in ids:
        events.publish(uid, "msg", {"message": message_out(m, uid, names), "room_name": room.name if room.kind == "group" else ""})
    if sender and kind in ("text", "file"):
        # chi non ha Emily aperta riceve il messaggio come notifica sul telefono
        from . import push
        who = names.get(sender.id, "") or sender.display_name
        title = f"{who} · {room.name}" if room.kind == "group" and room.name else who
        body = text[:200] if kind == "text" else f"📎 {file.get('file_name') or 'file'}"
        for uid in ids:
            if uid != sender.id and not events.online(uid):
                push.send_later(uid, title, body, f"/messages?room={room.id}", f"msg-{room.id}")
    return m


# ------------------------------------------------------------------ chiamate (stato in memoria)
class Call:
    def __init__(self, room_id: str, video: bool, started_by: str):
        self.room_id, self.video, self.started_by = room_id, video, started_by
        self.started_at = time.time()
        self.participants: set[str] = set()
        self.ever: set[str] = set()       # chi e' entrato almeno una volta (per "chiamata persa")


calls: dict[str, Call] = {}


def prune(call: Call) -> None:
    """Toglie chi non ha piu' nessuna scheda collegata (browser chiuso senza riattaccare)."""
    for uid in list(call.participants):
        if not events.online(uid):
            call.participants.discard(uid)


def ice_servers(user_id: str) -> list[dict]:
    """STUN/TURN per il browser. Credenziali TURN temporanee (formato coturn use-auth-secret): utente = scadenza:id."""
    out: list[dict] = []
    turn = [u.strip() for u in (settings.turn_urls or "").split(",") if u.strip()]
    stun = [u.strip() for u in (settings.stun_urls or "").split(",") if u.strip()]
    if turn and not stun:
        stun = list(dict.fromkeys("stun:" + u.split(":", 1)[1].split("?")[0] for u in turn if u.startswith("turn:")))
    if stun:
        out.append({"urls": stun})
    if turn and settings.turn_secret:
        username = f"{int(time.time()) + TURN_TTL_S}:{user_id}"
        pwd = base64.b64encode(hmac.new(settings.turn_secret.encode(), username.encode(), hashlib.sha1).digest()).decode()
        out.append({"urls": turn, "username": username, "credential": pwd})
    return out
