"""Policy di utilizzo per ruolo / fascia d'età.

La policy effettiva di un utente = DEFAULTS[age_group] sovrascritta da user.policy (impostata dal genitore).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..models import AgeGroup, Role, UsageLog, User

settings = get_settings()

ALL_TOOLS = ["web_search", "generate_image", "read_document", "artifacts", "run_code", "mcp", "ask_model", "memory"]

DEFAULTS: dict[str, dict] = {
    AgeGroup.young.value: {
        "label": "Bambino piccolo (5-8 anni)",
        "allowed_tools": ["web_search", "generate_image", "read_document", "artifacts", "memory"],
        "allowed_providers": ["openai", "ollama"],
        "daily_message_limit": 40,
        "daily_image_limit": 5,
        "daily_video_limit": 0,
        "time_windows": [{"days": [0, 1, 2, 3, 4, 5, 6], "start": "08:00", "end": "20:00"}],
        "safe_search": True,
        "moderation": True,
        "homework_mode": "ask_parent",   # allow | tutor | ask_parent | block
        "parent_can_view": True,
        "can_share": True,
        "can_create_projects": True,
        "max_upload_mb": 10,
        "language": "it",
        "provider": "",   # vuoto = primo provider consentito disponibile (scelto dal genitore nel pannello)
        "model": "",
    },
    AgeGroup.kid.value: {
        "label": "Bambino (9-12 anni)",
        "allowed_tools": ["web_search", "generate_image", "read_document", "artifacts", "run_code", "memory"],
        "allowed_providers": ["openai", "ollama"],
        "daily_message_limit": 80,
        "daily_image_limit": 10,
        "daily_video_limit": 1,
        "time_windows": [{"days": [0, 1, 2, 3, 4, 5, 6], "start": "07:30", "end": "21:00"}],
        "safe_search": True,
        "moderation": True,
        "homework_mode": "ask_parent",   # allow | tutor | ask_parent | block
        "parent_can_view": True,
        "can_share": True,
        "can_create_projects": True,
        "max_upload_mb": 20,
        "language": "it",
        "provider": "",
        "model": "",
    },
    AgeGroup.teen.value: {
        "label": "Ragazzo (13-17 anni)",
        "allowed_tools": ["web_search", "generate_image", "read_document", "artifacts", "run_code", "memory"],
        "allowed_providers": ["openai", "ollama"],
        "daily_message_limit": 200,
        "daily_image_limit": 20,
        "daily_video_limit": 3,
        "time_windows": [{"days": [0, 1, 2, 3, 4, 5, 6], "start": "07:00", "end": "23:00"}],
        "safe_search": True,
        "moderation": True,
        "homework_mode": "ask_parent",   # allow | tutor | ask_parent | block
        "parent_can_view": True,
        "can_share": True,
        "can_create_projects": True,
        "max_upload_mb": 50,
        "language": "it",
        "provider": "",
        "model": "",
    },
    AgeGroup.adult.value: {
        "label": "Adulto",
        "allowed_tools": ALL_TOOLS,
        "allowed_providers": ["openai", "ollama"],
        "daily_message_limit": 0,   # 0 = illimitato
        "daily_image_limit": 0,
        "daily_video_limit": 0,
        "time_windows": [],         # vuoto = sempre
        "safe_search": False,
        "moderation": False,
        "parent_can_view": False,
        "can_share": True,
        "can_create_projects": True,
        "max_upload_mb": 200,
        "language": "it",
    },
}

SYSTEM_PROMPTS: dict[str, str] = {
    AgeGroup.young.value: (
        "Sei " + settings.app_name + ", un assistente gentile e paziente che parla con un bambino di circa {age} anni. "
        "Rispondi in italiano semplice, con frasi brevi, tono allegro e incoraggiante. Usa esempi concreti e, se utile, "
        "qualche emoji. Non trattare mai argomenti violenti, spaventosi, sessuali, droghe, armi o adatti solo agli adulti: "
        "se il bambino li chiede, spiega con dolcezza che sono cose da chiedere a mamma o papà. Non chiedere né conservare "
        "dati personali (indirizzo, scuola, numeri di telefono). Non suggerire mai di incontrare sconosciuti o di tenere "
        "segreti ai genitori. Incoraggia la curiosità, la lettura e il gioco. Se ti chiede di fare i compiti al posto suo, "
        "aiutalo a ragionare passo passo invece di dare solo la risposta."
    ),
    AgeGroup.kid.value: (
        "Sei " + settings.app_name + ", un assistente amichevole per un ragazzo/a di circa {age} anni. Rispondi in italiano chiaro e adatto "
        "all'età, con un tono positivo. Evita contenuti violenti, sessuali, su droghe, armi, autolesionismo o inadatti ai "
        "minori: se emergono, rispondi con tatto e invita a parlarne con i genitori. Non chiedere né conservare dati "
        "personali e non incoraggiare contatti con sconosciuti online. Aiuta con scuola, curiosità, creatività e hobby "
        "spiegando i ragionamenti; per i compiti guida al risultato invece di sostituirti. Se qualcosa è pericoloso "
        "(esperimenti, sfide online, ecc.) dillo chiaramente."
    ),
    AgeGroup.teen.value: (
        "Sei " + settings.app_name + ", un assistente per un adolescente di circa {age} anni. Rispondi in italiano, in modo rispettoso e "
        "diretto. Evita contenuti espliciti o pericolosi e, su temi delicati (salute, relazioni, sicurezza), incoraggia il "
        "dialogo con adulti di fiducia. Aiuta con studio, progetti e interessi spiegando i ragionamenti."
    ),
    AgeGroup.adult.value: (
        "Sei " + settings.app_name + ", l'assistente della famiglia. Rispondi in italiano salvo diversa richiesta, in modo chiaro e completo."
    ),
}

DAILY_KINDS = {"message": "daily_message_limit", "image": "daily_image_limit", "video": "daily_video_limit"}


def effective_policy(user: User) -> dict:
    group = AgeGroup.adult.value if user.role != Role.child else user.age_group.value
    pol = dict(DEFAULTS[group])
    pol.update(user.policy or {})
    pol["allowed_providers"] = [p for p in pol.get("allowed_providers", []) if p in {"openai", "ollama"}]
    pol["age_group"] = group
    from ..services.apps import user_apps
    pol["apps"] = sorted(user_apps(user))
    if user.role != Role.child:
        # gli adulti possono sempre usare il modello locale e il router Auto (anche con una policy salvata prima che esistessero)
        pol["allowed_providers"] = list(dict.fromkeys(list(pol.get("allowed_providers", [])) + ["ollama"]))
    from .education import apply_to_policy
    return apply_to_policy(user, pol)


def system_prompt_for(user: User, tz: str = "Europe/Rome") -> str:
    group = AgeGroup.adult.value if user.role != Role.child else user.age_group.value
    age = ""
    if user.birth_year:
        age = str(datetime.now().year - user.birth_year)
    base = SYSTEM_PROMPTS[group].replace("{age}", age or "?")
    if user.role == Role.child:
        from .education import TUTOR, AGE_METHODS, mode_for
        base += "\n" + (TUTOR if mode_for(user) == "tutor" else "Il genitore ha autorizzato risposte dirette: puoi dare spiegazioni complete e soluzioni, mantenendo linguaggio e contenuti adatti all’età.") + "\n" + AGE_METHODS.get(group, AGE_METHODS["young"])
    from ..config import org_mode_on
    if org_mode_on():
        base = base.replace("l'assistente della famiglia", "l'assistente di lavoro dell'organizzazione: aiuti con documenti, "
                            "preventivi, posta, schemi tecnici e analisi, con tono professionale e preciso")
    extra = (user.policy or {}).get("custom_instructions", "")
    # NB: niente data/ora qui: questa parte deve restare identica tra una richiesta e l'altra per il prompt caching.
    fmt = ("Formattazione: Markdown. Per frecce e simboli usa i caratteri Unicode (→ × ≤ ° %), NON comandi LaTeX "
           "come $\\rightarrow$; riserva $...$ e $$...$$ alle vere formule matematiche.")
    return f"{base}\n\n{fmt}\n\nL'utente si chiama {user.display_name}.\n{extra}".strip()


def dynamic_context(tz: str = "Europe/Rome") -> str:
    """Parte variabile del prompt (dopo il punto di cache): data e ora correnti."""
    now = datetime.now(ZoneInfo(tz)).strftime("%A %d %B %Y, %H:%M")
    return f"Data e ora attuali: {now}."


TOOL_ALIASES = {"search_documents": "read_document",  # stesso permesso dei documenti
                "create_artifact": "artifacts", "update_artifact": "artifacts",
                "list_artifacts": "artifacts", "delete_artifact": "artifacts",
                "remember": "memory", "forget": "memory",
                "write_document": "mcp", "write_spreadsheet": "mcp", "pdf_tool": "mcp", "write_presentation": "mcp", "mind_map": "mcp", "starlink_status": "mcp"}   # app ArtCraft: stesso permesso dei server MCP


def tool_allowed(policy: dict, tool_name: str) -> bool:
    # software non abilitato per l'utente: i suoi tool non esistono (apps nel policy = quelli consentiti; assente = tutti)
    from ..services.apps import app_of_tool
    app = app_of_tool(tool_name)
    if app and "apps" in policy and app not in policy["apps"]:
        return False
    if tool_name.startswith("mcp__"):
        return "mcp" in policy.get("allowed_tools", [])
    return TOOL_ALIASES.get(tool_name, tool_name) in policy.get("allowed_tools", [])


def check_time_window(policy: dict, tz: str = "Europe/Rome") -> None:
    windows = policy.get("time_windows") or []
    if not windows:
        return
    now = datetime.now(ZoneInfo(tz))
    hm = now.strftime("%H:%M")
    for w in windows:
        if now.weekday() in w.get("days", []) and w["start"] <= hm <= w["end"]:
            return
    raise HTTPException(
        status.HTTP_403_FORBIDDEN,
        "In questo orario " + settings.app_name + " non è disponibile per te. Chiedi a mamma o papà se serve una eccezione.",
    )


async def check_daily_quota(db: AsyncSession, user: User, policy: dict, kind: str) -> None:
    key = DAILY_KINDS.get(kind)
    if not key:
        return
    limit = int(policy.get(key, 0) or 0)
    if limit <= 0:
        return
    since = datetime.now(timezone.utc) - timedelta(days=1)
    n = (
        await db.execute(
            select(func.count()).select_from(UsageLog).where(
                UsageLog.user_id == user.id, UsageLog.kind == kind, UsageLog.created_at >= since
            )
        )
    ).scalar_one()
    if n >= limit:
        nice = {"message": "messaggi", "image": "immagini", "video": "video"}[kind]
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, f"Hai raggiunto il limite giornaliero di {nice} ({limit}).")


async def log_usage(db: AsyncSession, user: User, kind: str, provider: str = "", model: str = "",
                    input_tokens: int = 0, output_tokens: int = 0, detail: str = "",
                    cache_read_tokens: int = 0, cache_write_tokens: int = 0) -> None:
    db.add(UsageLog(family_id=user.family_id, user_id=user.id, kind=kind, provider=provider, model=model,
                    input_tokens=input_tokens, output_tokens=output_tokens, detail=detail[:300],
                    cache_read_tokens=cache_read_tokens, cache_write_tokens=cache_write_tokens))
    await db.commit()
