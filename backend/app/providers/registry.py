"""FamilOS providers: OpenAI and local Ollama. Mock is development-only."""
from functools import lru_cache
from fastapi import HTTPException
from ..config import get_settings
from .base import BaseProvider
from .mock_provider import MockProvider
from .ollama_provider import OllamaProvider
from .openai_provider import OpenAIProvider

SUPPORTED_PROVIDERS = frozenset({"openai", "ollama"})
LOCAL_IDS = {"ollama"}


def local_provider_id() -> str | None:
    return "ollama" if "ollama" in _providers() else None


@lru_cache
def _providers() -> dict[str, BaseProvider]:
    settings = get_settings()
    out: dict[str, BaseProvider] = {}
    if settings.openai_api_key:
        out["openai"] = OpenAIProvider(settings.openai_api_key, settings.openai_default_model)
        out["openai"].reasoning_effort = settings.openai_reasoning_effort
    if settings.ollama_url:
        out["ollama"] = OllamaProvider(settings.ollama_url, settings.ollama_default_model)
    if settings.enable_mock_provider:
        out["mock"] = MockProvider("", "mock-1")
    for pid, provider in out.items():
        provider.thinking = settings.thinking_mode == "all" or settings.thinking_mode == "cloud" and pid == "openai"
        provider.thinking_budget = int(settings.thinking_budget or 4096)
    return out


def reload_providers() -> None:
    _providers.cache_clear()


async def refresh_models() -> None:
    import asyncio
    await asyncio.gather(*(p.refresh_models() for p in _providers().values()), return_exceptions=True)


def get_provider(pid: str | None) -> BaseProvider:
    providers = _providers()
    if pid:
        if pid not in SUPPORTED_PROVIDERS and not (pid == "mock" and get_settings().enable_mock_provider):
            raise HTTPException(400, "Provider non supportato: scegli OpenAI oppure Ollama")
        if pid not in providers:
            raise HTTPException(503, f"Configura il provider {pid} in Famiglia → Provider AI")
        return providers[pid]
    for key in ("ollama", "openai", "mock"):
        if key in providers:
            return providers[key]
    raise HTTPException(503, "Configura OpenAI oppure Ollama in Famiglia → Provider AI")


def available_providers() -> list[dict]:
    return [{"id": p.id, "label": p.label, "models": p.models(), "default_model": p.default_model}
            for p in _providers().values()]
