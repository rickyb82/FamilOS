"""RAG sui file di progetto/chat: chunking, embedding (OpenAI o Ollama), ricerca ibrida (semantica + BM25).

Funziona anche senza chiavi di embedding: in quel caso la ricerca è solo per parole chiave (BM25).
"""
from __future__ import annotations

import logging
import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import SessionLocal
from ..models import Chunk, FileAsset

log = logging.getLogger(__name__)
settings = get_settings()

INDEXABLE_PREFIXES = ("text/", "application/pdf", "application/json")
INDEXABLE_EXT = {".pdf", ".docx", ".xlsx", ".xlsm", ".txt", ".md", ".csv", ".json", ".html", ".py", ".js", ".ts"}


def is_indexable(asset: FileAsset) -> bool:
    if asset.kind == "chat_history":
        return True
    return asset.mime.startswith(INDEXABLE_PREFIXES) or Path(asset.filename).suffix.lower() in INDEXABLE_EXT


# ---------------------------------------------------------------- chunking
def chunk_text(text: str, size: int | None = None, overlap: int | None = None) -> list[str]:
    size = size or settings.rag_chunk_chars
    overlap = overlap or settings.rag_chunk_overlap
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not text:
        return []
    paragraphs = re.split(r"\n\s*\n", text)
    chunks: list[str] = []
    buf = ""
    for p in paragraphs:
        p = p.strip()
        if not p:
            continue
        # paragrafo troppo lungo: spezzalo per frasi/righe
        while len(p) > size:
            cut = max(p.rfind(". ", 0, size), p.rfind("\n", 0, size))
            cut = cut + 1 if cut > size // 2 else size
            part, p = p[:cut].strip(), p[cut:].strip()
            if buf:
                chunks.append(buf)
                buf = ""
            chunks.append(part)
        if len(buf) + len(p) + 2 <= size:
            buf = f"{buf}\n\n{p}" if buf else p
        else:
            if buf:
                chunks.append(buf)
            buf = p
    if buf:
        chunks.append(buf)
    # sovrapposizione: aggiungi la coda del chunk precedente in testa al successivo
    if overlap > 0 and len(chunks) > 1:
        out = [chunks[0]]
        for prev, cur in zip(chunks, chunks[1:]):
            out.append(prev[-overlap:].lstrip() + "\n" + cur if len(prev) > overlap else cur)
        chunks = out
    return chunks


# ---------------------------------------------------------------- embeddings
def _embedding_backend() -> tuple[str, str] | None:
    p = settings.embedding_provider
    if p == "none":
        return None
    if p in ("auto", "ollama") and settings.ollama_url:
        # locale: bge-m3 (multilingua, ottimo in italiano), qwen3-embedding, nomic-embed-text, mxbai-embed-large...
        return "ollama", settings.embedding_model or "bge-m3"
    if p in ("auto", "openai") and settings.openai_api_key:
        return "openai", settings.embedding_model or "text-embedding-3-small"
    return None


def embeddings_available() -> bool:
    return _embedding_backend() is not None


async def embed_texts(texts: list[str], for_query: bool = False) -> tuple[list[list[float]] | None, str]:
    backend = _embedding_backend()
    if not backend or not texts:
        return None, ""
    provider, model = backend
    try:
        if provider == "ollama":
            import httpx

            from .neturl import host_url
            base = host_url(settings.ollama_url).rstrip("/")
            out: list[list[float]] = []
            async with httpx.AsyncClient(timeout=120) as c:
                for i in range(0, len(texts), 32):
                    r = await c.post(f"{base}/api/embed", json={"model": model, "input": [t[:8000] for t in texts[i:i + 32]]})
                    if r.status_code == 404:
                        raise RuntimeError(f"modello di embedding '{model}' non trovato su Ollama: esegui `ollama pull {model}`")
                    r.raise_for_status()
                    out.extend(r.json().get("embeddings", []))
            return out, model
        if provider == "openai":
            from openai import AsyncOpenAI

            client = AsyncOpenAI(api_key=settings.openai_api_key)
            out: list[list[float]] = []
            for i in range(0, len(texts), 100):
                r = await client.embeddings.create(model=model, input=[t[:8000] for t in texts[i:i + 100]])
                out.extend(d.embedding for d in r.data)
            return out, model
    except Exception as e:
        log.warning("Embedding non disponibile (%s): %s", provider, e)
    return None, ""


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


# ---------------------------------------------------------------- BM25 (parole chiave)
_token_re = re.compile(r"[a-zàèéìòù0-9]{2,}", re.IGNORECASE)
STOP = set("il lo la i gli le un uno una di a da in con su per tra fra e o ma che chi cui non più come del della dello dei "
           "degli delle al allo alla ai agli alle dal dallo dalla dai dagli dalle nel nello nella nei negli nelle sul sullo "
           "sulla sui sugli sulle è sono ho hai ha mi ti si ci vi the of and to in is are for on with this that".split())


def tokenize(s: str) -> list[str]:
    return [t.lower() for t in _token_re.findall(s) if t.lower() not in STOP]


def bm25_scores(query: str, docs: list[str], k1: float = 1.5, b: float = 0.75) -> list[float]:
    q = tokenize(query)
    if not q or not docs:
        return [0.0] * len(docs)
    toks = [tokenize(d) for d in docs]
    n = len(docs)
    avgdl = sum(len(t) for t in toks) / n or 1.0
    df = Counter()
    for t in toks:
        df.update(set(t))
    scores = []
    for t in toks:
        tf = Counter(t)
        s = 0.0
        for term in q:
            if term not in tf:
                continue
            idf = math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5))
            s += idf * tf[term] * (k1 + 1) / (tf[term] + k1 * (1 - b + b * len(t) / avgdl))
        scores.append(s)
    return scores


# ---------------------------------------------------------------- indicizzazione
async def index_file(file_id: str, db: AsyncSession | None = None) -> int:
    """(Ri)indicizza un file. Ritorna il numero di chunk creati."""
    own = db is None
    db = db or SessionLocal()
    try:
        asset = await db.get(FileAsset, file_id)
        if not asset:
            return 0
        if not is_indexable(asset):
            asset.index_status = "none"
            await db.commit()
            return 0
        try:
            from ..tools.documents import extract_text  # import locale: evita cicli

            if asset.extracted_text is None and asset.path:
                asset.extracted_text = extract_text(Path(asset.path), asset.mime) or ""
            chunks = chunk_text(asset.extracted_text or "")
            # Prima gli embedding (chiamata di rete, anche decine di secondi), poi la transazione di scrittura: SQLite ha un
            # solo scrittore e tenerlo bloccato durante la rete faceva fallire le chat con "database is locked".
            from ..models import User, Role
            from ..policy.education import cloud_allowed
            owner = await db.get(User,asset.owner_id)
            vecs, model = (None, "") if owner and owner.role == Role.child and not cloud_allowed(owner) else await embed_texts(chunks)
            await db.execute(delete(Chunk).where(Chunk.file_id == asset.id))
            for i, c in enumerate(chunks):
                db.add(Chunk(family_id=asset.family_id, file_id=asset.id, project_id=asset.project_id,
                             conversation_id=asset.conversation_id, folder_id=asset.folder_id, idx=i, text=c,
                             embedding=vecs[i] if vecs else None, embedding_model=model))
            asset.chunk_count = len(chunks)
            asset.index_status = "indexed"
            await db.commit()
            log.info("Indicizzato %s: %d chunk (%s)", asset.filename, len(chunks), model or "solo BM25")
            return len(chunks)
        except Exception as e:
            log.exception("Indicizzazione fallita per %s", file_id)
            asset.index_status = "failed"
            await db.commit()
            return 0
    finally:
        if own:
            await db.close()


async def index_file_background(file_id: str) -> None:
    """Da usare con BackgroundTasks: apre una sessione propria."""
    await index_file(file_id)


# ---------------------------------------------------------------- ricerca
@dataclass
class Hit:
    file_id: str
    filename: str
    idx: int
    text: str
    score: float


async def search(db: AsyncSession, query: str, family_id: str, project_ids: list[str] | None = None,
                 conversation_ids: list[str] | None = None, file_ids: list[str] | None = None,
                 top_k: int | None = None, folder_ids: list[str] | set[str] | None = None, lexical_only: bool = False) -> list[Hit]:
    top_k = top_k or settings.rag_top_k
    conds = []
    if project_ids:
        conds.append(Chunk.project_id.in_(project_ids))
    if conversation_ids:
        conds.append(Chunk.conversation_id.in_(conversation_ids))
    if file_ids:
        conds.append(Chunk.file_id.in_(file_ids))
    if folder_ids:
        conds.append(Chunk.folder_id.in_(list(folder_ids)))   # cartelle di reparto + cartella della chat (e sottocartelle)
    if not conds:
        return []
    rows = (await db.execute(select(Chunk, FileAsset.filename).join(FileAsset, FileAsset.id == Chunk.file_id)
                             .where(Chunk.family_id == family_id, or_(*conds)))).all()
    if not rows:
        return []
    chunks = [r[0] for r in rows]
    names = [r[1] for r in rows]
    kw = bm25_scores(query, [c.text for c in chunks])
    kw_max = max(kw) or 1.0
    combined = [s / kw_max for s in kw]

    have_vec = [] if lexical_only else [i for i, c in enumerate(chunks) if c.embedding]
    if have_vec:
        qv, qmodel = await embed_texts([query], for_query=True)
        # confronta solo vettori prodotti dallo stesso modello (dopo un cambio modello, reindicizza dal pannello)
        have_vec = [i for i in have_vec if (chunks[i].embedding_model or qmodel) == qmodel and len(chunks[i].embedding) == (len(qv[0]) if qv else 0)]
        if qv and have_vec:
            sims = {i: _cosine(qv[0], chunks[i].embedding) for i in have_vec}
            lo, hi = min(sims.values()), max(sims.values())
            span = (hi - lo) or 1.0
            for i in range(len(chunks)):
                v = (sims[i] - lo) / span if i in sims else 0.0
                combined[i] = 0.65 * v + 0.35 * combined[i]

    order = sorted(range(len(chunks)), key=lambda i: combined[i], reverse=True)
    hits = [Hit(chunks[i].file_id, names[i], chunks[i].idx, chunks[i].text, round(combined[i], 4))
            for i in order[:top_k] if combined[i] > 0]
    return hits


def format_hits(hits: list[Hit]) -> str:
    if not hits:
        return ""
    parts = []
    for h in hits:
        parts.append(f"[{h.filename} · parte {h.idx + 1} · id {h.file_id}]\n{h.text}")
    return "\n\n---\n\n".join(parts)
