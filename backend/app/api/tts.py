from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..policy.permissions import log_usage
from ..security.sessions import AuthContext, RateLimiter, require_auth
from ..services import tts

router = APIRouter(prefix="/tts", tags=["tts"])
tts_limiter = RateLimiter(max_hits=120, window_s=300)   # per utente: un gioco puo' chiedere molte frasi brevi


class SpeakRequest(BaseModel):
    text: str = Field(min_length=1, max_length=tts.MAX_CHARS)
    voice: str | None = None
    style: str | None = None


@router.get("/status")
async def tts_status(ctx: AuthContext = Depends(require_auth)):
    from ..policy.education import cloud_allowed
    prov = tts.provider() if cloud_allowed(ctx.user) else ""
    return {"enabled": bool(prov), "provider": prov,
            "voices": tts.OPENAI_VOICES if prov == "openai" else []}


@router.post("")
async def speak(body: SpeakRequest, request: Request, ctx: AuthContext = Depends(require_auth),
                db: AsyncSession = Depends(get_db)):
    """Restituisce l'audio (mp3 o wav) del testo con una voce naturale. Cache per frase: ripetere e' gratis."""
    from ..policy.education import cloud_allowed
    if not cloud_allowed(ctx.user):
        raise HTTPException(403,"La voce cloud non è abilitata per il tuo profilo.")
    if not tts.enabled():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Sintesi vocale non configurata")
    tts_limiter.check("tts:" + ctx.user.id)
    try:
        audio, mime = await tts.synthesize(body.text, body.voice, body.style)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Sintesi vocale fallita ({tts.provider()}): {e}")
    await log_usage(db, ctx.user, "tts", tts.provider(), detail=f"{len(body.text)} caratteri")
    return Response(audio, media_type=mime, headers={"Cache-Control": "private, max-age=86400"})
