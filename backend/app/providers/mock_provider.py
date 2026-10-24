"""Provider finto per sviluppo/test senza chiavi API.
Se il messaggio contiene "cerca:" chiama web_search, se contiene "disegna:" chiama generate_image."""
from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator

from .base import BaseProvider, ProviderEvent, text_of


class MockProvider(BaseProvider):
    id = "mock"
    label = "Mock (test)"

    async def stream(self, model, system, messages, tools, max_tokens=4096, system_dynamic="") -> AsyncIterator[ProviderEvent]:
        last = messages[-1]
        tool_names = {t.name for t in tools}
        if last["role"] == "tool":
            res = last["content"][0]["content"]
            for w in f"Ecco cosa ho trovato con il tool: {res[:300]}".split(" "):
                yield ProviderEvent("text_delta", text=w + " ")
                await asyncio.sleep(0.01)
        else:
            text = text_of(last["content"])
            low = text.lower()
            if "cerca:" in low and "web_search" in tool_names:
                yield ProviderEvent("tool_use", tool_use={"id": f"call_{uuid.uuid4().hex[:8]}", "name": "web_search",
                                                           "input": {"query": text.split(":", 1)[1].strip()}})
            elif "disegna:" in low and "generate_image" in tool_names:
                yield ProviderEvent("tool_use", tool_use={"id": f"call_{uuid.uuid4().hex[:8]}", "name": "generate_image",
                                                           "input": {"prompt": text.split(":", 1)[1].strip()}})
            else:
                for w in f"(mock) Hai scritto: {text}. Sistema: {system[:60]}...".split(" "):
                    yield ProviderEvent("text_delta", text=w + " ")
                    await asyncio.sleep(0.01)
        yield ProviderEvent("usage", usage={"input_tokens": 10, "output_tokens": 20})
