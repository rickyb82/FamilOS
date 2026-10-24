"""Tool remember/forget: memoria di famiglia su richiesta esplicita dell'utente."""
from __future__ import annotations

from ..services import memory
from .registry import ToolContext, ToolResult, register


@register(
    "remember",
    "Salva nella memoria di famiglia un fatto durevole che l'utente ti ha chiesto ESPLICITAMENTE di ricordare "
    "('ricordati che…', 'segnati che…'). Scrivilo come frase breve in terza persona. Non usarlo di tua iniziativa e mai "
    "per dati sanitari, finanziari, credenziali, religione/politica.",
    {"type": "object", "properties": {
        "text": {"type": "string", "description": "Il fatto, es. 'Lorenzo gioca a basket il martedi' e il giovedi''"},
        "scope": {"type": "string", "enum": ["family", "user"], "description": "family = utile a tutti; user = riguarda solo chi scrive"},
        "category": {"type": "string", "enum": memory.CATEGORIES}},
     "required": ["text"]},
    usage_kind="tool",
)
async def remember(args: dict, ctx: ToolContext) -> ToolResult:
    ok, msg = await memory.remember(ctx.db, ctx.user, str(args.get("text", "")), str(args.get("scope", "family")),
                                    str(args.get("category", "altro")))
    return ToolResult(msg, is_error=not ok)


@register(
    "forget",
    "Rimuove dalla memoria di famiglia le voci che corrispondono alla descrizione, quando l'utente chiede di dimenticare qualcosa.",
    {"type": "object", "properties": {"query": {"type": "string", "description": "Cosa dimenticare (parole chiave della voce)"}},
     "required": ["query"]},
    usage_kind="tool",
)
async def forget(args: dict, ctx: ToolContext) -> ToolResult:
    n = await memory.forget(ctx.db, ctx.user, str(args.get("query", "")))
    return ToolResult(f"Rimosse {n} voci dalla memoria." if n else "Nessuna voce corrispondente in memoria.")
