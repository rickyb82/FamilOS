"""Tool ask_model: il modello in uso delega una domanda a un altro provider/modello (seconda opinione, confronto,
compito specifico) e ne riporta la risposta. Solo per chi puo' scegliere il provider (adulti)."""
from __future__ import annotations

import logging

from sqlalchemy import select

from ..config import get_settings
from ..policy.permissions import log_usage
from ..providers.registry import available_providers, get_provider
from .registry import ToolContext, ToolResult, register

log = logging.getLogger(__name__)
settings = get_settings()

ALIASES = {'gpt': 'openai',
 'chatgpt': 'openai',
 'openai': 'openai',
 'ollama': 'ollama',
 'locale': 'ollama',
 'local': 'ollama',
 'qwen': 'ollama',
 'gemma': 'ollama',
 'gpt-oss': 'ollama'}
MAX_CONTEXT_MESSAGES = 12
MAX_CONTEXT_CHARS = 12_000


def _resolve(provider: str, model: str | None, allowed: list[str]) -> tuple[str, str]:
    key = (provider or "").strip().lower()
    pid = ALIASES.get(key)
    if not pid:
        # accetta anche un id di modello al posto del provider (es. "gpt-4.1")
        for prefix, p in (("gpt-oss", "ollama"), ("gpt", "openai"), ("o1", "openai"), ("o3", "openai"), ("o4", "openai"), ("qwen", "ollama"), ("gemma", "ollama"), ("llama", "ollama"), ("mistral", "ollama")):
            if key.startswith(prefix):
                pid, model = p, model or key
                break
    if not pid:
        raise ValueError(f"Provider '{provider}' sconosciuto: usa openai oppure ollama")
    if pid not in allowed:
        raise ValueError(f"Il provider {pid} non e' consentito per questo utente")
    prov = get_provider(pid)
    if prov.id != pid:
        raise ValueError(f"Il provider {pid} non e' configurato (manca la chiave API)")
    ids = {m["id"] for m in prov.models()}
    if model:
        m = model.strip().lower().replace(" ", "")
        if ids and m not in ids:
            # prova un match parziale (es. "qwen" -> qwen3:8b)
            cand = [i for i in sorted(ids) if m in i]
            if not cand:
                raise ValueError(f"Modello '{model}' non trovato per {pid}. Disponibili: {', '.join(sorted(ids)[:15])}")
            m = cand[0]
        model = m
    return pid, model or prov.default_model


@register("ask_model", "Consulta OpenAI oppure Ollama per una seconda opinione o un compito specifico.",
    {"type":"object","properties":{"provider":{"type":"string","enum":["openai","ollama"]},"model":{"type":"string"},"prompt":{"type":"string"},"include_context":{"type":"boolean"},"with_web_search":{"type":"boolean"}},"required":["provider","prompt"]},
    usage_kind="tool")
async def ask_model(args: dict, ctx: ToolContext) -> ToolResult:
    prompt = str(args.get("prompt", "")).strip()
    if not prompt:
        return ToolResult("Prompt vuoto", is_error=True)
    allowed = list(ctx.policy.get("allowed_providers") or [])
    try:
        pid, model = _resolve(str(args.get("provider", "")), args.get("model"), allowed)
    except ValueError as e:
        return ToolResult(str(e), is_error=True)
    provider = get_provider(pid)

    # contesto: ultimi messaggi testuali della conversazione (senza tool e senza il turno corrente in corso)
    context = ""
    if args.get("include_context", True) and ctx.conversation_id and ctx.db is not None:
        from ..models import Message
        rows = (await ctx.db.execute(select(Message).where(Message.conversation_id == ctx.conversation_id)
                                     .order_by(Message.created_at.desc()).limit(MAX_CONTEXT_MESSAGES))).scalars().all()
        lines = []
        for m in reversed(rows):
            if m.role not in ("user", "assistant"):
                continue
            txt = " ".join(b.get("text", "") for b in (m.content or []) if b.get("type") == "text").strip()
            if txt:
                lines.append(f"[{'Utente' if m.role == 'user' else 'Assistente'}] {txt}")
        context = "\n".join(lines)[-MAX_CONTEXT_CHARS:]

    system = ("Sei un assistente consultato da un altro assistente AI per conto di un utente. Rispondi in italiano, "
              "in modo completo ma senza preamboli, direttamente al compito richiesto.")
    user_text = prompt if not context else (f"Contesto della conversazione (per riferimento):\n{context}\n\n---\n"
                                             f"Richiesta:\n{prompt}")
    messages = [{"role": "user", "content": [{"type": "text", "text": user_text}]}]

    text, usage = "", {}
    tools_spec = []
    if args.get("with_web_search"):
        from .registry import get_tool
        ws = get_tool("web_search")
        if ws:
            tools_spec = [ws.spec]
            system += (" Hai a disposizione il tool web_search: usalo per informazioni aggiornate o verificabili e cita le fonti "
                       "(titolo e URL). Non inventare dettagli su persone: riporta solo cio' che trovi.")
    used_tools: list[str] = []
    try:
        for _round in range(3):
            round_text, tool_uses = "", []
            async for ev in provider.stream(model, system, messages, tools_spec, max_tokens=min(settings.max_output_tokens, 8192)):
                if ev.type == "text_delta":
                    round_text += ev.text
                elif ev.type == "tool_use" and ev.tool_use:
                    tool_uses.append(ev.tool_use)
                elif ev.type == "usage":
                    u = ev.usage or {}
                    for k, v in u.items():
                        usage[k] = int(usage.get(k, 0) or 0) + int(v or 0)
            text = round_text
            if not tool_uses or not tools_spec:
                break
            # esegui le ricerche richieste e rimanda i risultati
            blocks = ([{"type": "text", "text": round_text}] if round_text else []) + [{"type": "tool_use", **tu} for tu in tool_uses]
            messages.append({"role": "assistant", "content": blocks})
            results = []
            from .registry import get_tool
            for tu in tool_uses:
                tool = get_tool(tu["name"])
                if not tool:
                    results.append({"type": "tool_result", "tool_use_id": tu["id"], "name": tu["name"], "content": "tool non disponibile", "is_error": True})
                    continue
                r = await tool.fn(tu["input"] or {}, ctx)
                used_tools.append(f"{tu['name']}({(tu['input'] or {}).get('query', '')})")
                results.append({"type": "tool_result", "tool_use_id": tu["id"], "name": tu["name"], "content": r.content[:20000], "is_error": r.is_error})
            messages.append({"role": "tool", "content": results})
            tools_spec = tools_spec if _round < 1 else []   # ultimo giro: solo risposta
    except Exception as e:
        log.exception("ask_model %s/%s", pid, model)
        return ToolResult(f"Errore dal provider {provider.label} ({model}): {e}", is_error=True)
    if usage:
        await log_usage(ctx.db, ctx.user, "message", pid, model, int(usage.get("input_tokens", 0) or 0),
                        int(usage.get("output_tokens", 0) or 0), detail="ask_model",
                        cache_read_tokens=int(usage.get("cache_read_tokens", 0) or 0),
                        cache_write_tokens=int(usage.get("cache_write_tokens", 0) or 0))
    header = f"Risposta di {provider.label} ({model})" + (f" dopo {len(used_tools)} ricerche web" if used_tools else "") + ":\n\n"
    return ToolResult(header + (text.strip() or "(nessuna risposta)"))


def describe_available(allowed: list[str]) -> str:
    """Riga per il prompt di sistema: quali altri modelli si possono consultare con ask_model."""
    parts = []
    for p in available_providers():
        if p["id"] in allowed and p["id"] != "mock":
            ids = [m["id"] for m in p["models"]][:6]
            parts.append(f"{p['label']} [{p['id']}] (default {p['default_model']}; altri: {', '.join(ids)})")
    return "; ".join(parts)
