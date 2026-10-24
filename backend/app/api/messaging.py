"""💬 Messaggi tra persone (stile WhatsApp): conversazioni dirette e gruppi, foto e documenti, letto/non letto,
"sta scrivendo", chiamate e videochiamate (anche di gruppo, fino a 5) con WebRTC. Logica in services/messaging."""
from __future__ import annotations

import mimetypes
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import get_db
from ..models import FileAsset, MsgMember, MsgMessage, MsgRoom, User
from ..policy.permissions import effective_policy
from ..security.sessions import AuthContext, require_auth
from ..services import events, messaging as ms

router = APIRouter(prefix="/msg", tags=["messaggi"])
settings = get_settings()


def _need_app(ctx: AuthContext) -> None:
    if not ms.can_use(ctx.user):
        raise HTTPException(403, "I Messaggi non sono abilitati per il tuo account: chiedi all'amministratore")


async def _room_for(db: AsyncSession, room_id: str, user: User) -> tuple[MsgRoom, MsgMember]:
    room = await db.get(MsgRoom, room_id[:40])
    me = await db.get(MsgMember, (room_id[:40], user.id)) if room else None
    if not room or not me or room.family_id != user.family_id:
        raise HTTPException(404, "Conversazione non trovata")
    return room, me


async def _room_out(db: AsyncSession, room: MsgRoom, me: MsgMember, uid: str) -> dict:
    mems = await ms.members_of(db, room.id)
    users = {u.id: u for u in (await db.execute(select(User).where(User.id.in_([m.user_id for m in mems])))).scalars()}
    last = (await db.execute(select(MsgMessage).where(MsgMessage.room_id == room.id).order_by(MsgMessage.created_at.desc()).limit(1))).scalars().first()
    since = me.last_read_at or datetime(1970, 1, 1, tzinfo=timezone.utc)
    unread = (await db.execute(select(func.count()).select_from(MsgMessage).where(
        MsgMessage.room_id == room.id, MsgMessage.created_at > since, MsgMessage.sender_id != uid))).scalar_one()
    call = ms.calls.get(room.id)
    if call:
        ms.prune(call)
    return {"id": room.id, "kind": room.kind, "name": ms.display_room_name(room, list(users.values()), uid),
            "group_name": room.name, "admin": me.admin, "muted": me.muted,
            "last_at": room.last_at.replace(tzinfo=timezone.utc).isoformat() if room.last_at else None,
            "unread": int(unread),
            "last": ms.message_out(last, uid, {u.id: u.display_name for u in users.values()}) if last else None,
            "members": [{"id": m.user_id, "name": users[m.user_id].display_name if m.user_id in users else "?",
                         "avatar": users[m.user_id].avatar if m.user_id in users else "", "admin": m.admin,
                         "online": events.online(m.user_id),
                         "last_read_at": m.last_read_at.replace(tzinfo=timezone.utc).isoformat() if m.last_read_at else None}
                        for m in mems],
            "call": {"video": call.video, "participants": sorted(call.participants)} if call and call.participants else None}


@router.get("/contacts")
async def contacts(ctx: AuthContext = Depends(require_auth), db: AsyncSession = Depends(get_db)):
    """Le persone dell'organizzazione con cui si puo' parlare (Messaggi abilitato), con chi e' collegato adesso."""
    _need_app(ctx)
    users = (await db.execute(select(User).where(User.family_id == ctx.user.family_id, User.id != ctx.user.id))).scalars().all()
    return [{"id": u.id, "name": u.display_name, "avatar": u.avatar, "online": events.online(u.id)}
            for u in sorted(users, key=lambda u: u.display_name.lower()) if ms.can_use(u)]


@router.get("/rooms")
async def rooms(ctx: AuthContext = Depends(require_auth), db: AsyncSession = Depends(get_db)):
    _need_app(ctx)
    mine = (await db.execute(select(MsgRoom, MsgMember).join(MsgMember, and_(MsgMember.room_id == MsgRoom.id, MsgMember.user_id == ctx.user.id))
                             .order_by(MsgRoom.last_at.desc()).limit(300))).all()
    return [await _room_out(db, r, m, ctx.user.id) for r, m in mine]


class NewRoom(BaseModel):
    members: list[str] = Field(max_length=ms.MAX_GROUP)
    name: str = Field("", max_length=120)
    group: bool = False


@router.post("/rooms")
async def create_room(body: NewRoom, ctx: AuthContext = Depends(require_auth), db: AsyncSession = Depends(get_db)):
    """Nuova conversazione: diretta (un'altra persona; se esiste gia' la riapre) o gruppo (nome e membri)."""
    _need_app(ctx)
    ids = list(dict.fromkeys(i[:40] for i in body.members if i and i != ctx.user.id))
    people = (await db.execute(select(User).where(User.id.in_(ids), User.family_id == ctx.user.family_id))).scalars().all() if ids else []
    people = [u for u in people if ms.can_use(u)]
    if not people:
        raise HTTPException(400, "Scegli almeno una persona")
    if not body.group and len(people) == 1:
        other = people[0]
        mine = select(MsgMember.room_id).where(MsgMember.user_id == ctx.user.id)
        theirs = select(MsgMember.room_id).where(MsgMember.user_id == other.id)
        existing = (await db.execute(select(MsgRoom).where(MsgRoom.kind == "direct", MsgRoom.id.in_(mine), MsgRoom.id.in_(theirs)))).scalars().first()
        if existing:
            me = await db.get(MsgMember, (existing.id, ctx.user.id))
            return await _room_out(db, existing, me, ctx.user.id)
        room = MsgRoom(family_id=ctx.user.family_id, kind="direct", created_by=ctx.user.id)
    else:
        name = body.name.strip()[:120] or ", ".join([ctx.user.display_name] + [u.display_name for u in people])[:120]
        room = MsgRoom(family_id=ctx.user.family_id, kind="group", name=name, created_by=ctx.user.id)
    db.add(room)
    await db.flush()
    db.add(MsgMember(room_id=room.id, user_id=ctx.user.id, admin=True, last_read_at=datetime.now(timezone.utc)))
    for u in people:
        db.add(MsgMember(room_id=room.id, user_id=u.id, admin=False))
    await db.commit()
    if room.kind == "group":
        await ms.post(db, room, None, "system", f"{ctx.user.display_name} ha creato il gruppo «{room.name}»")
    ms.publish_all([u.id for u in people], "msg_room", {"room_id": room.id})
    me = await db.get(MsgMember, (room.id, ctx.user.id))
    return await _room_out(db, room, me, ctx.user.id)


class RoomEdit(BaseModel):
    name: str | None = Field(None, max_length=120)
    add: list[str] = Field(default_factory=list, max_length=ms.MAX_GROUP)
    remove: list[str] = Field(default_factory=list, max_length=ms.MAX_GROUP)
    make_admin: list[str] = Field(default_factory=list, max_length=ms.MAX_GROUP)
    revoke_admin: list[str] = Field(default_factory=list, max_length=ms.MAX_GROUP)
    muted: bool | None = None


@router.patch("/rooms/{room_id}")
async def edit_room(room_id: str, body: RoomEdit, ctx: AuthContext = Depends(require_auth), db: AsyncSession = Depends(get_db)):
    """Gruppo: rinomina, aggiungi o togli persone (amministratori del gruppo). Silenzia: chiunque, per se'."""
    _need_app(ctx)
    room, me = await _room_for(db, room_id, ctx.user)
    if body.muted is not None:
        me.muted = body.muted
    changes: list[str] = []
    if body.name is not None or body.add or body.remove or body.make_admin or body.revoke_admin:
        if room.kind != "group" or not me.admin:
            raise HTTPException(403, "Solo gli amministratori del gruppo possono cambiarlo")
        if body.name is not None and body.name.strip() and body.name.strip() != room.name:
            room.name = body.name.strip()[:120]
            changes.append(f"{ctx.user.display_name} ha rinominato il gruppo in «{room.name}»")
        current = set(await ms.member_ids(db, room.id))
        if len(current | set(body.add)) > ms.MAX_GROUP:
            raise HTTPException(400, f"Al massimo {ms.MAX_GROUP} persone in un gruppo")
        for u in (await db.execute(select(User).where(User.id.in_([i[:40] for i in body.add]), User.family_id == ctx.user.family_id))).scalars():
            if u.id not in current and ms.can_use(u):
                db.add(MsgMember(room_id=room.id, user_id=u.id))
                changes.append(f"{ctx.user.display_name} ha aggiunto {u.display_name}")
        for uid in body.remove:
            m = await db.get(MsgMember, (room.id, uid[:40]))
            if m and m.user_id != ctx.user.id:
                u = await db.get(User, m.user_id)
                await db.delete(m)
                changes.append(f"{ctx.user.display_name} ha tolto {u.display_name if u else 'un membro'}")
                events.publish(m.user_id, "msg_room", {"room_id": room.id, "removed": True})
        for uid in body.make_admin:
            m = await db.get(MsgMember, (room.id, uid[:40]))
            if m and not m.admin:
                m.admin = True
                u = await db.get(User, m.user_id)
                changes.append(f"{ctx.user.display_name} ha reso amministratore {u.display_name if u else 'un membro'}")
        if body.revoke_admin:
            await db.flush()
            mems = await ms.members_of(db, room.id)
            for uid in body.revoke_admin:
                m = next((x for x in mems if x.user_id == uid[:40]), None)
                if not m or not m.admin:
                    continue
                if sum(1 for x in mems if x.admin) <= 1:
                    raise HTTPException(400, "Il gruppo deve avere almeno un amministratore")
                m.admin = False
                u = await db.get(User, m.user_id)
                who = "si è tolto il ruolo di amministratore" if m.user_id == ctx.user.id else f"ha tolto il ruolo di amministratore a {u.display_name if u else 'un membro'}"
                changes.append(f"{ctx.user.display_name} {who}")
    await db.commit()
    for c in changes:
        await ms.post(db, room, None, "system", c)
    ms.publish_all(await ms.member_ids(db, room.id), "msg_room", {"room_id": room.id})
    return await _room_out(db, room, me, ctx.user.id)


@router.post("/rooms/{room_id}/leave")
async def leave_room(room_id: str, ctx: AuthContext = Depends(require_auth), db: AsyncSession = Depends(get_db)):
    _need_app(ctx)
    room, me = await _room_for(db, room_id, ctx.user)
    if room.kind != "group":
        raise HTTPException(400, "Si escono solo dai gruppi")
    await db.delete(me)
    await db.flush()
    rest = await ms.members_of(db, room.id)
    if rest and not any(m.admin for m in rest):   # il gruppo non resta senza amministratori
        rest[0].admin = True
    await db.commit()
    if rest:
        await ms.post(db, room, None, "system", f"{ctx.user.display_name} è uscito dal gruppo")
        ms.publish_all([m.user_id for m in rest], "msg_room", {"room_id": room.id})
    return {"ok": True}


@router.get("/rooms/{room_id}/messages")
async def messages(room_id: str, before: str | None = None, limit: int = 50, ctx: AuthContext = Depends(require_auth),
                   db: AsyncSession = Depends(get_db)):
    _need_app(ctx)
    room, _me = await _room_for(db, room_id, ctx.user)
    q = select(MsgMessage).where(MsgMessage.room_id == room.id)
    if before:
        ref = await db.get(MsgMessage, before[:40])
        if ref and ref.room_id == room.id:
            q = q.where(MsgMessage.created_at < ref.created_at)
    rows = list((await db.execute(q.order_by(MsgMessage.created_at.desc()).limit(max(1, min(limit, 200))))).scalars())
    rows.reverse()
    names = await ms.names_for(db, {m.sender_id for m in rows if m.sender_id})
    return {"messages": [ms.message_out(m, ctx.user.id, names) for m in rows], "more": len(rows) >= max(1, min(limit, 200))}


class NewMessage(BaseModel):
    text: str = Field(min_length=1, max_length=8000)
    reply_to: str | None = Field(None, max_length=40)


@router.post("/rooms/{room_id}/messages")
async def send(room_id: str, body: NewMessage, ctx: AuthContext = Depends(require_auth), db: AsyncSession = Depends(get_db)):
    _need_app(ctx)
    room, _me = await _room_for(db, room_id, ctx.user)
    text = body.text.strip()
    if not text:
        raise HTTPException(400, "Messaggio vuoto")
    m = await ms.post(db, room, ctx.user, "text", text)
    if body.reply_to:
        ref = await db.get(MsgMessage, body.reply_to)
        if ref and ref.room_id == room.id:
            m.reply_to = ref.id
            await db.commit()
    return ms.message_out(m, ctx.user.id, {ctx.user.id: ctx.user.display_name})


@router.post("/rooms/{room_id}/files")
async def send_file(room_id: str, file: UploadFile = File(...), caption: str = Form(""), ctx: AuthContext = Depends(require_auth),
                    db: AsyncSession = Depends(get_db)):
    """Foto o documento: chi invia carica una volta, ogni membro (compreso chi invia) ne ha una copia sua in
    Documenti › Messaggi › <conversazione>. Chi ha lo spazio pieno riceve il messaggio senza la copia."""
    _need_app(ctx)
    room, _me = await _room_for(db, room_id, ctx.user)
    pol = effective_policy(ctx.user)
    data = await file.read()
    max_bytes = int(pol.get("max_upload_mb", 20)) * 1024 * 1024
    if not data:
        raise HTTPException(400, "File vuoto")
    if len(data) > max_bytes:
        raise HTTPException(413, f"File troppo grande (max {pol.get('max_upload_mb')} MB)")
    name = re.sub(r"[^\w.\- ]", "_", file.filename or "file")[:200]
    ext = Path(name).suffix.lower()
    mime = file.content_type or mimetypes.guess_type(name)[0] or "application/octet-stream"
    from ..api.files import _scan_upload
    from ..services import quota as _quota
    from ..services.naming import unique_filename
    from ..tools.documents import extract_text
    probe = settings.uploads_dir / f"{uuid.uuid4().hex}{ext or '.bin'}"
    probe.write_bytes(data)
    verdict = await _scan_upload(probe)
    if verdict:
        probe.unlink(missing_ok=True)
        raise HTTPException(422, f"File rifiutato dal controllo antivirus: {verdict}")
    text = None if mime.startswith(("image/", "video/", "audio/")) else (extract_text(probe, mime) or "")
    members = (await db.execute(select(User).join(MsgMember, MsgMember.user_id == User.id).where(MsgMember.room_id == room.id))).scalars().all()
    all_members = list(members)
    file_ids: dict[str, str] = {}
    first = True
    for u in sorted(all_members, key=lambda u: u.id != ctx.user.id):   # prima chi invia: usa il file appena scritto
        try:
            await _quota.ensure_space(db, u, len(data))
        except HTTPException:
            if u.id == ctx.user.id:
                probe.unlink(missing_ok=True)
                raise
            continue
        title = ms.display_room_name(room, all_members, u.id)
        folder = await ms.room_folder(db, u, room, title)
        fid = uuid.uuid4().hex
        path = probe if first else settings.uploads_dir / f"{fid}{ext or '.bin'}"
        if not first:
            path.write_bytes(data)
        first = False
        asset = FileAsset(id=fid, family_id=u.family_id, owner_id=u.id, filename=await unique_filename(db, u.id, folder.id, name),
                          mime=mime, size=len(data), path=str(path), folder_id=folder.id, extracted_text=text)
        db.add(asset)
        file_ids[u.id] = fid
        if u.id != ctx.user.id:
            events.publish(u.id, "files", {"reason": "msg_file", "file_id": fid})
    await db.flush()
    m = await ms.post(db, room, ctx.user, "file", caption.strip()[:2000], file_name=name, file_mime=mime, file_size=len(data), file_ids=file_ids)
    return ms.message_out(m, ctx.user.id, {ctx.user.id: ctx.user.display_name})


@router.delete("/messages/{msg_id}")
async def delete_message(msg_id: str, ctx: AuthContext = Depends(require_auth), db: AsyncSession = Depends(get_db)):
    """Elimina per tutti un proprio messaggio (resta "messaggio eliminato"; le copie dei file restano a chi le ha)."""
    _need_app(ctx)
    m = await db.get(MsgMessage, msg_id[:40])
    if not m or m.sender_id != ctx.user.id:
        raise HTTPException(404, "Messaggio non trovato")
    await _room_for(db, m.room_id, ctx.user)
    m.deleted, m.text = True, ""
    await db.commit()
    names = {ctx.user.id: ctx.user.display_name}
    for uid in await ms.member_ids(db, m.room_id):
        events.publish(uid, "msg", {"message": ms.message_out(m, uid, names), "update": True})
    return {"ok": True}


@router.post("/rooms/{room_id}/read")
async def mark_read(room_id: str, ctx: AuthContext = Depends(require_auth), db: AsyncSession = Depends(get_db)):
    _need_app(ctx)
    room, me = await _room_for(db, room_id, ctx.user)
    me.last_read_at = datetime.now(timezone.utc)
    await db.commit()
    ms.publish_all(await ms.member_ids(db, room.id), "msg_read", {"room_id": room.id, "user_id": ctx.user.id,
                                                                  "at": me.last_read_at.isoformat()})
    return {"ok": True}


_typing_at: dict[tuple[str, str], float] = {}


@router.post("/rooms/{room_id}/typing")
async def typing(room_id: str, ctx: AuthContext = Depends(require_auth), db: AsyncSession = Depends(get_db)):
    _need_app(ctx)
    room, _me = await _room_for(db, room_id, ctx.user)
    k = (room.id, ctx.user.id)
    if time.time() - _typing_at.get(k, 0) > 2:   # al massimo un evento ogni 2 secondi
        _typing_at[k] = time.time()
        ms.publish_all(await ms.member_ids(db, room.id), "msg_typing", {"room_id": room.id, "user_id": ctx.user.id,
                                                                        "name": ctx.user.display_name}, except_id=ctx.user.id)
    return {"ok": True}


# ------------------------------------------------------------------ chiamate WebRTC (mesh): segnalazione via SSE
@router.get("/ice")
async def ice(ctx: AuthContext = Depends(require_auth)):
    _need_app(ctx)
    return {"iceServers": ms.ice_servers(ctx.user.id), "max": ms.CALL_MAX}


class CallJoin(BaseModel):
    video: bool = True


@router.post("/rooms/{room_id}/call/join")
async def call_join(room_id: str, body: CallJoin, ctx: AuthContext = Depends(require_auth), db: AsyncSession = Depends(get_db)):
    """Avvia la chiamata (fa squillare gli altri) o entra in quella in corso. Chi entra chiama chi c'e' gia'."""
    _need_app(ctx)
    room, _me = await _room_for(db, room_id, ctx.user)
    ids = await ms.member_ids(db, room.id)
    call = ms.calls.get(room.id)
    if call:
        ms.prune(call)
    if not call or not call.participants:
        call = ms.Call(room.id, body.video, ctx.user.id)
        ms.calls[room.id] = call
    if ctx.user.id not in call.participants and len(call.participants) >= ms.CALL_MAX:
        raise HTTPException(409, f"Chiamata piena: al massimo {ms.CALL_MAX} persone")
    present = sorted(call.participants - {ctx.user.id})
    new_call = not call.participants
    call.participants.add(ctx.user.id)
    call.ever.add(ctx.user.id)
    info = {"room_id": room.id, "video": call.video, "participants": sorted(call.participants),
            "room_name": room.name if room.kind == "group" else ""}
    for uid in ids:
        if uid == ctx.user.id:
            continue
        if uid in call.participants:
            events.publish(uid, "call", {**info, "type": "joined", "user_id": ctx.user.id, "name": ctx.user.display_name})
        elif new_call:
            events.publish(uid, "call", {**info, "type": "ring", "from_id": ctx.user.id, "from_name": ctx.user.display_name})
        else:
            events.publish(uid, "call", {**info, "type": "update"})
    return {**info, "peers": present, "iceServers": ms.ice_servers(ctx.user.id)}


@router.post("/rooms/{room_id}/call/leave")
async def call_leave(room_id: str, ctx: AuthContext = Depends(require_auth), db: AsyncSession = Depends(get_db)):
    _need_app(ctx)
    room, _me = await _room_for(db, room_id, ctx.user)
    call = ms.calls.get(room.id)
    if not call:
        return {"ok": True}
    call.participants.discard(ctx.user.id)
    ms.prune(call)
    ids = await ms.member_ids(db, room.id)
    ms.publish_all(ids, "call", {"room_id": room.id, "type": "left", "user_id": ctx.user.id,
                                 "participants": sorted(call.participants)}, except_id=ctx.user.id)
    if not call.participants or (len(call.participants) == 1 and room.kind == "direct"):
        ms.calls.pop(room.id, None)
        ms.publish_all(ids, "call", {"room_id": room.id, "type": "ended"})
        what = "Videochiamata" if call.video else "Chiamata"
        if len(call.ever) <= 1:
            await ms.post(db, room, await db.get(User, call.started_by), "call", f"{what} persa")
        else:
            secs = int(time.time() - call.started_at)
            await ms.post(db, room, await db.get(User, call.started_by), "call", f"{what} · {secs // 60}:{secs % 60:02d}")
    return {"ok": True}


@router.post("/rooms/{room_id}/call/decline")
async def call_decline(room_id: str, ctx: AuthContext = Depends(require_auth), db: AsyncSession = Depends(get_db)):
    """Rifiuta lo squillo: in una conversazione diretta la chiamata finisce, in un gruppo gli altri proseguono."""
    _need_app(ctx)
    room, _me = await _room_for(db, room_id, ctx.user)
    call = ms.calls.get(room.id)
    if call:
        for uid in call.participants:
            events.publish(uid, "call", {"room_id": room.id, "type": "declined", "user_id": ctx.user.id, "name": ctx.user.display_name})
    return {"ok": True}


class Signal(BaseModel):
    to: str = Field(max_length=40)
    data: dict


@router.post("/rooms/{room_id}/call/signal")
async def call_signal(room_id: str, body: Signal, ctx: AuthContext = Depends(require_auth), db: AsyncSession = Depends(get_db)):
    """Offerta/risposta SDP e candidati ICE tra due partecipanti della stessa chiamata."""
    _need_app(ctx)
    room, _me = await _room_for(db, room_id, ctx.user)
    call = ms.calls.get(room.id)
    if not call or ctx.user.id not in call.participants or body.to not in call.participants:
        raise HTTPException(409, "Chiamata non in corso")
    if len(str(body.data)) > 60_000:
        raise HTTPException(413, "Segnale troppo grande")
    events.publish(body.to, "call", {"room_id": room.id, "type": "signal", "from": ctx.user.id, "data": body.data})
    return {"ok": True}
