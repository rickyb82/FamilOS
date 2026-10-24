"""Impostazioni runtime modificabili dal pannello admin.

I valori salvati in `app_settings` (chiavi API cifrate con SECRET_KEY, modelli di default, provider di ricerca…)
sovrascrivono quelli del file .env. All'avvio e dopo ogni modifica vengono applicati all'oggetto Settings
condiviso, e il registro dei provider viene ricaricato a caldo.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import time

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..models import AppSetting

log = logging.getLogger(__name__)
settings = get_settings()

# chiave -> (etichetta, è segreto)
MANAGED_KEYS: dict[str, tuple[str, bool]] = {'passkeys': ('Passkey abilitate (0 = solo app authenticator per il 2FA)', False),
 'mfa_optional': ("2FA facoltativo: l'utente sceglie al primo accesso (0 = obbligatorio)", False),
 'user_models_json': ('Modelli offerti agli utenti (JSON di provider/modello)', False),
 'user_default_model': ('Modello di default per gli utenti (provider/modello)', False),
 'default_quota_gb': ('Quota spazio per utente in GB (0 = illimitata)', False),
 'openai_api_key': ('Chiave API OpenAI', True),
 'openai_default_model': ('Modello di default OpenAI', False),
 'openai_cache_mode': ('Cache prompt OpenAI (auto/stable)', False),
 'openai_reasoning_effort': ('Sforzo di ragionamento OpenAI (auto/none/low/medium/high)', False),
 'ollama_url': ('Indirizzo Ollama (modello locale)', False),
 'ollama_default_model': ('Modello locale di default', False),
 'web_search_provider': ('Motore di ricerca web', False),
 'searxng_url': ('Indirizzo SearXNG (ricerca web in casa)', False),
 'web_search_summarize': ('Ricerca web: riassunto pagine con modello locale (auto/none)', False),
 'image_provider': ('Provider immagini (OpenAI)', False),
 'image_model': ('Modello immagini OpenAI', False),
 'tts_provider': ('Provider voce (auto/openai/none)', False),
 'tts_model_openai': ('Modello voce OpenAI', False),
 'tts_voice_openai': ('Voce OpenAI', False),
 'tts_style': ('Stile della voce (istruzioni)', False),
 'compaction_enabled': ('Compattazione chat lunghe (riassunto + archivio RAG)', False),
 'auto_continue': ('Continuazione automatica delle risposte troncate', False),
 'thinking_mode': ('Ragionamento visibile (off/cloud/all)', False),
 'debug_llm': ('Debug LLM (off/admins/all)', False),
 'ollama_temperature': ('Ollama: temperatura', False),
 'ollama_top_p': ('Ollama: top_p', False),
 'ollama_seed': ('Ollama: seed', False),
 'ollama_think': ('Ollama: ragionamento (auto/on/off)', False),
 'memory_enabled': ('Memoria di famiglia attiva', False),
 'memory_auto_approve': ('Memoria: approvazione automatica voci sicure', False),
 'memory_model': ("Memoria: modello per l'estrazione", False),
 'embedding_provider': ('Provider embedding (RAG)', False),
 'embedding_model': ('Modello embedding', False),
 'smtp_host': ('Server SMTP', False),
 'smtp_port': ('Porta SMTP', False),
 'smtp_user': ('Utente SMTP', False),
 'smtp_password': ('Password SMTP', True),
 'smtp_from': ('Mittente', False),
 'smtp_from_name': ('Nome mittente', False),
 'smtp_security': ('Sicurezza SMTP (starttls/ssl/none)', False),
 'email_mfa_fallback': ('Codice email al posto della passkey', False),
 'mail_transport': ('Canale email (auto/gmail_oauth/smtp)', False),
 'gmail_oauth_refresh_token': ('Token Gmail OAuth', True),
 'gmail_oauth_email': ('Account Gmail collegato', False),
 'google_client_id': ('Google OAuth Client ID', False),
 'google_client_secret': ('Google OAuth Client secret', True),
 'pricing_json': ('Listino prezzi (JSON)', False),
 'budget_openai': ('Budget mensile OpenAI (USD)', False),
 'openai_admin_key': ('Chiave Admin OpenAI (spesa reale)', True)}

_env_defaults: dict[str, str] = {k: getattr(settings, k) for k in MANAGED_KEYS}


def _fernet() -> Fernet:
    digest = hashlib.sha256(("familai:" + settings.secret_key).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        log.error("Impossibile decifrare un'impostazione: SECRET_KEY cambiata? Il valore verrà ignorato.")
        return ""


def mask(value: str) -> str:
    if not value:
        return ""
    return value[:4] + "…" + value[-4:] if len(value) > 10 else "•" * len(value)


async def load_overrides(db: AsyncSession) -> dict[str, str]:
    rows = (await db.execute(select(AppSetting))).scalars().all()
    out = {}
    for r in rows:
        if r.key in MANAGED_KEYS:
            out[r.key] = decrypt(r.value) if r.secret else r.value
    return out


async def apply(db: AsyncSession) -> None:
    """Applica env + override DB all'oggetto Settings e ricarica i provider."""
    overrides = await load_overrides(db)
    for k in MANAGED_KEYS:
        val = overrides.get(k) or _env_defaults[k]
        # campi numerici del Settings: i valori dal pannello arrivano come stringa
        if isinstance(_env_defaults[k], int) and not isinstance(_env_defaults[k], bool):
            try:
                val = int(str(val).strip() or _env_defaults[k])
            except ValueError:
                val = _env_defaults[k]
        setattr(settings, k, val)
    from ..providers.registry import refresh_models, reload_providers

    reload_providers()
    await refresh_models()


async def set_value(db: AsyncSession, key: str, value: str, user_id: str | None) -> None:
    if key not in MANAGED_KEYS:
        raise ValueError("Impostazione non gestibile")
    _, secret = MANAGED_KEYS[key]
    row = await db.get(AppSetting, key)
    value = (value or "").strip()
    if key.endswith(("_model", "_default_model", "_provider")) or key in ("image_model", "video_model"):
        value = value.lower().replace(" ", "")   # gli id dei modelli sono minuscoli senza spazi
    if not value:
        if row:
            await db.delete(row)
    else:
        stored = encrypt(value) if secret else value
        if row:
            row.value, row.secret, row.updated_by = stored, secret, user_id
        else:
            db.add(AppSetting(key=key, value=stored, secret=secret, updated_by=user_id))
    await db.commit()


async def describe(db: AsyncSession) -> dict[str, dict]:
    """Stato di ogni impostazione: valore effettivo (mascherato se segreto) e provenienza."""
    overrides = await load_overrides(db)
    out = {}
    for k, (label, secret) in MANAGED_KEYS.items():
        env_v = _env_defaults[k]
        db_v = overrides.get(k, "")
        eff = db_v or env_v
        out[k] = {"label": label, "secret": secret, "configured": bool(eff),
                  "source": "admin" if db_v else ("env" if env_v else "none"),
                  "value": mask(eff) if secret else eff}
    return out


async def test_provider(pid: str) -> dict:
    started = time.perf_counter()
    try:
        if pid == "openai":
            from openai import AsyncOpenAI
            client = AsyncOpenAI(api_key=settings.openai_api_key, timeout=15)
            models = await client.models.list()
            detail = f"Connessione OpenAI riuscita: {len(models.data)} modelli disponibili"
        elif pid == "ollama":
            import httpx
            from .neturl import host_url
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.get(host_url(settings.ollama_url).rstrip("/") + "/api/tags")
                response.raise_for_status()
            names = [m["name"] for m in response.json().get("models", [])]
            detail = ", ".join(names[:12]) if names else "Ollama risponde; scarica un modello con ollama pull"
        else:
            return {"ok": False, "detail": "Provider non supportato"}
        return {"ok": True, "detail": detail, "latency_ms": int((time.perf_counter()-started)*1000)}
    except Exception as error:
        return {"ok": False, "detail": str(error)[:300], "latency_ms": int((time.perf_counter()-started)*1000)}
