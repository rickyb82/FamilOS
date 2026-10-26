from __future__ import annotations

import base64
import uuid
from collections.abc import AsyncIterator

from .base import BaseProvider, ProviderEvent, ToolSpec


def _clean_schema(schema: dict) -> dict:
    """Gemini accetta un sottoinsieme di JSON Schema: rimuoviamo le chiavi non supportate."""
    allowed = {"type", "description", "properties", "required", "items", "enum", "format", "nullable", "anyOf"}
    out = {}
    for k, v in schema.items():
        if k not in allowed:
            continue
        if k == "properties" and isinstance(v, dict):
            out[k] = {pk: _clean_schema(pv) for pk, pv in v.items()}
        elif k == "items" and isinstance(v, dict):
            out[k] = _clean_schema(v)
        elif k == "anyOf" and isinstance(v, list):
            out[k] = [_clean_schema(x) for x in v]
        else:
            out[k] = v
    if out.get("type") == "object" and "properties" not in out:
        out["properties"] = {}
    return out


class GeminiProvider(BaseProvider):
    id = "gemini"
    label = "Gemini (Google)"

    fallback_models = ["gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.5-flash-lite"]

    async def discover_models(self) -> list[dict]:
        from google import genai

        client = genai.Client(api_key=self.api_key)
        out = []
        async for m in await client.aio.models.list():
            name = (m.name or "").removeprefix("models/")
            actions = getattr(m, "supported_actions", None) or []
            if not name.startswith("gemini-") or (actions and "generateContent" not in actions):
                continue
            if any(k in name for k in ("embedding", "tts", "image", "audio", "live", "native")):
                continue
            out.append({"id": name, "label": getattr(m, "display_name", None) or name})
        out.sort(key=lambda m: (m["id"] != self.default_model, "flash" not in m["id"], m["id"]))
        return out

    @staticmethod
    def _convert(messages: list[dict]):
        from google.genai import types

        contents = []
        for m in messages:
            parts = []
            for b in m["content"]:
                t = b["type"]
                if t == "text" and b["text"]:
                    parts.append(types.Part.from_text(text=b["text"]))
                elif t == "image":
                    parts.append(types.Part.from_bytes(data=base64.b64decode(b["data"]), mime_type=b["mime"]))
                elif t == "tool_use":
                    parts.append(types.Part.from_function_call(name=b["name"], args=b["input"]))
                elif t == "tool_result":
                    parts.append(types.Part.from_function_response(
                        name=b.get("name", "tool"), response={"result": b["content"]}))
            if not parts:
                continue
            role = "model" if m["role"] == "assistant" else "user"
            if contents and contents[-1].role == role:
                contents[-1].parts.extend(parts)
            else:
                contents.append(types.Content(role=role, parts=parts))
        return contents

    async def stream(self, model, system, messages, tools, max_tokens=4096, system_dynamic="") -> AsyncIterator[ProviderEvent]:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=self.api_key)
        # Gemini 2.5: caching implicito automatico sul prefisso identico (parte stabile prima, variabile dopo).
        full_system = system + (("\n\n" + system_dynamic) if system_dynamic else "")
        cfg = types.GenerateContentConfig(system_instruction=full_system or None, max_output_tokens=max_tokens)
        if self.thinking:
            try:
                cfg.thinking_config = types.ThinkingConfig(include_thoughts=True, thinking_budget=int(self.thinking_budget or 4096))
            except Exception:
                pass
        if tools:
            cfg.tools = [types.Tool(function_declarations=[
                types.FunctionDeclaration(name=t.name, description=t.description,
                                          parameters=_clean_schema(t.input_schema)) for t in tools])]
            cfg.automatic_function_calling = types.AutomaticFunctionCallingConfig(disable=True)
        usage = {}
        truncated = False
        stream = await client.aio.models.generate_content_stream(
            model=model or self.default_model, contents=self._convert(messages), config=cfg)
        async for chunk in stream:
            if chunk.usage_metadata:
                cached = chunk.usage_metadata.cached_content_token_count or 0
                usage = {"input_tokens": (chunk.usage_metadata.prompt_token_count or 0) - cached, "cache_read_tokens": cached,
                         "output_tokens": chunk.usage_metadata.candidates_token_count or 0}
            for cand in chunk.candidates or []:
                if str(getattr(cand, "finish_reason", "") or "").upper().endswith("MAX_TOKENS"):
                    truncated = True
                if not cand.content or not cand.content.parts:
                    continue
                for part in cand.content.parts:
                    if part.text:
                        if getattr(part, "thought", False):
                            yield ProviderEvent("thinking_delta", text=part.text)
                        else:
                            yield ProviderEvent("text_delta", text=part.text)
                    if part.function_call:
                        fc = part.function_call
                        yield ProviderEvent("tool_use", tool_use={"id": fc.id or f"call_{uuid.uuid4().hex[:12]}",
                                                                   "name": fc.name, "input": dict(fc.args or {})})
        if truncated:
            yield ProviderEvent("truncated")
        yield ProviderEvent("usage", usage=usage)
