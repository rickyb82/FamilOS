import json
import httpx
from app.config import get_settings
from app.providers.ollama_provider import OllamaProvider
from app.providers.base import ToolSpec


async def test_native_ollama_stream_separates_thinking_and_supports_tools(monkeypatch):
    client = httpx.AsyncClient
    requests = []
    def respond(request):
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"template": "normal native template"})
        requests.append(request)
        chunks = [
            {"message": {"thinking": "private internal reasoning"}, "done": False},
            {"message": {"content": "FamilOS pronto"}, "done": False},
            {"message": {"tool_calls": [{"function": {"name": "read_document", "arguments": {"file_id": "one"}}}]}, "done": False},
            {"message": {}, "done": True, "prompt_eval_count": 12, "eval_count": 4},
        ]
        return httpx.Response(200, text="\n".join(json.dumps(c) for c in chunks))
    monkeypatch.setattr(get_settings(), "ollama_think", "off")
    monkeypatch.setattr("app.providers.ollama_provider.httpx.AsyncClient", lambda **kwargs: client(transport=httpx.MockTransport(respond)))
    provider = OllamaProvider("http://local-test:11434", "qwen3:8b")
    messages = [{"role": "user", "content": [{"type": "text", "text": "Ciao"}, {"type": "image", "data": "base64-image"}]}]
    events = [e async for e in provider.stream("qwen3:8b", "system", messages, [ToolSpec("read_document", "read", {})])]
    payload = json.loads(requests[0].content)
    assert requests[0].url.path == "/api/chat"
    assert payload["think"] is False
    assert payload["messages"][-1]["images"] == ["base64-image"]
    assert "".join(e.text for e in events if e.type == "text_delta") == "FamilOS pronto"
    assert next(e for e in events if e.type == "tool_use").tool_use["input"] == {"file_id": "one"}
    assert events[-1].usage == {"input_tokens": 12, "output_tokens": 4}


async def test_old_qwen_template_does_not_leak_implicit_thinking(monkeypatch):
    client = httpx.AsyncClient
    def respond(request):
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"template": "<|im_start|>assistant\n<think>\n{{ end }}"})
        chunks = [{"message": {"content": text}} for text in ["private reasoning", "</thi", "nk>", "\nFamilOS pronto"]]
        chunks.append({"done": True})
        return httpx.Response(200, text="\n".join(json.dumps(c) for c in chunks))
    monkeypatch.setattr("app.providers.ollama_provider.httpx.AsyncClient", lambda **kwargs: client(transport=httpx.MockTransport(respond)))
    events = [e async for e in OllamaProvider("http://local-test:11434", "qwen3:4b").stream("qwen3:4b", "", [], [])]
    assert "".join(e.text for e in events if e.type == "text_delta").strip() == "FamilOS pronto"
