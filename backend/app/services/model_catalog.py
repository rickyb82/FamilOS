"""Quali modelli puo' scegliere chi: l'amministratore decide dal pannello (impostazione `user_models_json`) l'elenco dei
modelli offerti agli utenti e quello di default per le nuove chat (`user_default_model`); gli amministratori vedono tutto,
ma possono bloccare per se stessi un modello (`locked_model` nella propria policy) cosi' da non cambiarlo per sbaglio.

Formato: "provider/modello" (es. "openai/gpt-4.1", "ollama/qwen3:8b"). Elenco vuoto = ogni provider
consentito offre solo il suo modello di default (comportamento prudente, non "tutto").

Per utente: l'amministratore puo' dare a una persona un elenco suo (`user_models` nella policy dell'utente, con il suo
`user_default_model`); senza, la persona segue l'elenco predefinito (e le sue modifiche future).
"""
from __future__ import annotations

import json
import logging

from ..config import get_settings
from ..models import Role, User
from ..providers.registry import available_providers

log = logging.getLogger(__name__)
settings = get_settings()


def key(provider: str, model: str) -> str:
    return f"{provider}/{model}"


def split(k: str) -> tuple[str, str]:
    p, _, m = (k or "").partition("/")
    return p, m


def user_models() -> list[str]:
    """Le chiavi provider/modello abilitate per gli utenti (dal pannello)."""
    raw = (getattr(settings, "user_models_json", "") or "").strip()
    if not raw:
        return []
    try:
        v = json.loads(raw)
        return [str(x) for x in v if isinstance(x, str) and "/" in x] if isinstance(v, list) else []
    except Exception as e:
        log.warning("user_models_json non valido: %s", e)
        return []


def user_default() -> str:
    return (getattr(settings, "user_default_model", "") or "").strip()


MAX_USER_MODELS = 200


def personal_models(user: User) -> list[str] | None:
    """L'elenco personale dell'utente (None = segue il predefinito)."""
    v = (user.policy or {}).get("user_models")
    if not isinstance(v, list):
        return None
    return [str(x) for x in v[:MAX_USER_MODELS] if isinstance(x, str) and "/" in x]


def user_models_for(user: User) -> list[str]:
    own = personal_models(user)
    return user_models() if not own else own   # personale vuoto (non dovrebbe esistere) = predefinito, mai "tutto"


def user_default_for(user: User) -> str:
    own = personal_models(user)
    if not own:
        return user_default()
    d = str((user.policy or {}).get("user_default_model") or "")
    return d if d in own else (own[0] if own else "")


def admin_lock(user: User) -> str:
    """Il modello che l'amministratore si e' bloccato da solo ("" = libero)."""
    if user.role != Role.admin:
        return ""
    return str((user.policy or {}).get("locked_model") or "")


def catalog(user: User, policy: dict) -> list[dict]:
    """I provider (con i loro modelli) che questo utente puo' scegliere nel menu della chat."""
    provs = [p for p in available_providers() if p["id"] in policy.get("allowed_providers", []) or p["id"] == "mock"]
    if user.role == Role.admin:
        lock = admin_lock(user)
        if lock:
            lp, lm = split(lock)
            only = [{**p, "models": [m for m in p["models"] if m["id"] == lm] or p["models"][:1], "default_model": lm}
                    for p in provs if p["id"] == lp]
            if only:
                return only
        preferred = (user.prefs or {}).get("default_chat_model", "")
        dp, dm = split(preferred)
        if any(p["id"] == dp and any(m["id"] == dm for m in p["models"]) for p in provs):
            provs = [{**p, "default_model": dm} if p["id"] == dp else p for p in provs]
            provs.sort(key=lambda p: p["id"] != dp)
        return provs
    if user.role == Role.child:
        return provs   # i bambini hanno gia' provider+modello fissati dal genitore (policy)
    allowed = user_models_for(user)   # un elenco personale non e' mai vuoto (lo impedisce set_personal)
    out = []
    for p in provs:
        if allowed:
            ids = {m for pp, m in (split(k) for k in allowed) if pp == p["id"]}
            models = [m for m in p["models"] if m["id"] in ids]
            if not models and p["id"] == "mock":
                models = p["models"]
        else:
            models = [m for m in p["models"] if m["id"] == p["default_model"]] or p["models"][:1]
        if models:
            out.append({**p, "models": models, "default_model": models[0]["id"] if p["default_model"] not in {m["id"] for m in models} else p["default_model"]})
    # il default scelto dall'admin va per primo (il client prende il primo provider per le nuove chat)
    dp, dm = split(user_default_for(user))
    if dp:
        out.sort(key=lambda p: p["id"] != dp)
        for p in out:
            if p["id"] == dp and any(m["id"] == dm for m in p["models"]):
                p["default_model"] = dm
    return out


def coerce(user: User, policy: dict, provider_id: str | None, model: str | None) -> tuple[str | None, str | None]:
    """Riporta una scelta (provider, modello) dentro il catalogo dell'utente: gli utenti non escono dall'elenco dell'admin,
    l'admin bloccato resta sul suo modello. Ritorna (None, None) se il catalogo e' vuoto (poi decide il default)."""
    if user.role == Role.child:
        return provider_id, model
    cat = catalog(user, policy)
    if user.role == Role.admin:
        lock = admin_lock(user)
        if lock:
            lp, lm = split(lock)
            return lp, lm
        if not provider_id and not model and cat:
            return cat[0]["id"], cat[0]["default_model"]
        return provider_id, model
    if not cat:
        return provider_id, model
    for p in cat:
        if p["id"] == provider_id:
            ids = {m["id"] for m in p["models"]}
            return provider_id, (model if model in ids else p["default_model"])
    # provider non consentito: il default dell'admin, altrimenti il primo del catalogo
    dp, dm = split(user_default_for(user))
    for p in cat:
        if p["id"] == dp and any(m["id"] == dm for m in p["models"]):
            return dp, dm
    return cat[0]["id"], cat[0]["default_model"]
