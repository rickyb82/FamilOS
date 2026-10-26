"""FamilOS providers: OpenAI, Anthropic Claude, Google Gemini, local Ollama and any OpenAI-compatible server.
Mock is development-only."""
from functools import lru_cache
from fastapi import HTTPException
from ..config import get_settings
from .base import BaseProvider
from .mock_provider import MockProvider
from .ollama_provider import OllamaProvider
from .openai_provider import OpenAIProvider

SUPPORTED_PROVIDERS = frozenset({"openai", "anthropic", "gemini", "ollama", "custom"})
CLOUD_IDS = frozenset({"openai", "anthropic", "gemini"})
PROVIDER_ORDER = ("ollama", "custom", "openai", "anthropic", "gemini")
PROVIDER_LABELS = {"openai": "OpenAI", "anthropic": "Anthropic", "gemini": "Google Gemini",
                   "ollama": "Locale (Ollama)", "custom": "Compatibile OpenAI"}


def is_local(pid: str | None) -> bool:
    """Provider in casa: Ollama, oppure il server compatibile OpenAI quando l'indirizzo e' sulla rete locale."""
    if pid == "ollama":
        return True
    if pid == "custom":
        p = _providers().get("custom")
        return bool(p and p.is_local())
    return False


def local_ids() -> set[str]:
    return {pid for pid in _providers() if is_local(pid)}


def local_provider_id() -> str | None:
    for pid in ("ollama", "custom"):
        if pid in _providers() and is_local(pid):
            return pid
    return None


@lru_cache
def _providers() -> dict[str, BaseProvider]:
    settings = get_settings()
    out: dict[str, BaseProvider] = {}
    if settings.openai_api_key:
        out["openai"] = OpenAIProvider(settings.openai_api_key, settings.openai_default_model)
        out["openai"].reasoning_effort = settings.openai_reasoning_effort
    if settings.anthropic_api_key:
        from .anthropic_provider import AnthropicProvider
        out["anthropic"] = AnthropicProvider(settings.anthropic_api_key, settings.anthropic_default_model)
    if settings.gemini_api_key:
        from .gemini_provider import GeminiProvider
        out["gemini"] = GeminiProvider(settings.gemini_api_key, settings.gemini_default_model)
    if settings.ollama_url:
        out["ollama"] = OllamaProvider(settings.ollama_url, settings.ollama_default_model)
    if settings.custom_llm_url:
        from .openai_compat_provider import OpenAICompatProvider
        out["custom"] = OpenAICompatProvider(settings.custom_llm_url, settings.custom_llm_model,
                                             settings.custom_llm_key, settings.custom_llm_label)
    if settings.enable_mock_provider:
        out["mock"] = MockProvider("", "mock-1")
    for pid, provider in out.items():
        provider.thinking = settings.thinking_mode == "all" or settings.thinking_mode == "cloud" and pid in CLOUD_IDS
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
            raise HTTPException(400, "Provider non supportato")
        if pid not in providers:
            raise HTTPException(503, f"Configura il provider {pid} in Famiglia → Provider AI")
        return providers[pid]
    for key in (*PROVIDER_ORDER, "mock"):
        if key in providers:
            return providers[key]
    raise HTTPException(503, "Configura un provider AI in Famiglia → Provider AI")


def available_providers() -> list[dict]:
    return [{"id": p.id, "label": p.label, "models": p.models(), "default_model": p.default_model}
            for p in _providers().values()]
