"""Posta elettronica (MailCraft): la pagina ✉️ Posta incornicia la web app di MailCraft con il token dell'utente."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..models import Role
from ..services import mailcraft
from ..security.sessions import AuthContext, require_auth

router = APIRouter(prefix="/mail", tags=["mail"])


def _need_app(ctx: AuthContext) -> None:
    from ..services.apps import app_enabled
    if not app_enabled(ctx.user, "mailcraft"):
        raise HTTPException(403, "La Posta non e' abilitata per il tuo account: chiedi all'amministratore")


@router.get("/session")
async def mail_session(ctx: AuthContext = Depends(require_auth)):
    """Token utente per la web app: la pagina apre /mailcraft/?server=<origine>/mailcraft&token=…&lang=it."""
    if ctx.user.role == Role.child:
        raise HTTPException(403, "La posta e' riservata agli adulti")
    _need_app(ctx)
    if not mailcraft.enabled():
        raise HTTPException(503, "MailCraft non e' attivo su questo server (./familai.sh mailcraft up)")
    try:
        tok = await mailcraft.user_token(ctx.user.id)
    except Exception as e:
        raise HTTPException(502, f"MailCraft non raggiungibile: {e}")
    return {"token": tok, "path": "/mailcraft/", "lang": "it"}


@router.post("/logout")
async def mail_logout(ctx: AuthContext = Depends(require_auth)):
    """Revoca i token di posta dell'utente (web e assistente)."""
    try:
        await mailcraft.revoke(ctx.user.id)
    except Exception as e:
        raise HTTPException(502, f"MailCraft non raggiungibile: {e}")
    from ..tools.mcp_client import mcp_manager
    await mcp_manager.drop_user_connections(ctx.user.id)
    return {"ok": True}


# ---------------------------------------------------------------- suggerimenti di risposta (stile Gmail) e bozze ----
from pydantic import BaseModel  # noqa: E402


class AssistIn(BaseModel):
    kind: str = "suggest"            # suggest = 3 risposte brevi da un tocco; draft = bozza completa
    subject: str = ""
    sender: str = ""
    text: str = ""                   # corpo della mail ricevuta (testo), MailCraft lo tronca gia'
    instruction: str = ""            # per draft: cosa dire ("conferma per giovedi' alle 10", "rifiuta gentilmente"…)
    tone: str = "professionale"      # professionale | amichevole | formale
    language: str = "it"
    signature: str = ""              # firma dell'utente da lasciare in fondo (MailCraft la aggiunge gia': lasciare vuoto)
    local_only: bool = True          # true = solo modello locale (la mail non lascia la rete); false = anche cloud


SUGGEST_SYSTEM = (
    "Sei l'assistente di posta di {app}. Ricevi una mail e proponi risposte brevissime da un tocco, nello stile dei "
    "suggerimenti di Gmail: 3 frasi diverse tra loro (es. una positiva, una interlocutoria, una che rimanda), massimo 60 "
    "caratteri ciascuna, nella lingua della mail, senza saluti ne' firma, senza virgolette. Rispondi SOLO con un JSON: "
    "{{\"suggestions\": [\"…\", \"…\", \"…\"]}}. Se la mail non aspetta risposta (newsletter, notifica automatica, fattura) "
    "rispondi {{\"suggestions\": []}}."
)
DRAFT_SYSTEM = (
    "Sei l'assistente di posta di {app}: scrivi la risposta a una mail per conto dell'utente. Tono {tone}, lingua della "
    "mail salvo indicazione diversa, frasi chiare, niente preamboli tipo 'Ecco la bozza', niente firma (viene aggiunta "
    "dopo), niente oggetto. Segui l'indicazione dell'utente se c'e'; se manca, rispondi nel modo piu' sensato ai punti "
    "della mail. Non inventare fatti, date o cifre che non sono nella mail o nell'indicazione. Rispondi SOLO con il testo "
    "della risposta."
)


def _pick_provider(local_only: bool) -> tuple[str, str] | None:
    """(provider, modello): il locale se c'e'; il cloud solo se l'utente lo consente e non c'e' un locale."""
    from ..providers.registry import available_providers, get_provider, local_provider_id

    lid = local_provider_id()
    if lid:
        try:
            return lid, get_provider(lid).default_model
        except Exception:
            pass
    if local_only:
        return None
    for p in available_providers():
        pid = p.get("id") if isinstance(p, dict) else getattr(p, "id", None)
        if pid and pid not in ("mock", "auto"):
            try:
                return pid, get_provider(pid).default_model
            except Exception:
                continue
    return None


@router.post("/assist")
async def mail_assist(body: AssistIn, ctx: AuthContext = Depends(require_auth)):
    """Suggerimenti di risposta (kind=suggest) o bozza completa (kind=draft) per la mail aperta in MailCraft.
    Di default usa solo il modello locale: il contenuto della posta non esce dalla rete."""
    import json
    import re
    _need_app(ctx)

    from ..config import get_settings
    from ..providers.registry import get_provider

    if ctx.user.role == Role.child:
        raise HTTPException(403, "Riservato agli adulti")
    pm = _pick_provider(body.local_only)
    if not pm:
        raise HTTPException(503, "Nessun modello locale configurato (Ollama o server LLM di casa): attivane uno, oppure consenti il cloud")
    pid, model = pm
    app = get_settings().app_name
    mail = f"Da: {body.sender}\nOggetto: {body.subject}\n\n{(body.text or '')[:6000]}"
    if body.kind == "draft":
        system = DRAFT_SYSTEM.format(app=app, tone=body.tone or "professionale")
        user = mail + (f"\n\n---\nIndicazione dell'utente: {body.instruction[:800]}" if body.instruction.strip() else "")
        max_tokens = 700
    else:
        system = SUGGEST_SYSTEM.format(app=app)
        user = mail
        max_tokens = 160
    out = ""
    try:
        async for ev in get_provider(pid).stream(model, system, [{"role": "user", "content": [{"type": "text", "text": user}]}], [],
                                                 max_tokens=max_tokens):
            if ev.type == "text_delta":
                out += ev.text
    except Exception as e:
        raise HTTPException(502, f"Modello {pid} non disponibile: {str(e)[:200]}")
    out = re.sub(r"<think>.*?</think>", "", out, flags=re.S).strip()
    if body.kind == "draft":
        return {"kind": "draft", "draft": out, "provider": pid, "model": model}
    m = re.search(r"\{.*\}", out, re.S)
    sugg: list[str] = []
    try:
        sugg = [str(s).strip().strip('"') for s in (json.loads(m.group(0)) if m else {}).get("suggestions", [])]
    except Exception:
        sugg = [ln.strip("-•* \"") for ln in out.splitlines() if ln.strip()][:3]
    sugg = [s[:80] for s in sugg if s][:3]
    return {"kind": "suggest", "suggestions": sugg, "provider": pid, "model": model}
