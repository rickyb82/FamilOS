"""Compattazione delle conversazioni lunghe (come la "memoria compressa" delle app di chat).

Quando la cronologia supera il budget di contesto:
  1. la parte piu' vecchia viene riassunta da un modello economico (locale se c'e') in un "riassunto cumulativo",
  2. i messaggi vecchi vengono archiviati, testo integrale, in un file virtuale "Cronologia chat" indicizzato dal RAG
     (chunk + embedding), cosi' i dettagli restano cercabili con search_documents e con il recupero automatico,
  3. al modello si inviano: riassunto + solo i messaggi recenti.
Lo stato vive in conversation.settings["compaction"] = {"upto": <created_at ISO dell'ultimo messaggio riassunto>, "summary": str}.
Funziona con qualunque provider (OpenAI, Ollama): tutto avviene nel backend prima della chiamata.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..models import Chunk, Conversation, FileAsset, Message

log = logging.getLogger(__name__)
settings = get_settings()

HISTORY_FILENAME = "Cronologia chat (archivio automatico)"
SUMMARY_SYSTEM = """Sei un archivista. Aggiorna il RIASSUNTO CUMULATIVO di una conversazione tra un utente e un assistente,
integrando i nuovi messaggi. Conserva: obiettivi dell'utente, decisioni prese, preferenze espresse, fatti e numeri
importanti, nomi di file/artifact/progetti citati, cose ancora da fare, il tono della conversazione. Elimina chiacchiere,
ripetizioni e dettagli superati. Scrivi in italiano, in prosa compatta per punti brevi, massimo 350 parole.
Rispondi SOLO con il riassunto aggiornato."""


def _approx_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _msg_text(m: Message) -> str:
    parts = []
    for b in m.content or []:
        t = b.get("type")
        if t == "text" and b.get("text"):
            parts.append(b["text"])
        elif t == "tool_use":
            parts.append(f"[tool {b.get('name')}]")
        elif t == "tool_result":
            parts.append(f"[risultato {b.get('name')}: {str(b.get('content', ''))[:400]}]")
        elif t == "image":
            parts.append("[immagine]")
    return "\n".join(parts).strip()


def state(conv: Conversation) -> dict:
    return dict((conv.settings or {}).get("compaction") or {})


def budget_tokens() -> int:
    return int(settings.context_budget_tokens or 24000)


async def maybe_compact(db: AsyncSession, conv: Conversation, msgs: list[Message]) -> list[Message]:
    """Se la cronologia supera il budget, riassume e archivia la parte vecchia. Ritorna i messaggi da inviare al modello."""
    from ..models import User, Role
    owner = await db.get(User,conv.owner_id)
    if owner and owner.role == Role.child:
        return msgs
    st = state(conv)
    upto = st.get("upto")
    recent = [m for m in msgs if not upto or m.created_at.isoformat() > upto]
    total = sum(_approx_tokens(_msg_text(m)) for m in recent)
    if total <= budget_tokens() or str(settings.compaction_enabled).lower() not in ("1", "true", "yes"):
        return recent

    # tieni gli ultimi KEEP token "freschi", riassumi il resto (mai l'ultimo messaggio utente)
    keep = int(settings.context_keep_tokens or 8000)
    acc, cut = 0, len(recent)
    for i in range(len(recent) - 1, -1, -1):
        acc += _approx_tokens(_msg_text(recent[i]))
        if acc > keep:
            cut = i
            break
    # non spezzare una coppia tool_use/tool_result: il taglio deve cadere su un messaggio utente
    while cut < len(recent) and recent[cut].role != "user":
        cut += 1
    old, fresh = recent[:cut], recent[cut:]
    if len(old) < 4:
        return recent

    transcript = "\n\n".join(f"[{'Utente' if m.role == 'user' else 'Assistente' if m.role == 'assistant' else 'Tool'}] {_msg_text(m)[:1500]}"
                             for m in old if _msg_text(m))
    prev = st.get("summary", "")
    new_summary = await _summarize(prev, transcript)
    if not new_summary:
        return recent   # modello non disponibile: nessuna compattazione, si va avanti come prima

    await _archive(db, conv, old)
    conv.settings = {**(conv.settings or {}), "compaction": {
        "upto": old[-1].created_at.isoformat(), "summary": new_summary,
        "archived_messages": int(st.get("archived_messages", 0)) + len(old),
        "at": datetime.now(timezone.utc).isoformat()}}
    await db.commit()
    log.info("Compattata %s: %d messaggi archiviati, riassunto %d caratteri", conv.id, len(old), len(new_summary))
    return fresh


async def _summarize(prev: str, transcript: str) -> str:
    from .memory import _cheap_provider_model
    from ..providers.registry import get_provider

    pm = _cheap_provider_model()
    if not pm:
        return ""
    pid, model = pm
    prompt = (f"RIASSUNTO PRECEDENTE:\n{prev or '(nessuno)'}\n\nNUOVI MESSAGGI DA INTEGRARE:\n{transcript[-14000:]}")
    out = ""
    try:
        async for ev in get_provider(pid).stream(model, SUMMARY_SYSTEM, [{"role": "user", "content": [{"type": "text", "text": prompt}]}],
                                                 [], max_tokens=900):
            if ev.type == "text_delta":
                out += ev.text
    except Exception as e:
        log.warning("Riassunto conversazione non riuscito (%s/%s): %s", pid, model, e)
        return ""
    return out.strip()


async def _archive(db: AsyncSession, conv: Conversation, old: list[Message]) -> None:
    """Appende i messaggi al file virtuale 'Cronologia chat' e (ri)indicizza i nuovi chunk."""
    from . import rag

    asset = (await db.execute(select(FileAsset).where(FileAsset.conversation_id == conv.id,
                                                     FileAsset.kind == "chat_history"))).scalar_one_or_none()
    if not asset:
        asset = FileAsset(family_id=conv.family_id, owner_id=conv.owner_id, project_id=conv.project_id, conversation_id=conv.id,
                          filename=HISTORY_FILENAME, mime="text/plain", size=0, path="", kind="chat_history",
                          extracted_text="", index_status="indexed")
        db.add(asset)
        await db.flush()
    text = "\n\n".join(f"[{m.created_at.strftime('%Y-%m-%d %H:%M')}] {'Utente' if m.role == 'user' else 'Assistente' if m.role == 'assistant' else 'Tool'}: {_msg_text(m)}"
                       for m in old if _msg_text(m))
    asset.extracted_text = ((asset.extracted_text or "") + "\n\n" + text).strip()
    asset.size = len(asset.extracted_text)
    # indicizza solo la parte nuova: chunk aggiuntivi con idx progressivo
    start_idx = asset.chunk_count or 0
    chunks = rag.chunk_text(text)
    vecs, model = await rag.embed_texts(chunks)
    for i, c in enumerate(chunks):
        db.add(Chunk(family_id=asset.family_id, file_id=asset.id, project_id=asset.project_id, conversation_id=conv.id,
                     idx=start_idx + i, text=c, embedding=vecs[i] if vecs else None, embedding_model=model))
    asset.chunk_count = start_idx + len(chunks)


def summary_block(conv: Conversation) -> str:
    st = state(conv)
    if not st.get("summary"):
        return ""
    n = st.get("archived_messages", 0)
    return (f"# Riassunto della conversazione finora ({n} messaggi precedenti archiviati; i dettagli integrali sono cercabili "
            f"con search_documents nel file '{HISTORY_FILENAME}')\n{st['summary']}")
