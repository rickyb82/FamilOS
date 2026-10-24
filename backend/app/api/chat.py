from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import get_db
from ..models import Artifact, Chunk, Conversation, FileAsset, Message, Share, User
from ..policy.permissions import TOOL_ALIASES, effective_policy, tool_allowed
from ..providers.registry import available_providers, get_provider
from ..schemas import ConversationCreate, ConversationOut, ConversationUpdate, MessageOut, SendMessageRequest
from ..security.sessions import AuthContext, require_auth
from ..services.access import require_access
from ..services.chat_service import resolve_provider_model, run_turn
from ..tools.registry import all_tools

settings = get_settings()
router = APIRouter(prefix="/chat", tags=["chat"])


@router.get("/providers")
async def providers(ctx: AuthContext = Depends(require_auth)):
    pol = effective_policy(ctx.user)
    from ..services import model_catalog
    return {"providers": model_catalog.catalog(ctx.user, pol),
            "tools": [{"name": n, "description": t.spec.description, "kind": t.usage_kind,
                       "group": "mcp" if n.startswith("mcp__") else TOOL_ALIASES.get(n, n)}
                      for n, t in all_tools().items() if tool_allowed(pol, n)],
            "sandbox": bool(settings.sandbox_url)}


@router.get("/conversations", response_model=list[ConversationOut])
async def list_conversations(project_id: str | None = None, include_shared: bool = True,
                             ctx: AuthContext = Depends(require_auth), db: AsyncSession = Depends(get_db)):
    q = select(Conversation).where(Conversation.family_id == ctx.user.family_id, Conversation.archived == False)  # noqa: E712
    own = q.where(Conversation.owner_id == ctx.user.id)
    if project_id:
        own = own.where(Conversation.project_id == project_id)
    rows = list((await db.execute(own.order_by(Conversation.updated_at.desc()))).scalars().all())
    out = [ConversationOut.model_validate(c) for c in rows]
    if include_shared:
        shared_ids = (await db.execute(select(Share.resource_id).where(
            Share.resource_type == "conversation", Share.family_id == ctx.user.family_id,
            or_(Share.target_user_id == ctx.user.id, Share.target_user_id.is_(None))))).scalars().all()
        if shared_ids:
            sq = q.where(Conversation.id.in_(shared_ids), Conversation.owner_id != ctx.user.id)
            if project_id:
                sq = sq.where(Conversation.project_id == project_id)
            shared = list((await db.execute(sq.order_by(Conversation.updated_at.desc()))).scalars().all())
        else:
            shared = []
        if project_id:
            # dentro un progetto condiviso si vedono anche le chat degli altri membri (con i loro allegati)
            try:
                await require_access(db, "project", project_id, ctx.user)
                seen = {c.id for c in rows} | {c.id for c in shared}
                pq = q.where(Conversation.project_id == project_id, Conversation.owner_id != ctx.user.id)
                for c in (await db.execute(pq.order_by(Conversation.updated_at.desc()))).scalars().all():
                    if c.id not in seen:
                        shared.append(c)
            except HTTPException:
                pass
        if shared:
            for c in shared:
                o = ConversationOut.model_validate(c)
                o.shared = True
                owner = await db.get(User, c.owner_id)
                o.owner_name = owner.display_name if owner else None
                out.append(o)
    return out


@router.post("/conversations", response_model=ConversationOut)
async def create_conversation(body: ConversationCreate, ctx: AuthContext = Depends(require_auth),
                              db: AsyncSession = Depends(get_db)):
    pol = effective_policy(ctx.user)
    pid, model = resolve_provider_model(ctx.user, pol, body.provider, body.model)
    if body.project_id:
        await require_access(db, "project", body.project_id, ctx.user)
    conv = Conversation(family_id=ctx.user.family_id, owner_id=ctx.user.id, title=body.title or "Nuova chat",
                        provider=pid, model=model, project_id=body.project_id)
    db.add(conv)
    await db.commit()
    return ConversationOut.model_validate(conv)


@router.get("/conversations/{cid}")
async def get_conversation(cid: str, ctx: AuthContext = Depends(require_auth), db: AsyncSession = Depends(get_db)):
    conv, level = await require_access(db, "conversation", cid, ctx.user)
    msgs = (await db.execute(select(Message).where(Message.conversation_id == cid).order_by(Message.created_at))).scalars().all()
    owner = await db.get(User, conv.owner_id)
    return {"conversation": ConversationOut.model_validate(conv), "access": level,
            "owner_name": owner.display_name if owner else None,
            "messages": [MessageOut.model_validate(m) for m in msgs]}


async def _move_to_project(db: AsyncSession, conv: Conversation, project_id: str | None) -> None:
    """Sposta la chat in un progetto: anche gli allegati caricati nella chat, i loro frammenti RAG e gli artifact
    passano al progetto, cosi' chi partecipa al progetto li vede (e il modello li ritrova nelle altre chat del progetto).
    Se la chat viene tolta dal progetto, i suoi file restano solo nella chat."""
    conv.project_id = project_id
    await db.execute(update(FileAsset).where(FileAsset.conversation_id == conv.id).values(project_id=project_id))
    await db.execute(update(Chunk).where(Chunk.conversation_id == conv.id).values(project_id=project_id))
    await db.execute(update(Artifact).where(Artifact.conversation_id == conv.id).values(project_id=project_id))


@router.patch("/conversations/{cid}", response_model=ConversationOut)
async def update_conversation(cid: str, body: ConversationUpdate, ctx: AuthContext = Depends(require_auth),
                              db: AsyncSession = Depends(get_db)):
    conv, level = await require_access(db, "conversation", cid, ctx.user, write=True)
    data = body.model_dump(exclude_unset=True)
    if "project_id" in data:
        # spostare la chat in un progetto (o toglierla): solo il proprietario; il progetto deve essere accessibile
        if level != "owner":
            raise HTTPException(403, "Solo il proprietario può spostare la chat")
        new_pid = data.pop("project_id") or None
        if new_pid:
            await require_access(db, "project", new_pid, ctx.user)
        await _move_to_project(db, conv, new_pid)
    data = {k: v for k, v in data.items() if v is not None}
    if "provider" in data or "model" in data:
        # i bambini non possono cambiare modello: resolve_provider_model impone quello del genitore
        data["provider"], data["model"] = resolve_provider_model(
            ctx.user, effective_policy(ctx.user), data.get("provider"), data.get("model"), conv)
    for k, v in data.items():
        setattr(conv, k, v)
    if "title" in data:
        from ..services import workspace
        await workspace.sync_conv_title(db, conv)   # Documenti > CHAT > <titolo> segue la chat
    await db.commit()
    return ConversationOut.model_validate(conv)


@router.delete("/conversations/{cid}")
async def delete_conversation(cid: str, ctx: AuthContext = Depends(require_auth), db: AsyncSession = Depends(get_db)):
    conv, level = await require_access(db, "conversation", cid, ctx.user)
    if level not in ("owner", "parent"):
        raise HTTPException(403, "Solo il proprietario può eliminare")
    # la cartella CHAT/<titolo> resta con i suoi file (sono documenti dell'utente), ma non e' piu' legata a una chat
    from sqlalchemy import update as _update

    from ..models import Folder as _Folder
    await db.execute(_update(_Folder).where(_Folder.conversation_id == conv.id).values(conversation_id=None, kind=""))
    await db.delete(conv)
    await db.commit()
    return {"status": "ok"}


HEARTBEAT_SECONDS = 8


def _stream(db, ctx, conv, text, file_ids, provider, model, tools, approval_id=None):
    """Incapsula run_turn in uno StreamingResponse SSE. Il turno gira in un task separato: se il modello resta
    in silenzio (ragionamento, ricerca web, generazione lunga) ogni HEARTBEAT_SECONDS mandiamo comunque un
    commento SSE, cosi' il browser e i proxy vedono la connessione viva e il client puo' mostrare l'attesa."""
    import asyncio
    import json

    async def gen():
        q: asyncio.Queue = asyncio.Queue()
        DONE = object()

        async def pump():
            try:
                async for chunk in run_turn(db, ctx.user, conv, text, file_ids, provider, model, tools, approval_id=approval_id):
                    await q.put(f"data: {chunk}\n\n")
            except HTTPException as e:
                await q.put(f"data: {json.dumps({'type': 'error', 'error': e.detail, 'status': e.status_code})}\n\n")
                await q.put(f"data: {json.dumps({'type': 'done'})}\n\n")
            except asyncio.CancelledError:
                raise
            except Exception as e:  # pragma: no cover - rete di sicurezza
                await q.put(f"data: {json.dumps({'type': 'error', 'error': f'Errore interno: {e}'})}\n\n")
                await q.put(f"data: {json.dumps({'type': 'done'})}\n\n")
            finally:
                await q.put(DONE)

        task = asyncio.create_task(pump())
        try:
            while True:
                try:
                    item = await asyncio.wait_for(q.get(), timeout=HEARTBEAT_SECONDS)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
                    continue
                if item is DONE:
                    break
                yield item
        finally:
            if not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


class RegenerateRequest(BaseModel):
    provider: str | None = None
    model: str | None = None
    tools: list[str] | None = None


@router.post("/conversations/{cid}/regenerate")
async def regenerate(cid: str, body: RegenerateRequest, ctx: AuthContext = Depends(require_auth),
                     db: AsyncSession = Depends(get_db)):
    """Rigenera l'ultima risposta (con il modello indicato o quello corrente): utile dopo un cambio modello."""
    conv, _ = await require_access(db, "conversation", cid, ctx.user, write=True)
    return _stream(db, ctx, conv, None, [], body.provider, body.model, body.tools)


@router.post("/conversations/{cid}/messages")
async def send_message(cid: str, body: SendMessageRequest, ctx: AuthContext = Depends(require_auth),
                       db: AsyncSession = Depends(get_db)):
    conv, _ = await require_access(db, "conversation", cid, ctx.user, write=True)
    if not body.text.strip() and not body.file_ids:
        raise HTTPException(400, "Messaggio vuoto")
    return _stream(db, ctx, conv, body.text, body.file_ids, body.provider, body.model, body.tools, approval_id=body.approval_id)
