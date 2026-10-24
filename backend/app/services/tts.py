"""Sintesi vocale (voci naturali) con OpenAI, con cache su disco per frase+voce+stile."""
from __future__ import annotations

import asyncio
import hashlib
import struct
from pathlib import Path

from ..config import get_settings

settings = get_settings()

MAX_CHARS = 2000
OPENAI_VOICES = ["alloy", "ash", "ballad", "coral", "echo", "fable", "nova", "onyx", "sage", "shimmer", "verse"]
DEFAULT_STYLE = ("Parla in italiano con voce calda, chiara e naturale, ritmo tranquillo, tono gentile e allegro, "
                 "come chi legge a un bambino.")

_locks: dict[str, asyncio.Lock] = {}


def provider() -> str:
    return "openai" if settings.tts_provider in ("auto", "openai") and settings.openai_api_key else ""


def enabled() -> bool:
    return bool(provider())


def _cache_dir() -> Path:
    p = settings.data_dir / "tts"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _wav(pcm: bytes, rate: int = 24000, channels: int = 1, width: int = 2) -> bytes:
    header = b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVE" + b"fmt " + struct.pack(
        "<IHHIIHH", 16, 1, channels, rate, rate * channels * width, channels * width, width * 8) + b"data" + struct.pack("<I", len(pcm))
    return header + pcm


async def synthesize(text: str, voice: str | None = None, style: str | None = None) -> tuple[bytes, str]:
    """Restituisce (audio, mime). Solleva RuntimeError con messaggio leggibile se non disponibile."""
    text = " ".join(text.split())[:MAX_CHARS]
    if not text:
        raise RuntimeError("Testo vuoto")
    prov = provider()
    if not prov:
        raise RuntimeError("Sintesi vocale non configurata: serve una chiave OpenAI")
    model = settings.tts_model_openai or "gpt-4o-mini-tts"
    voice = (voice or settings.tts_voice_openai or "nova").lower()
    if voice not in OPENAI_VOICES:
        voice = "nova"
    mime, ext = "audio/mpeg", "mp3"
    style = (style or settings.tts_style or DEFAULT_STYLE).strip()[:500]

    key = hashlib.sha256(f"{prov}|{model}|{voice}|{style}|{text}".encode()).hexdigest()
    path = _cache_dir() / f"{key}.{ext}"
    if path.exists():
        return path.read_bytes(), mime
    lock = _locks.setdefault(key, asyncio.Lock())
    async with lock:
        if path.exists():
            return path.read_bytes(), mime
        audio = await _openai(text, model, voice, style)
        path.write_bytes(audio)
        return audio, mime


async def _openai(text: str, model: str, voice: str, style: str) -> bytes:
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=settings.openai_api_key)
    kwargs = dict(model=model, voice=voice, input=text, response_format="mp3")
    if model.startswith("gpt-"):          # tts-1 / tts-1-hd non accettano 'instructions'
        kwargs["instructions"] = style
    r = await client.audio.speech.create(**kwargs)
    return r.content if hasattr(r, "content") else await r.aread()
