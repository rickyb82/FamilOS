"""Provider per modelli locali serviti da Ollama (API nativa /api/chat).
Sul Mac: Ollama nativo (Metal) e OLLAMA_URL=http://host.docker.internal:11434. Su Linux: servizio 'ollama' nel compose."""
from __future__ import annotations

from collections.abc import AsyncIterator

import httpx

from .base import ProviderEvent
from .base import BaseProvider


class OllamaProvider(BaseProvider):
    id = "ollama"
    label = "Locale (Ollama)"
    fallback_models = ["qwen3:8b", "qwen3:4b", "gemma3:12b", "llama3.1:8b"]

    def __init__(self, base_url: str, default_model: str):
        super().__init__("ollama", default_model)
        from ..services.neturl import host_url
        self.base_url = host_url(base_url).rstrip("/")
        self.param_sizes: dict[str, float] = {}   # modello -> miliardi di parametri (da /api/tags)
        self._show_cache: dict[str, tuple[float, dict]] = {}   # /api/show per il pannello debug
        self._implicit_think: dict[str, bool] = {}

    async def _template_starts_thinking(self, model: str) -> bool:
        """Older Qwen templates prefill <think> even when the API receives think=false."""
        if model not in self._implicit_think:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(self.base_url + "/api/show", json={"model": model})
                response.raise_for_status()
                template = str(response.json().get("template") or "")
            suffix = template.rsplit("<|im_start|>assistant", 1)[-1]
            self._implicit_think[model] = suffix.lstrip().startswith("<think>")
        return self._implicit_think[model]

    def is_small(self, model: str | None) -> bool:
        """Modelli piccoli (< 20B) reggono meglio un set ridotto di tool; i grandi (gpt-oss:120b, qwen3:235b...) li usano tutti."""
        size = self.param_sizes.get((model or self.default_model or "").lower())
        if size is None:
            import re
            m = re.search(r"(\d+(?:\.\d+)?)\s*b\b", (model or "").lower())
            size = float(m.group(1)) if m else None
        return size is None or size < 20

    def _sampling(self, kwargs: dict) -> None:
        """Temperatura / top_p / seed impostati dall'amministratore (vuoto = quelli del Modelfile del modello)."""
        from ..config import get_settings
        s = get_settings()
        for key, attr, cast in (("temperature", "ollama_temperature", float), ("top_p", "ollama_top_p", float), ("seed", "ollama_seed", int)):
            raw = str(getattr(s, attr, "") or "").strip().replace(",", ".")
            if raw:
                try:
                    kwargs[key] = cast(float(raw)) if cast is int else cast(raw)
                except ValueError:
                    pass

    async def model_debug_info(self, model: str) -> dict:
        """Per il pannello debug: i parametri del Modelfile (temperature, top_k, num_ctx...), il contesto massimo del modello,
        le capacita' (tools, thinking) e, se caricato, il contesto effettivo e la VRAM occupata (da /api/ps)."""
        import time
        hit = self._show_cache.get(model)
        if hit and time.time() - hit[0] < 600:
            info = dict(hit[1])
        else:
            info = {}
            try:
                async with httpx.AsyncClient(timeout=8) as c:
                    r = await c.post(self.base_url + "/api/show", json={"model": model})
                    if r.status_code == 200:
                        d = r.json()
                        params = {}
                        for line in str(d.get("parameters") or "").splitlines():
                            parts = line.split(None, 1)
                            if len(parts) == 2:
                                params.setdefault(parts[0], []).append(parts[1].strip().strip('"'))
                        info["modelfile_params"] = {k: (v[0] if len(v) == 1 else v) for k, v in params.items()}
                        mi = d.get("model_info") or {}
                        ctx = next((v for k, v in mi.items() if k.endswith(".context_length")), None)
                        info["model_context_length"] = ctx
                        info["capabilities"] = d.get("capabilities") or []
                        det = d.get("details") or {}
                        info["quantization"] = det.get("quantization_level")
                        info["parameter_size"] = det.get("parameter_size")
                        tpl = str(d.get("template") or "")
                        info["template_has_think"] = ("think" in tpl.lower()) or None
            except Exception as e:
                info["show_error"] = str(e)[:200]
            self._show_cache[model] = (time.time(), info)
            info = dict(info)
        try:
            async with httpx.AsyncClient(timeout=5) as c:
                r = await c.get(self.base_url + "/api/ps")
                for m in (r.json().get("models") or []) if r.status_code == 200 else []:
                    if (m.get("name") or m.get("model")) == model:
                        info["loaded_context_length"] = m.get("context_length")
                        info["vram_gb"] = round((m.get("size_vram") or 0) / 1e9, 1)
                        info["size_gb"] = round((m.get("size") or 0) / 1e9, 1)
        except Exception:
            pass
        return info

    async def discover_models(self) -> list[dict]:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(self.base_url + "/api/tags")
            r.raise_for_status()
            data = r.json()
        out = []
        for m in data.get("models", []):
            name = m.get("name") or m.get("model")
            if not name or "embed" in name:
                continue
            size_gb = (m.get("size") or 0) / 1e9
            fam = (m.get("details") or {}).get("parameter_size", "")
            try:
                self.param_sizes[name.lower()] = float(str(fam).upper().rstrip("B").replace(",", "."))
            except (ValueError, AttributeError):
                pass
            out.append({"id": name, "label": f"{name} ({fam or f'{size_gb:.1f} GB'})"})
        out.sort(key=lambda x: (x["id"] != self.default_model, x["id"]))
        return out

    @staticmethod
    def _native_messages(system: str, messages: list[dict]) -> list[dict]:
        out = [{"role": "system", "content": system}] if system else []
        for message in messages:
            blocks = message.get("content", [])
            if message["role"] == "tool":
                out.extend({"role": "tool", "tool_name": b.get("name", ""), "content": str(b.get("content", ""))}
                           for b in blocks if b["type"] == "tool_result")
                continue
            row = {"role": message["role"], "content": "".join(b.get("text", "") for b in blocks if b["type"] == "text")}
            images = [b["data"] for b in blocks if b["type"] == "image" and b.get("data")]
            if images:
                row["images"] = images
            calls = [{"function": {"name": b["name"], "arguments": b.get("input", {})}}
                     for b in blocks if b["type"] == "tool_use"]
            if calls:
                row["tool_calls"] = calls
            out.append(row)
        return out

    async def stream(self, model, system, messages, tools, max_tokens=4096, system_dynamic="") -> AsyncIterator[ProviderEvent]:
        import json
        import uuid
        from ..config import get_settings
        from .base import dbg_reasoning
        settings = get_settings()
        full_system = system + ("\n\n" + system_dynamic if system_dynamic else "")
        options = {"num_predict": max_tokens}
        self._sampling(options)
        payload = {"model": model or self.default_model, "messages": self._native_messages(full_system, messages),
                   "stream": True, "options": options}
        think = str(settings.ollama_think or "auto").lower()
        if think in ("on", "off"):
            payload["think"] = think == "on"
        elif not self.thinking:
            payload["think"] = False
        if tools:
            payload["tools"] = [{"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.input_schema}} for t in tools]
        pending = {}
        last = {}
        implicit_think = await self._template_starts_thinking(payload["model"])
        think_buffer = ""
        async with httpx.AsyncClient(timeout=300) as client:
            async with client.stream("POST", self.base_url + "/api/chat", json=payload) as response:
                if response.is_error:
                    # Ollama returns the actionable cause in JSON; httpx's generic 400 hides it.
                    raw = bytearray()
                    async for part in response.aiter_bytes():
                        raw.extend(part[:8192-len(raw)])
                        if len(raw) >= 8192:
                            break
                    try:
                        detail = json.loads(raw).get("error", "")
                    except (ValueError, AttributeError):
                        detail = ""
                    if not isinstance(detail, str):
                        detail = ""
                    detail = " ".join(detail.split())[:1000]
                    raise RuntimeError(f"Ollama HTTP {response.status_code}: " + (detail or "richiesta rifiutata dal server"))
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    chunk = json.loads(line)
                    if chunk.get("error"):
                        yield ProviderEvent("error", error=chunk["error"])
                        return
                    message = chunk.get("message") or {}
                    reasoning = message.get("thinking") or ""
                    if reasoning:
                        implicit_think = False  # The server parsed the reasoning itself.
                        dbg_reasoning(reasoning)
                        if self.thinking:
                            yield ProviderEvent("thinking_delta", text=reasoning)
                    content = message.get("content") or ""
                    if content and implicit_think:
                        think_buffer += content
                        if "</think>" in think_buffer:
                            hidden, content = think_buffer.split("</think>", 1)
                            dbg_reasoning(hidden)
                            if self.thinking and hidden:
                                yield ProviderEvent("thinking_delta", text=hidden)
                            implicit_think, think_buffer = False, ""
                            content = content.lstrip()
                        else:
                            # Keep a possible split closing tag across NDJSON chunks.
                            hidden, think_buffer = think_buffer[:-8], think_buffer[-8:]
                            dbg_reasoning(hidden)
                            if self.thinking and hidden:
                                yield ProviderEvent("thinking_delta", text=hidden)
                            content = ""
                    if content:
                        yield ProviderEvent("text_delta", text=content)
                    for index, call in enumerate(message.get("tool_calls") or []):
                        function = call.get("function") or {}
                        pending[function.get("index", index)] = function
                    if chunk.get("done"):
                        last = chunk
        for call in pending.values():
            arguments = call.get("arguments") or {}
            if isinstance(arguments, str):
                arguments = json.loads(arguments)
            yield ProviderEvent("tool_use", tool_use={"id": uuid.uuid4().hex, "name": call["name"], "input": arguments})
        if implicit_think and not pending:
            yield ProviderEvent("error", error="Il modello ha prodotto solo ragionamento. Aumenta il limite di risposta oppure aggiorna il modello Ollama.")
        if last.get("done_reason") == "length" and not pending:
            yield ProviderEvent("truncated")
        yield ProviderEvent("usage", usage={"input_tokens": last.get("prompt_eval_count", 0), "output_tokens": last.get("eval_count", 0)})
