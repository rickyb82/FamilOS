from __future__ import annotations

import json
from collections.abc import AsyncIterator

from .base import PROGRESS_STEP, truncated_tool_warning, writing_status, BaseProvider, ProviderEvent, ToolSpec


class stream_cm_guard:
    """Chiude il context manager dello stream gia' aperto (entrata gestita a mano per poter riprovare)."""
    def __init__(self, cm):
        self.cm = cm

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return await self.cm.__aexit__(*exc)


class AnthropicProvider(BaseProvider):
    id = "anthropic"
    label = "Claude (Anthropic)"

    fallback_models = ["claude-sonnet-5-5", "claude-opus-5-5", "claude-haiku-5-5"]
    _adaptive_models: set[str] = set()   # modelli recenti: thinking "adaptive" + output_config.effort invece di budget_tokens

    def _thinking_kwargs(self, model: str, max_tokens: int) -> dict:
        budget = max(1024, min(int(self.thinking_budget or 4096), max_tokens - 1024))
        if model in self._adaptive_models:
            effort = "low" if budget <= 2048 else "medium" if budget <= 8192 else "high"
            return {"thinking": {"type": "adaptive"}, "output_config": {"effort": effort}}
        return {"thinking": {"type": "enabled", "budget_tokens": budget}}

    async def discover_models(self) -> list[dict]:
        from anthropic import AsyncAnthropic

        client = AsyncAnthropic(api_key=self.api_key)
        out = []
        async for m in client.models.list(limit=100):
            out.append({"id": m.id, "label": getattr(m, "display_name", None) or m.id})
        # ordina: sonnet, opus, haiku, poi il resto; piu' recenti prima
        rank = lambda i: (0 if "sonnet" in i else 1 if "opus" in i else 2 if "haiku" in i else 3)
        out.sort(key=lambda m: (rank(m["id"]), m["id"]))
        return out

    @staticmethod
    def _convert(messages: list[dict], keep_thinking: bool = False) -> list[dict]:
        out: list[dict] = []
        # I blocchi thinking firmati vanno rimandati solo per il turno in corso (dall'ultimo messaggio dell'utente in poi):
        # quelli dei turni passati sono inutili e, dopo una compattazione della cronologia, la firma non combacia piu'
        # ("Invalid signature in thinking block ... bound to a different conversation").
        last_user = max((i for i, m in enumerate(messages) if m["role"] == "user"), default=-1)
        for i, m in enumerate(messages):
            keep_think = keep_thinking and i > last_user
            blocks = []
            for b in m["content"]:
                t = b["type"]
                if t == "text":
                    if b["text"]:
                        blocks.append({"type": "text", "text": b["text"]})
                elif t == "image":
                    blocks.append({"type": "image", "source": {"type": "base64", "media_type": b["mime"], "data": b["data"]}})
                elif t == "thinking" and b.get("signature") and keep_think:
                    blocks.append({"type": "thinking", "thinking": b.get("thinking") or b.get("text", ""), "signature": b["signature"]})
                elif t == "redacted_thinking" and b.get("data") and keep_think:
                    blocks.append({"type": "redacted_thinking", "data": b["data"]})
                elif t == "tool_use":
                    blocks.append({"type": "tool_use", "id": b["id"], "name": b["name"], "input": b["input"]})
                elif t == "tool_result":
                    blocks.append({"type": "tool_result", "tool_use_id": b["tool_use_id"],
                                   "content": b["content"], "is_error": bool(b.get("is_error"))})
            role = "user" if m["role"] in ("user", "tool") else "assistant"
            if not blocks:
                continue
            # Anthropic richiede alternanza: unisci messaggi consecutivi dello stesso ruolo
            if out and out[-1]["role"] == role:
                out[-1]["content"].extend(blocks)
            else:
                out.append({"role": role, "content": blocks})
        return out

    async def stream(self, model, system, messages, tools, max_tokens=4096, system_dynamic="") -> AsyncIterator[ProviderEvent]:
        from anthropic import AsyncAnthropic

        client = AsyncAnthropic(api_key=self.api_key)
        # Prompt caching: breakpoint alla fine della parte stabile del system (copre anche i tool, che la precedono)
        # e sull'ultimo messaggio utente (cache incrementale della conversazione). La parte dinamica viene dopo.
        cache = {"type": "ephemeral"}
        sys_blocks = [{"type": "text", "text": system or " ", "cache_control": cache}]
        if system_dynamic:
            sys_blocks.append({"type": "text", "text": system_dynamic})
        msgs = self._convert(messages, keep_thinking=bool(self.thinking))
        for m in reversed(msgs):
            if m["role"] == "user":
                m["content"][-1] = {**m["content"][-1], "cache_control": cache}
                break
        kwargs = dict(model=model or self.default_model, system=sys_blocks, max_tokens=max_tokens, messages=msgs)
        if self.thinking:
            kwargs.update(self._thinking_kwargs(kwargs["model"], max_tokens))
        if tools:
            spec = []
            for t in tools:
                if t.name == "web_search" and t.input_schema.get("x-native"):
                    # ricerca web lato server di Anthropic: niente esecuzione locale, cita le fonti da se'
                    spec.append({"type": "web_search_20250305", "name": "web_search", "max_uses": 5})
                else:
                    spec.append({"name": t.name, "description": t.description, "input_schema": t.input_schema})
            kwargs["tools"] = spec

        current_tool: dict | None = None
        json_buf = ""
        next_report = PROGRESS_STEP
        stop_reason = None
        think_sig, in_think = "", False
        usage = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0}
        try:
            stream_cm = client.messages.stream(**kwargs)
            s = await stream_cm.__aenter__()
        except Exception as e:
            msg = str(e)
            if self.thinking and "adaptive" in msg and kwargs["model"] not in self._adaptive_models:
                # modello recente: ripeti con thinking adattivo e ricorda la scelta per questo modello
                self._adaptive_models.add(kwargs["model"])
                kwargs.pop("thinking", None)
                kwargs.update(self._thinking_kwargs(kwargs["model"], max_tokens))
                stream_cm = client.messages.stream(**kwargs)
                s = await stream_cm.__aenter__()
            elif self.thinking and "thinking" in msg.lower():
                # modello senza supporto al ragionamento, o firme dei blocchi thinking non piu' valide: riprova senza
                # ragionamento e senza i blocchi thinking nella cronologia
                kwargs.pop("thinking", None)
                kwargs.pop("output_config", None)
                if "signature" in msg.lower():
                    kwargs["messages"] = self._convert(messages, keep_thinking=False)
                    for m in reversed(kwargs["messages"]):
                        if m["role"] == "user":
                            m["content"][-1] = {**m["content"][-1], "cache_control": cache}
                            break
                stream_cm = client.messages.stream(**kwargs)
                s = await stream_cm.__aenter__()
            else:
                raise
        async with stream_cm_guard(stream_cm):
            async for ev in s:
                et = ev.type
                if et == "content_block_start":
                    cb = ev.content_block
                    if cb.type == "tool_use":
                        current_tool = {"id": cb.id, "name": cb.name, "input": {}}
                        json_buf = ""
                        next_report = PROGRESS_STEP
                        yield ProviderEvent("status", text=writing_status(cb.name, 0))
                    elif cb.type == "server_tool_use":
                        current_tool = None
                        yield ProviderEvent("status", text="🔎 Ricerca web (Claude)…")
                    elif cb.type == "thinking":
                        think_sig = ""
                        in_think = True
                    elif cb.type == "redacted_thinking":
                        yield ProviderEvent("thinking_block", tool_use={"type": "redacted_thinking", "data": getattr(cb, "data", "")})
                elif et == "content_block_delta":
                    d = ev.delta
                    if d.type == "text_delta":
                        yield ProviderEvent("text_delta", text=d.text)
                    elif d.type == "thinking_delta":
                        yield ProviderEvent("thinking_delta", text=d.thinking)
                    elif d.type == "signature_delta":
                        think_sig += d.signature
                    elif d.type == "input_json_delta":
                        json_buf += d.partial_json
                        if len(json_buf) >= next_report and current_tool is not None:
                            next_report += PROGRESS_STEP
                            yield ProviderEvent("status", text=writing_status(current_tool["name"], len(json_buf)))
                elif et == "content_block_stop":
                    if in_think:
                        in_think = False
                        yield ProviderEvent("thinking_block", tool_use={"type": "thinking", "signature": think_sig})
                    if current_tool is not None:
                        try:
                            current_tool["input"] = json.loads(json_buf) if json_buf.strip() else {}
                            yield ProviderEvent("tool_use", tool_use=current_tool)
                        except json.JSONDecodeError:
                            # input troncato (di solito max_tokens): niente tool, avvisa l'utente in chiaro
                            yield ProviderEvent("text_delta", text=truncated_tool_warning(current_tool["name"]))
                        current_tool = None
                elif et == "message_start":
                    u = ev.message.usage
                    usage["input_tokens"] = getattr(u, "input_tokens", 0) or 0          # esclude i token in cache
                    usage["cache_read_tokens"] = getattr(u, "cache_read_input_tokens", 0) or 0
                    usage["cache_write_tokens"] = getattr(u, "cache_creation_input_tokens", 0) or 0
                elif et == "message_delta":
                    usage["output_tokens"] = getattr(ev.usage, "output_tokens", 0)
                    stop_reason = getattr(ev.delta, "stop_reason", None) or stop_reason
        if stop_reason == "max_tokens" and current_tool is None and not json_buf:
            yield ProviderEvent("truncated")
        yield ProviderEvent("usage", usage=usage)
