from __future__ import annotations

import json
from collections.abc import AsyncIterator

from .base import PROGRESS_STEP, BaseProvider, ProviderEvent, ToolSpec, truncated_tool_warning, writing_status


class OpenAIProvider(BaseProvider):
    id = "openai"
    label = "ChatGPT (OpenAI)"

    fallback_models = ["gpt-6-luna", "gpt-6.1-sol", "gpt-5.4-mini", "gpt-4.1"]

    image_models: list[str] = []
    reasoning_effort: str = "auto"
    use_responses_api: bool = True          # False nei server compatibili: solo /chat/completions
    _needs_no_reasoning: set[str] = set()   # modelli che vogliono reasoning_effort='none' con i tool

    def _client(self):
        from openai import AsyncOpenAI

        return AsyncOpenAI(api_key=self.api_key)

    async def discover_models(self) -> list[dict]:
        from openai import AsyncOpenAI

        client = self._client()
        page = await client.models.list()
        self.image_models = sorted({m.id for m in page.data if m.id.startswith(("gpt-image", "dall-e"))}, reverse=True)
        skip = ("realtime", "audio", "tts", "transcribe", "whisper", "embedding", "image", "dall-e", "moderation",
                "instruct", "search", "davinci", "babbage", "codex", "computer-use", "sora")
        ids = sorted({m.id for m in page.data
                      if (m.id.startswith("gpt-") or m.id.startswith("o") and m.id[1:2].isdigit() or m.id.startswith("chatgpt-"))
                      and not any(k in m.id for k in skip)}, reverse=True)
        # metti prima i modelli "generici" (senza data) e il default
        ids.sort(key=lambda i: (i != self.default_model, any(ch.isdigit() for ch in i.split("-")[-1]) and len(i.split("-")[-1]) >= 4, i))
        return [{"id": i, "label": i} for i in ids]

    @staticmethod
    def _convert(system: str, messages: list[dict]) -> list[dict]:
        out: list[dict] = [{"role": "system", "content": system}] if system else []
        for m in messages:
            if m["role"] == "user":
                parts = []
                for b in m["content"]:
                    if b["type"] == "text" and b["text"]:
                        parts.append({"type": "text", "text": b["text"]})
                    elif b["type"] == "image":
                        parts.append({"type": "image_url", "image_url": {"url": f"data:{b['mime']};base64,{b['data']}"}})
                if parts:
                    out.append({"role": "user", "content": parts})
            elif m["role"] == "assistant":
                text = "".join(b["text"] for b in m["content"] if b["type"] == "text")
                calls = [{"id": b["id"], "type": "function",
                          "function": {"name": b["name"], "arguments": json.dumps(b["input"])}}
                         for b in m["content"] if b["type"] == "tool_use"]
                msg: dict = {"role": "assistant", "content": text or None}
                if calls:
                    msg["tool_calls"] = calls
                out.append(msg)
            elif m["role"] == "tool":
                for b in m["content"]:
                    if b["type"] == "tool_result":
                        out.append({"role": "tool", "tool_call_id": b["tool_use_id"], "content": b["content"]})
        return out

    def _sampling(self, kwargs: dict) -> None:
        """Parametri di campionamento aggiuntivi (sovrascritto dai provider locali). Di default nessuno: vale il default del modello."""

    async def stream(self, model, system, messages, tools, max_tokens=4096, system_dynamic="", cache_scope="") -> AsyncIterator[ProviderEvent]:

        chosen = model or self.default_model
        if self.use_responses_api and chosen.startswith(("gpt-6", "gpt-5.6")):
            from .openai_responses import stream_response
            async for event in stream_response(self,chosen,system,messages,tools,max_tokens,system_dynamic,cache_scope):
                yield event
            return
        client = self._client()
        # OpenAI mette in cache automaticamente il prefisso (>=1024 token) identico tra richieste: basta tenere la parte
        # stabile per prima e quella variabile in coda al system.
        full_system = system
        converted = self._convert(system, messages)
        if system_dynamic:
            user_indexes = [i for i,m in enumerate(converted) if m["role"] == "user"]
            converted.insert(user_indexes[-1] if user_indexes else len(converted), {"role":"system","content":system_dynamic})
        from .openai_responses import cache_key
        kwargs = dict(model=model or self.default_model, messages=converted, stream=True,
                      prompt_cache_key=cache_key(cache_scope, model or self.default_model, system), store=False,
                      stream_options={"include_usage": True}, max_completion_tokens=max_tokens)
        if tools:
            kwargs["tools"] = [{"type": "function", "function": {"name": t.name, "description": t.description,
                                                                  "parameters": t.input_schema}} for t in sorted(tools,key=lambda t:t.name)]
        # Modelli con ragionamento: alcuni (es. gpt-6.x) accettano i tool su /chat/completions solo con reasoning_effort
        # 'none'. Impostazione OPENAI_REASONING_EFFORT: auto (prova e si adatta) | none | low | medium | high.
        effort = (getattr(self, "reasoning_effort", "auto") or "auto").lower()
        mkey = kwargs["model"]
        if effort != "auto":
            kwargs["reasoning_effort"] = effort
        elif mkey in self._needs_no_reasoning and tools:
            kwargs["reasoning_effort"] = "none"
        self._sampling(kwargs)
        from .base import debug_sink as _sink, dbg as _dbg, dbg_reasoning as _dbg_r
        debugging = _sink.get() is not None
        if debugging:
            import time as _time
            t_start = _time.monotonic()
            sent = {k: v for k, v in kwargs.items() if k not in ("messages", "tools", "stream", "stream_options", "extra_body")}
            sent.update(kwargs.get("extra_body") or {})
            _dbg(endpoint=str(getattr(client, "base_url", "")) + "chat/completions", params=sent,
                 defaults_note="parametri non elencati = default del server/modello",
                 tools_sent=[t["function"]["name"] for t in kwargs.get("tools", [])],
                 messages_sent=len(kwargs["messages"]), system_chars=len(full_system))
            t_first = t_think0 = t_think1 = None
        pending: dict[int, dict] = {}
        usage = {}
        finish_reason = None
        try:
            response = await client.chat.completions.create(**kwargs)
        except Exception as e:
            msg = str(e)
            if tools and "reasoning_effort" in msg and "reasoning_effort" not in kwargs:
                self._needs_no_reasoning.add(mkey)
                kwargs["reasoning_effort"] = "none"
                response = await client.chat.completions.create(**kwargs)
            elif "reasoning_effort" in msg and "Unsupported" in msg and "reasoning_effort" in kwargs:
                kwargs.pop("reasoning_effort")       # modello senza ragionamento: il parametro non e' accettato
                response = await client.chat.completions.create(**kwargs)
            else:
                raise
        async for chunk in response:
            if chunk.usage:
                cached = getattr(getattr(chunk.usage, "prompt_tokens_details", None), "cached_tokens", 0) or 0
                usage = {"input_tokens": (chunk.usage.prompt_tokens or 0) - cached, "cache_read_tokens": cached,
                         "output_tokens": chunk.usage.completion_tokens or 0}
            if not chunk.choices:
                continue
            finish_reason = chunk.choices[0].finish_reason or finish_reason
            delta = chunk.choices[0].delta
            rc = getattr(delta, "reasoning_content", None) or getattr(delta, "reasoning", None)
            if debugging:
                now = _time.monotonic()
                if rc:
                    t_think0 = t_think0 or now
                    t_think1 = now
                if (delta.content or delta.tool_calls) and t_first is None:
                    t_first = now
            if rc and self.thinking:
                yield ProviderEvent("thinking_delta", text=str(rc))
            elif rc and debugging:
                _dbg_r(str(rc))
                yield ProviderEvent("hidden_thinking", text=str(rc))   # ragionamento che l'utente non vedrebbe: solo in debug
            if delta.content:
                yield ProviderEvent("text_delta", text=delta.content)
            for tc in delta.tool_calls or []:
                p = pending.setdefault(tc.index, {"id": "", "name": "", "args": "", "next": PROGRESS_STEP})
                if tc.id:
                    p["id"] = tc.id
                if tc.function:
                    if tc.function.name:
                        p["name"] += tc.function.name
                        yield ProviderEvent("status", text=writing_status(p["name"], 0))
                    if tc.function.arguments:
                        p["args"] += tc.function.arguments
                        if len(p["args"]) >= p["next"]:
                            p["next"] += PROGRESS_STEP
                            yield ProviderEvent("status", text=writing_status(p["name"], len(p["args"])))
        for p in pending.values():
            try:
                args = json.loads(p["args"]) if p["args"].strip() else {}
            except json.JSONDecodeError:
                yield ProviderEvent("text_delta", text=truncated_tool_warning(p["name"]))
                continue
            yield ProviderEvent("tool_use", tool_use={"id": p["id"], "name": p["name"], "input": args})
        if finish_reason == "length" and not pending:
            yield ProviderEvent("truncated")
        if debugging:
            t_end = _time.monotonic()
            out_tok = usage.get("output_tokens", 0)
            gen_s = t_end - (t_think0 or t_first or t_start)
            _dbg(finish_reason=finish_reason, usage=usage, total_s=round(t_end - t_start, 2),
                 first_output_s=round(t_first - t_start, 2) if t_first else None,
                 first_token_s=round((t_think0 or t_first or t_end) - t_start, 2),
                 reasoning_s=round(t_think1 - t_think0, 2) if t_think0 and t_think1 else None,
                 tokens_per_s=round(out_tok / gen_s, 1) if out_tok and gen_s > 0 else None,
                 tool_calls=[p["name"] for p in pending.values()])
        yield ProviderEvent("usage", usage=usage)
