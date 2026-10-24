"""Tool di ricerca semantica nei file del progetto / della chat (RAG)."""
from __future__ import annotations

from ..services import rag
from .registry import ToolContext, ToolResult, register


@register(
    "search_documents",
    "Cerca nei documenti disponibili (allegati e cartella CHAT di questa chat, documenti dell'organizzazione del tuo reparto, "
    "file del progetto) i passaggi più pertinenti a una domanda. "
    "Usalo prima di rispondere a domande sul contenuto dei file (relazioni, manuali, appunti, tabelle). "
    "Restituisce estratti con nome file e numero di parte; cita il file quando usi le informazioni.",
    {"type": "object", "properties": {
        "query": {"type": "string", "description": "Domanda o parole chiave da cercare"},
        "file_id": {"type": "string", "description": "Opzionale: limita la ricerca a un file specifico"},
        "top_k": {"type": "integer", "description": "Numero di estratti (1-15)"}},
     "required": ["query"]},
)
async def search_documents(args: dict, ctx: ToolContext) -> ToolResult:
    query = str(args.get("query", "")).strip()
    if not query:
        return ToolResult("Query vuota", is_error=True)
    top_k = max(1, min(int(args.get("top_k", 6) or 6), 15))
    file_ids = [str(args["file_id"])] if args.get("file_id") else None
    if file_ids:
        from ..services.access import require_access
        await require_access(ctx.db, "file", file_ids[0], ctx.user)
    folder_ids = None
    if not file_ids:
        from ..models import Conversation
        from ..services import workspace
        conv = await ctx.db.get(Conversation, ctx.conversation_id) if ctx.conversation_id else None
        folder_ids = await workspace.context_folder_ids(ctx.db, ctx.user, conv) or None
    hits = await rag.search(
        ctx.db, query, ctx.user.family_id,
        project_ids=[ctx.project_id] if ctx.project_id and not file_ids else None,
        conversation_ids=[ctx.conversation_id] if ctx.conversation_id and not file_ids else None,
        file_ids=file_ids, top_k=top_k, folder_ids=folder_ids,
        lexical_only=not ctx.policy.get("cloud_allowed",True),
    )
    if not hits:
        return ToolResult("Nessun passaggio pertinente trovato nei documenti disponibili "
                          "(verifica che i file siano stati indicizzati o prova parole diverse).")
    return ToolResult(rag.format_hits(hits))
