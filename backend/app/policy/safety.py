"""Filtro contenuti per i bambini: blocklist locale + (opzionale) OpenAI Moderation."""
from __future__ import annotations

import logging
import re

from ..config import get_settings

log = logging.getLogger(__name__)
settings = get_settings()

# Lista minima, estendibile dal genitore via policy["blocked_words"]
BASE_BLOCKLIST = [
    r"\bporn\w*", r"\bsess\w* esplicit\w*", r"\bcome (si )?(fabbric|costru)\w* (una |un )?(bomba|arma)",
    r"\bsuicid\w*", r"\bautolesion\w*", r"\bdroga\b", r"\bcocain\w*", r"\beroin\w*",
]


class SafetyResult:
    def __init__(self, flagged: bool, reason: str = "", block: bool = False):
        self.flagged = flagged
        self.reason = reason
        self.block = block


async def check_text(text: str, policy: dict) -> SafetyResult:
    if not policy.get("moderation"):
        return SafetyResult(False)
    patterns = BASE_BLOCKLIST + [re.escape(w) for w in policy.get("blocked_words", [])]
    for p in patterns:
        if re.search(p, text, flags=re.IGNORECASE):
            return SafetyResult(True, f"parola/espressione bloccata: {p}", block=True)

    if settings.child_moderation and settings.openai_api_key and policy.get("cloud_allowed",True):
        try:
            from openai import AsyncOpenAI

            client = AsyncOpenAI(api_key=settings.openai_api_key)
            r = await client.moderations.create(model="omni-moderation-latest", input=text[:4000])
            res = r.results[0]
            if res.flagged:
                cats = [k for k, v in res.categories.model_dump().items() if v]
                # blocchiamo le categorie più gravi, segnaliamo le altre
                severe = {"sexual", "sexual/minors", "self-harm", "self-harm/intent", "self-harm/instructions",
                          "violence/graphic", "illicit/violent"}
                return SafetyResult(True, "moderazione: " + ", ".join(cats), block=bool(severe & set(cats)))
        except Exception as e:  # la moderazione non deve bloccare il servizio
            log.warning("Moderazione non disponibile: %s", e)
    return SafetyResult(False)


CHILD_BLOCK_MESSAGE = (
    "Mi dispiace, di questo argomento non posso parlare con te. Se hai dubbi o curiosità, "
    "parlane con mamma o papà: loro sapranno aiutarti. 💛"
)
