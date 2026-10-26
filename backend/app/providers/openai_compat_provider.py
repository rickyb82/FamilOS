"""Provider generico per qualsiasi server con API compatibile OpenAI (/v1/chat/completions).

Copre servizi cloud (OpenRouter, Groq, Mistral, DeepSeek, Together, Fireworks...) e server in casa
(vLLM, llama.cpp server, LM Studio, LocalAI, TGI...). Chiave API facoltativa per i server locali."""
from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator

from .base import ProviderEvent
from .openai_provider import OpenAIProvider

log = logging.getLogger(__name__)

THINK_OPEN, THINK_CLOSE = "<think>", "</think>"


class OpenAICompatProvider(OpenAIProvider):
    id = "custom"
    label = "Compatibile OpenAI"
    fallback_models: list[str] = []
    use_responses_api = False

    def __init__(self, base_url: str, default_model: str, api_key: str = "", label: str = ""):
        super().__init__(api_key or "none", default_model)
        from ..services.neturl import host_url
        self.base_url = host_url(base_url).rstrip("/")
        self.tools_supported: bool | None = None   # None = non ancora verificato
        if label:
            self.label = label

    @staticmethod
    def normalize_base(url: str) -> str:
        """Accetta http://host:8000, http://host:8000/v1, https://openrouter.ai/api/v1 o l'URL completo .../chat/completions."""
        u = url.strip().rstrip("/")
        if not u.startswith(("http://", "https://")):
            u = "http://" + u
        u = re.sub(r"/(chat/completions|completions|models)$", "", u)
        if re.search(r"/v\d+(beta)?$", u) or re.search(r"/openai$", u):
            return u
        return u + "/v1"

    def is_local(self) -> bool:
        """Server in casa (rete privata o host Docker): niente costi e dati che non escono."""
        from urllib.parse import urlparse
        import ipaddress
        host = (urlparse(self.normalize_base(self.base_url)).hostname or "").lower()
        if host in ("localhost", "host.docker.internal") or host.endswith((".local", ".lan", ".home", ".internal")) or "." not in host:
            return True
        try:
            return ipaddress.ip_address(host).is_private
        except ValueError:
            return False

    def _client(self):
        from openai import AsyncOpenAI

        return AsyncOpenAI(api_key=self.api_key, base_url=self.normalize_base(self.base_url), timeout=300)

    def _sampling(self, kwargs: dict) -> None:
        # Parametri specifici di OpenAI che molti server compatibili rifiutano.
        for k in ("prompt_cache_key", "store", "reasoning_effort"):
            kwargs.pop(k, None)
        if "max_completion_tokens" in kwargs:
            kwargs["max_tokens"] = kwargs.pop("max_completion_tokens")

    async def discover_models(self) -> list[dict]:
        page = await self._client().models.list()
        ids = sorted({m.id for m in page.data if not any(k in m.id.lower() for k in ("embed", "whisper", "tts", "rerank"))})
        if not ids and self.default_model:
            ids = [self.default_model]
        ids.sort(key=lambda i: (i != self.default_model, i))
        if ids and (not self.default_model or self.default_model not in ids):
            self.default_model = ids[0]
        return [{"id": i, "label": i} for i in ids]

    async def _chat(self, model, system, messages, tools, max_tokens, system_dynamic) -> AsyncIterator[ProviderEvent]:
        async for ev in OpenAIProvider.stream(self, model, system, messages, tools, max_tokens, system_dynamic):
            yield ev

    async def stream(self, model, system, messages, tools, max_tokens=4096, system_dynamic="", cache_scope="") -> AsyncIterator[ProviderEvent]:
        """Se il server rifiuta i tool (es. vLLM senza --enable-auto-tool-choice) riprova senza e lo ricorda.
        Il ragionamento tra <think>...</think> (DeepSeek R1, Qwen...) viene separato dal testo della risposta."""
        use_tools = tools if self.tools_supported is not False else []
        try:
            got_any = False
            async for ev in self._strip_think(self._chat(model, system, messages, use_tools, max_tokens, system_dynamic)):
                got_any = True
                yield ev
            if use_tools and self.tools_supported is None:
                self.tools_supported = True
        except Exception as e:
            msg = str(e).lower()
            if use_tools and not got_any and ("tool" in msg or "function" in msg) and any(k in msg for k in ("400", "invalid", "not support", "unsupported")):
                log.warning("%s: il server non accetta i tool (%s). Da ora rispondo senza tool.", self.label, str(e)[:200])
                self.tools_supported = False
                yield ProviderEvent("status", text=f"⚠️ {self.label} non supporta gli strumenti: rispondo senza")
                async for ev in self._strip_think(self._chat(model, system, messages, [], max_tokens, system_dynamic)):
                    yield ev
            else:
                raise

    async def _strip_think(self, events: AsyncIterator[ProviderEvent]) -> AsyncIterator[ProviderEvent]:
        buf, in_think = "", False
        async for ev in events:
            if ev.type != "text_delta":
                yield ev
                continue
            buf += ev.text
            out = ""
            while buf:
                if in_think:
                    j = buf.find(THINK_CLOSE)
                    if j < 0:
                        if len(buf) > len(THINK_CLOSE):
                            if self.thinking:
                                yield ProviderEvent("thinking_delta", text=buf[:-len(THINK_CLOSE)])
                            buf = buf[-len(THINK_CLOSE):]
                        break
                    if j > 0 and self.thinking:
                        yield ProviderEvent("thinking_delta", text=buf[:j])
                    buf, in_think = buf[j + len(THINK_CLOSE):].lstrip(), False
                    continue
                i = buf.find(THINK_OPEN)
                if i >= 0:
                    out += buf[:i]
                    buf, in_think = buf[i + len(THINK_OPEN):], True
                    continue
                k = buf.rfind("<")
                if k >= 0 and len(buf) - k < len(THINK_OPEN) and THINK_OPEN.startswith(buf[k:]):
                    out, buf = out + buf[:k], buf[k:]
                else:
                    out, buf = out + buf, ""
                break
            if out:
                yield ProviderEvent("text_delta", text=out)
        if buf and not in_think:
            yield ProviderEvent("text_delta", text=buf)
