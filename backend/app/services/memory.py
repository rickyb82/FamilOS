"""Memoria di famiglia: fatti durevoli estratti dalle chat, filtrati da un "firewall" (regole + modello), approvati
dall'amministratore (o auto-approvati se consentito) e poi recuperati nel prompt per rendere le risposte piu' precise.

Pipeline:  turno concluso → (ogni N messaggi) estrazione con un modello economico → firewall (privacy, utilita',
duplicati) → coda 'pending' (o 'approved') → al turno successivo: recupero top-k (embedding/BM25) + voci fissate.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..models import Conversation, MemoryItem, Message, Role, User
from ..providers.registry import _providers, get_provider
from . import rag

log = logging.getLogger(__name__)
settings = get_settings()

CATEGORIES = ["famiglia", "preferenze", "scuola", "casa", "lavoro", "hobby", "progetti", "regole", "altro"]
EXTRACT_EVERY_USER_MESSAGES = 4          # estrai ogni N messaggi utente per conversazione
EXTRACT_MIN_INTERVAL = timedelta(minutes=5)
MAX_ITEMS_PER_RUN = 8
MAX_MEMORY_CHARS_IN_PROMPT = 2500

# ------------------------------------------------------------------ firewall: regole fisse
# Mai in memoria: dati sensibili per legge/buon senso. Il modello riceve la stessa istruzione, questo e' il doppio controllo.
BLOCK_PATTERNS = [
    (re.compile(r"\bIT\d{2}[A-Z]\d{10}[A-Z0-9]{12}\b", re.I), "IBAN"),
    (re.compile(r"\b(?:\d[ -]?){13,19}\b"), "numero di carta"),
    (re.compile(r"\b[A-Z]{6}\d{2}[A-Z]\d{2}[A-Z]\d{3}[A-Z]\b", re.I), "codice fiscale"),
    (re.compile(r"\b(password|pwd|pin|otp|token|api[ _-]?key|segreto)\b\s*[:=]?\s*\S+", re.I), "credenziale"),
    (re.compile(r"\b(diagnos|malatt|terapia|farmac|psicolog|psichiatr|depress|ansia|tumor|diabet|HIV|gravid|aborto|disabil)\w*", re.I), "salute"),
    (re.compile(r"\b(religio|cattolic|musulman|ebre|ateo|voto|partito|elettor|sindacat|orientamento sessuale|gay|lesbic|trans)\w*", re.I), "dato sensibile"),
    (re.compile(r"\b(stipendio|reddito|guadagn|debit|mutuo di|risparmi|conto corrente|saldo)\w*\s*(di|:)?\s*\d", re.I), "finanza"),
]
JUNK_PATTERNS = [
    re.compile(r"^(l'utente|utente)\s+(ha chiesto|vuole|chiede)\b", re.I),   # non e' un fatto durevole
    re.compile(r"\b(oggi|domani|ieri|stasera|adesso|in questo momento)\b", re.I),
]


@dataclass
class Candidate:
    text: str
    category: str
    scope: str          # family | user
    confidence: int     # 1-5
    subject_user_id: str | None = None


def firewall(text: str) -> str | None:
    """Ritorna il motivo del blocco, o None se la voce passa le regole fisse."""
    t = text.strip()
    if len(t) < 8 or len(t) > 400:
        return "lunghezza"
    for pat, why in BLOCK_PATTERNS:
        if pat.search(t):
            return why
    for pat in JUNK_PATTERNS:
        if pat.search(t):
            return "non durevole"
    return None


# ------------------------------------------------------------------ estrazione
EXTRACT_SYSTEM = """Sei il "bibliotecario" di un assistente di famiglia. Dagli ultimi messaggi di una chat estrai SOLO fatti
durevoli e utili per le conversazioni future: nomi/eta'/ruoli dei familiari, scuola e classe, preferenze e gusti stabili,
hobby, progetti in corso, dettagli della casa, animali, regole di famiglia, come l'utente preferisce le risposte.
NON estrarre: dati sanitari, religione, politica, orientamento sessuale, dati finanziari con cifre, password/chiavi/codici,
numeri di documenti, cose temporanee (impegni di oggi, umore), richieste puntuali ("ha chiesto una poesia"), opinioni tue.
Scrivi ogni fatto come frase breve in terza persona, in italiano, autonoma (es. "Lorenzo (7 anni) frequenta la seconda
elementare e adora i dinosauri."). Se non c'e' nulla di durevole restituisci una lista vuota.
Rispondi SOLO con JSON: {"items":[{"text":"...","category":"famiglia|preferenze|scuola|casa|lavoro|hobby|progetti|regole|altro",
"scope":"family|user","confidence":1-5}]}  (scope "user" = riguarda solo chi scrive; "family" = utile a tutta la famiglia)."""


def _cheap_provider_model() -> tuple[str, str] | None:
    """Modello economico per l'estrazione: preferenza impostabile, altrimenti locale → mini cloud."""
    pref = (settings.memory_model or "auto").strip()
    provs = _providers()
    if pref and pref != "auto" and "/" in pref:
        pid, model = pref.split("/", 1)
        if pid in provs:
            return pid, model
    if "ollama" in provs:
        return "ollama", provs["ollama"].default_model
    for pid, candidates in (("openai", [get_settings().openai_default_model]),):
        if pid in provs:
            known = {m["id"] for m in provs[pid].models()}
            for c in candidates:
                if not known or c in known:
                    return pid, c
            return pid, provs[pid].default_model
    return None


async def _llm_json(system: str, prompt: str, max_tokens: int = 800) -> dict:
    pm = _cheap_provider_model()
    if not pm:
        return {}
    pid, model = pm
    out = ""
    async for ev in get_provider(pid).stream(model, system, [{"role": "user", "content": [{"type": "text", "text": prompt}]}], [],
                                             max_tokens=max_tokens):
        if ev.type == "text_delta":
            out += ev.text
    m = re.search(r"\{.*\}", out, re.S)
    try:
        return json.loads(m.group(0)) if m else {}
    except json.JSONDecodeError:
        return {}


def enabled() -> bool:
    return str(settings.memory_enabled).lower() in ("1", "true", "yes")


async def maybe_extract(conv_id: str, user_id: str) -> None:
    """Chiamato a fine turno (in background). Decide se e' il momento di estrarre e lo fa."""
    if not enabled():
        return
    from ..db import SessionLocal
    try:
        async with SessionLocal() as db:
            conv = await db.get(Conversation, conv_id)
            user = await db.get(User, user_id)
            if not conv or not user or user.role == Role.child:
                return
            msgs = (await db.execute(select(Message).where(Message.conversation_id == conv_id)
                                     .order_by(Message.created_at))).scalars().all()
            user_msgs = [m for m in msgs if m.role == "user"]
            state = _state(conv)
            done_count = int(state.get("user_count", 0))
            last_at = state.get("at")
            if len(user_msgs) - done_count < EXTRACT_EVERY_USER_MESSAGES:
                return
            if last_at and datetime.fromisoformat(last_at) > datetime.now(timezone.utc) - EXTRACT_MIN_INTERVAL:
                return
            # finestra: dagli ultimi messaggi gia' processati in poi (max ~6000 caratteri)
            start = user_msgs[done_count].created_at if done_count < len(user_msgs) else None
            window = [m for m in msgs if m.role in ("user", "assistant") and (start is None or m.created_at >= start)]
            transcript = "\n".join(f"[{'Utente' if m.role == 'user' else 'Assistente'}] "
                                   + " ".join(b.get("text", "") for b in (m.content or []) if b.get("type") == "text")[:800]
                                   for m in window)[-6000:]
            if len(transcript) < 80:
                return
            data = await _llm_json(EXTRACT_SYSTEM, f"Chi scrive: {user.display_name} ({'bambino/a' if user.role == Role.child else 'adulto'})."
                                                   f"\n\nMessaggi:\n{transcript}")
            cands = []
            for it in (data.get("items") or [])[:MAX_ITEMS_PER_RUN]:
                try:
                    cands.append(Candidate(str(it.get("text", "")).strip(), str(it.get("category", "altro")).lower(),
                                           "family" if str(it.get("scope", "family")).lower() == "family" else "user",
                                           int(it.get("confidence", 3) or 3), user.id))
                except (TypeError, ValueError):
                    continue
            n = await _ingest(db, user, cands, source_conv=conv.id)
            _set_state(conv, {"user_count": len(user_msgs), "at": datetime.now(timezone.utc).isoformat()})
            await db.commit()
            if n:
                log.info("Memoria: %d nuove voci da %s (%s)", n, user.username, conv.id)
    except Exception:
        log.exception("Estrazione memoria fallita")


def _state(conv: Conversation) -> dict:
    return dict((conv.settings or {}).get("memory", {}))


def _set_state(conv: Conversation, st: dict) -> None:
    s = dict(conv.settings or {})
    s["memory"] = st
    conv.settings = s


async def _ingest(db: AsyncSession, author: User, cands: list[Candidate], source_conv: str | None = None,
                  force_status: str | None = None) -> int:
    """Firewall + dedup + salvataggio. Ritorna il numero di voci create."""
    if not cands:
        return 0
    existing = (await db.execute(select(MemoryItem).where(MemoryItem.family_id == author.family_id,
                                                           MemoryItem.status != "rejected"))).scalars().all()
    ex_texts = [e.text for e in existing]
    auto_ok = str(settings.memory_auto_approve).lower() in ("1", "true", "yes") and author.role != Role.child
    created = 0
    new_items: list[MemoryItem] = []
    for c in cands:
        why = firewall(c.text)
        if why:
            log.info("Memoria: scartata (%s): %s", why, c.text[:80])
            continue
        if c.category not in CATEGORIES:
            c.category = "altro"
        # duplicati: BM25 contro le voci esistenti (e quelle appena aggiunte)
        pool = ex_texts + [i.text for i in new_items]
        if pool:
            scores = rag.bm25_scores(c.text, pool)
            best = max(scores) if scores else 0
            toks = set(rag.tokenize(c.text))
            if best > 0 and toks:
                j = scores.index(best)
                overlap = len(toks & set(rag.tokenize(pool[j]))) / max(1, len(toks))
                if overlap >= 0.8:
                    continue
        status = force_status or ("approved" if auto_ok and c.confidence >= 4 else "pending")
        item = MemoryItem(family_id=author.family_id, user_id=author.id,
                          subject_user_id=c.subject_user_id if c.scope == "user" else None,
                          scope=c.scope, category=c.category, text=c.text[:400], confidence=c.confidence,
                          status=status, source_conversation_id=source_conv)
        db.add(item)
        new_items.append(item)
        created += 1
    if new_items:
        await db.flush()
        await embed_items(new_items)
    return created


async def embed_items(items: list[MemoryItem]) -> None:
    vecs, model = await rag.embed_texts([i.text for i in items])
    if vecs:
        for i, v in zip(items, vecs):
            i.embedding, i.embedding_model = v, model


# ------------------------------------------------------------------ recupero per il prompt
async def recall(db: AsyncSession, user: User, query: str, k: int | None = None) -> list[MemoryItem]:
    """Voci approvate pertinenti: fissate + top-k per similarita' (embedding se c'e', altrimenti BM25)."""
    if not enabled() or user.role == Role.child:
        return []
    k = k or int(settings.memory_top_k or 8)
    rows = (await db.execute(select(MemoryItem).where(
        MemoryItem.family_id == user.family_id, MemoryItem.status == "approved"))).scalars().all()
    rows = [r for r in rows if r.scope == "family" or r.subject_user_id == user.id or r.user_id == user.id]
    if not rows:
        return []
    pinned = [r for r in rows if r.pinned]
    rest = [r for r in rows if not r.pinned]
    chosen: list[MemoryItem] = []
    if rest and query.strip():
        qv, qmodel = await rag.embed_texts([query], for_query=True)
        scored: list[tuple[float, MemoryItem]] = []
        comparable = [r for r in rest if r.embedding and (r.embedding_model or qmodel) == qmodel and qv and len(r.embedding) == len(qv[0])]
        if qv and comparable:
            for r in comparable:
                scored.append((rag._cosine(qv[0], r.embedding), r))
            scored.sort(key=lambda x: -x[0])
            chosen = [r for s, r in scored[:k] if s > 0.25]
        else:
            sc = rag.bm25_scores(query, [r.text for r in rest])
            order = sorted(range(len(rest)), key=lambda i: -sc[i])
            chosen = [rest[i] for i in order[:k] if sc[i] > 0]
    out, total = [], 0
    for r in pinned + chosen:
        if total + len(r.text) > MAX_MEMORY_CHARS_IN_PROMPT:
            break
        out.append(r)
        total += len(r.text)
    return out


def format_for_prompt(items: list[MemoryItem]) -> str:
    if not items:
        return ""
    lines = "\n".join(f"- {i.text}" for i in items)
    return ("# Memoria di famiglia (fatti verificati, usali quando pertinenti senza elencarli)\n" + lines)


# ------------------------------------------------------------------ richieste esplicite ("ricordati che…")
async def remember(db: AsyncSession, user: User, text: str, scope: str = "family", category: str = "altro") -> tuple[bool, str]:
    why = firewall(text)
    if why:
        return False, f"Non posso salvarlo in memoria ({why})."
    status = "pending" if user.role == Role.child else "approved"
    n = await _ingest(db, user, [Candidate(text, category, scope, 5, user.id)], force_status=status)
    await db.commit()
    if n == 0:
        return True, "Lo sapevo gia' (voce simile presente)."
    return True, "Salvato in memoria." + (" Un genitore dovra' approvarlo." if status == "pending" else "")


async def forget(db: AsyncSession, user: User, query: str) -> int:
    rows = (await db.execute(select(MemoryItem).where(MemoryItem.family_id == user.family_id,
                                                       MemoryItem.status != "rejected"))).scalars().all()
    if user.role == Role.child:
        rows = [r for r in rows if r.user_id == user.id]
    if not rows:
        return 0
    sc = rag.bm25_scores(query, [r.text for r in rows])
    toks = set(rag.tokenize(query))
    n = 0
    for r, s in zip(rows, sc):
        overlap = len(toks & set(rag.tokenize(r.text))) / max(1, len(toks))
        if s > 0 and overlap >= 0.6:
            r.status = "rejected"
            n += 1
    await db.commit()
    return n
