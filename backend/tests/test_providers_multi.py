"""Provider aggiuntivi: Anthropic, Gemini e server compatibili OpenAI (nessuna chiamata di rete)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.models import AgeGroup, Role
from app.providers.base import ProviderEvent
from app.providers.openai_compat_provider import OpenAICompatProvider


@pytest.mark.parametrize("url,base", [
    ("http://192.168.1.20:8000", "http://192.168.1.20:8000/v1"),
    ("http://192.168.1.20:8000/v1/", "http://192.168.1.20:8000/v1"),
    ("http://server:8000/v1/chat/completions", "http://server:8000/v1"),
    ("https://openrouter.ai/api/v1", "https://openrouter.ai/api/v1"),
    ("https://api.groq.com/openai/v1", "https://api.groq.com/openai/v1"),
    ("https://generativelanguage.googleapis.com/v1beta/openai", "https://generativelanguage.googleapis.com/v1beta/openai"),
    ("lmstudio.local:1234", "http://lmstudio.local:1234/v1"),
])
def test_compatible_base_url_is_normalized(url, base):
    assert OpenAICompatProvider.normalize_base(url) == base


@pytest.mark.parametrize("url,local", [
    ("http://192.168.1.20:8000", True), ("http://10.0.0.5:8080/v1", True), ("http://host.docker.internal:1234", True),
    ("http://vllm:8000", True), ("http://nas.local:8000", True),
    ("https://openrouter.ai/api/v1", False), ("https://api.mistral.ai/v1", False),
])
def test_compatible_server_locality(url, local):
    assert OpenAICompatProvider(url, "").is_local() is local


def test_compatible_server_drops_openai_only_parameters():
    provider = OpenAICompatProvider("https://openrouter.ai/api/v1", "m", "key")
    kwargs = {"model": "m", "prompt_cache_key": "x", "store": False, "reasoning_effort": "none", "max_completion_tokens": 99}
    provider._sampling(kwargs)
    assert kwargs == {"model": "m", "max_tokens": 99}
    assert provider.use_responses_api is False
    assert provider.api_key == "key" and OpenAICompatProvider("http://x:1", "m").api_key == "none"


async def _collect(provider, chunks):
    async def events():
        for c in chunks:
            yield ProviderEvent("text_delta", text=c)
        yield ProviderEvent("usage", usage={})
    return [e async for e in provider._strip_think(events())]


async def test_think_tags_are_separated_from_the_answer():
    provider = OpenAICompatProvider("http://x:1", "m")
    provider.thinking = True
    out = await _collect(provider, ["<thi", "nk>ragiono", " ancora</th", "ink>Ciao", " mondo <b>ok</b>"])
    text = "".join(e.text for e in out if e.type == "text_delta")
    thought = "".join(e.text for e in out if e.type == "thinking_delta")
    assert text == "Ciao mondo <b>ok</b>"
    assert "ragiono" in thought and "<think>" not in text
    provider.thinking = False
    out = await _collect(provider, ["<think>segreto</think>Risposta"])
    assert [e.text for e in out if e.type == "thinking_delta"] == []
    assert "".join(e.text for e in out if e.type == "text_delta") == "Risposta"


async def test_server_without_tools_is_retried_without_them(monkeypatch):
    provider = OpenAICompatProvider("http://x:1", "m")
    calls = []

    async def fake_chat(model, system, messages, tools, max_tokens, system_dynamic):
        calls.append(len(tools))
        if tools:
            raise RuntimeError("Error code: 400 - tool choice is not supported")
        yield ProviderEvent("text_delta", text="ok")

    monkeypatch.setattr(provider, "_chat", fake_chat)
    events = [e async for e in provider.stream("m", "", [], [SimpleNamespace(name="t")])]
    assert calls == [1, 0] and provider.tools_supported is False
    assert any(e.type == "status" for e in events) and events[-1].text == "ok"


def _child(policy: dict | None = None):
    return SimpleNamespace(role=Role.child, age_group=AgeGroup.kid, policy=policy or {}, id="c", family_id="f", apps=None)


def test_parent_choice_of_provider_is_kept_for_children(monkeypatch):
    from app.policy import education
    monkeypatch.setattr(education, "cloud_allowed", lambda user: True)
    policy = education.apply_to_policy(_child({"provider": "anthropic", "model": "claude-sonnet-5-5"}),
                                       {"allowed_providers": ["openai", "anthropic", "gemini", "ollama", "custom"]})
    assert (policy["provider"], policy["model"]) == ("anthropic", "claude-sonnet-5-5")


def test_children_without_cloud_keep_only_local_providers(monkeypatch):
    from app.policy import education
    from app.providers import registry
    monkeypatch.setattr(education, "cloud_allowed", lambda user: False)
    monkeypatch.setattr(registry, "is_local", lambda pid: pid in ("ollama", "custom"))
    policy = education.apply_to_policy(_child({"provider": "gemini"}),
                                       {"allowed_providers": ["openai", "anthropic", "gemini", "ollama", "custom"], "provider": "gemini"})
    assert policy["allowed_providers"] == ["ollama", "custom"]
    assert policy["provider"] == ""
