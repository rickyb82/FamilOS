"""Interfaccia comune ai provider LLM.

Formato interno dei messaggi (indipendente dal provider):
  {"role": "user"|"assistant"|"tool", "content": [blocchi]}
Blocchi:
  {"type": "text", "text": str}
  {"type": "image", "mime": str, "data": base64}
  {"type": "tool_use", "id": str, "name": str, "input": dict}
  {"type": "tool_result", "tool_use_id": str, "name": str, "content": str, "is_error": bool}
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass
class ProviderEvent:
    type: str  # text_delta | tool_use | usage | error | done
    text: str = ""
    tool_use: dict | None = None
    usage: dict = field(default_factory=dict)
    error: str = ""


# Debug LLM: durante un giro dell'agente il chat_service mette qui un dict; i provider vi annotano i parametri effettivi della
# richiesta, i tempi, il motivo di fine e il ragionamento che l'utente non vedrebbe. None = debug spento (nessun costo).
debug_sink: ContextVar[dict | None] = ContextVar("llm_debug_sink", default=None)


def dbg(**kw) -> None:
    d = debug_sink.get()
    if d is not None:
        d.update(kw)


def dbg_reasoning(text: str) -> None:
    """Accumula il ragionamento nascosto (fino a 40k caratteri) per il pannello debug."""
    d = debug_sink.get()
    if d is not None and text:
        cur = d.get("reasoning", "")
        if len(cur) < 40000:
            d["reasoning"] = cur + text[: 40000 - len(cur)]
        d["reasoning_chars"] = d.get("reasoning_chars", 0) + len(text)


class BaseProvider:
    id: str = "base"
    label: str = "Base"
    supports_images: bool = True
    thinking: bool = False            # mostra il ragionamento (extended thinking / <think>) come eventi thinking_delta
    thinking_budget: int = 4096

    fallback_models: list[str] = []

    def __init__(self, api_key: str, default_model: str):
        self.api_key = api_key
        self.default_model = default_model
        self._models: list[dict] | None = None   # scoperti via API (refresh_models)

    def models(self) -> list[dict]:
        if self._models:
            return self._models
        ids = [self.default_model] + [m for m in self.fallback_models if m != self.default_model]
        return [{"id": m, "label": m} for m in ids if m]

    async def discover_models(self) -> list[dict]:
        """Interroga l'API del provider per la lista dei modelli chat disponibili."""
        return []

    async def refresh_models(self) -> None:
        try:
            found = await self.discover_models()
        except Exception:
            found = []
        if found:
            self._models = found
            ids = {m["id"] for m in found}
            if self.default_model not in ids:
                # il default configurato non esiste (piu'): usa il primo consigliato
                self.default_model = found[0]["id"]

    async def stream(self, model: str, system: str, messages: list[dict], tools: list[ToolSpec],
                     max_tokens: int = 4096, system_dynamic: str = "") -> AsyncIterator[ProviderEvent]:
        """`system` e' la parte stabile del prompt (cacheabile), `system_dynamic` quella che cambia a ogni turno."""
        raise NotImplementedError
        yield  # pragma: no cover


def text_of(blocks: list[dict]) -> str:
    return "".join(b.get("text", "") for b in blocks if b.get("type") == "text")


# ---- progresso e troncamenti durante lo streaming dei tool ----
WRITING_LABELS = {
    "create_artifact": "l'app", "update_artifact": "le modifiche all'app", "run_code": "il codice da eseguire",
    "generate_image": "la richiesta di immagine", "generate_video": "la richiesta di video",
}
PROGRESS_STEP = 2500   # caratteri di input tool tra un aggiornamento di stato e l'altro


def writing_status(tool_name: str, nchars: int) -> str:
    what = WRITING_LABELS.get(tool_name, f"i dati per {tool_name}")
    kb = nchars / 1024
    size = f"{kb:.0f} KB" if kb >= 1 else f"{nchars} caratteri"
    return f"✍️ Sto scrivendo {what} ({size})…"


def truncated_tool_warning(tool_name: str) -> str:
    what = WRITING_LABELS.get(tool_name, tool_name)
    return (f"\n\n⚠️ Ho superato la lunghezza massima della risposta mentre scrivevo {what}, quindi non e' stato "
            "completato. Chiedimi una versione piu' semplice o di procedere a pezzi (per esempio: \"fai prima la "
            "struttura, poi aggiungiamo un mini-gioco alla volta\").")
