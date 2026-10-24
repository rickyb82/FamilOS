"""Responses API streaming for current OpenAI models and measured prompt caching."""
import hashlib
import json
from .base import ProviderEvent


def cache_key(scope: str, model: str, system: str) -> str:
    identity = scope or hashlib.sha256(system.encode()).hexdigest()
    return "familos-" + hashlib.sha256((identity + ":" + model).encode()).hexdigest()[:48]


def response_input(system: str, messages: list[dict], dynamic: str, model: str = "") -> list[dict]:
    out = [{"role": "developer", "content": [{"type": "input_text", "text": system}]}] if system else []
    for message in messages:
        blocks = message.get("content") or []
        for block in blocks:
            if block.get("type") == "openai_reasoning" and (not model or block.get("model",model) == model):
                out.append(block["item"])
        if message["role"] == "tool":
            out.extend({"type":"function_call_output","call_id":b["tool_use_id"],"output":str(b.get("content",""))}
                       for b in blocks if b["type"] == "tool_result")
            continue
        content = []
        for b in blocks:
            if b["type"] == "text" and b.get("text"):
                content.append({"type":"output_text" if message["role"] == "assistant" else "input_text","text":b["text"]})
            elif b["type"] == "image":
                content.append({"type":"input_image","image_url":f"data:{b['mime']};base64,{b['data']}"})
        if content:
            out.append({"role":message["role"],"content":content})
        out.extend({"type":"function_call","call_id":b["id"],"name":b["name"],"arguments":json.dumps(b["input"])}
                   for b in blocks if b["type"] == "tool_use")
    # Time, memories and retrieved passages come after the stable prefix/history.
    if dynamic:
        out.append({"role":"developer","content":[{"type":"input_text","text":dynamic}]})
    return out


async def stream_response(provider, model, system, messages, tools, max_tokens, dynamic, scope):
    from ..config import get_settings
    from .base import dbg, dbg_reasoning
    settings = get_settings()
    inputs = response_input(system,messages,dynamic,model)
    mode = "explicit" if settings.openai_cache_mode == "stable" else "implicit"
    if mode == "explicit" and system:
        inputs[0]["content"][-1]["prompt_cache_breakpoint"] = {"mode":"explicit"}
    effort = provider.reasoning_effort
    if effort == "auto":
        effort = "none" if "luna" in model else "low"
    if effort == "none" and ("astra" in model or "6.1-sol" in model):
        effort = "low"
    kwargs = {"model":model,"input":inputs,"store":False,"stream":True,"max_output_tokens":max_tokens,
              "reasoning":{"effort":effort},"prompt_cache_key":cache_key(scope,model,system),
              "extra_body":{"prompt_cache_options":{"mode":mode,"ttl":"30m"}}}
    if tools:
        kwargs["tools"] = [{"type":"function","name":t.name,"description":t.description,
                           "parameters":t.input_schema,"strict":False} for t in sorted(tools,key=lambda t:t.name)]
    dbg(endpoint="responses",cache_mode=mode,cache_ttl="30m",store=False)
    stream = await provider._client().responses.create(**kwargs)
    calls, usage = [], {}
    async for event in stream:
        kind = event.type
        if kind == "response.output_text.delta":
            yield ProviderEvent("text_delta",text=event.delta)
        elif kind == "response.refusal.delta":
            yield ProviderEvent("text_delta",text=event.delta)
        elif kind == "response.reasoning_summary_text.delta":
            dbg_reasoning(event.delta)
            if provider.thinking:
                yield ProviderEvent("thinking_delta",text=event.delta)
        elif kind == "response.output_item.done":
            item = event.item
            if item.type == "function_call":
                try:
                    calls.append({"id":item.call_id,"name":item.name,"input":json.loads(item.arguments or "{}")})
                except json.JSONDecodeError:
                    yield ProviderEvent("error",error="Lo strumento ha ricevuto argomenti incompleti. Riprova.")
            elif item.type == "reasoning":
                raw = item.model_dump(exclude_none=True)
                yield ProviderEvent("thinking_block",tool_use={"type":"openai_reasoning","item":raw,"model":model})
        elif kind in ("response.completed","response.incomplete","response.failed"):
            response = event.response
            if response.usage:
                u = response.usage
                details = getattr(u,"input_tokens_details",None)
                cached = int(getattr(details,"cached_tokens",0) or 0)
                written = int(getattr(details,"cache_write_tokens",0) or 0)
                usage = {"input_tokens":max(0,int(u.input_tokens)-cached-written),"cache_read_tokens":cached,
                         "cache_write_tokens":written,"output_tokens":int(u.output_tokens)}
            if kind == "response.failed":
                yield ProviderEvent("error",error=getattr(getattr(response,"error",None),"message","Risposta OpenAI non riuscita"))
            elif kind == "response.incomplete" and not calls:
                yield ProviderEvent("truncated")
        elif kind == "error":
            yield ProviderEvent("error",error=getattr(event,"message","Errore OpenAI"))
    for call in calls:
        yield ProviderEvent("tool_use",tool_use=call)
    dbg(usage=usage)
    yield ProviderEvent("usage",usage=usage)
