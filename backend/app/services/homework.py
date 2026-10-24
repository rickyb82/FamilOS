"""Guardia compiti per i bambini: riconosce le richieste di "fare i compiti al posto mio" e, secondo la policy del
figlio, le lascia passare, le trasforma in aiuto guidato (tutor), le blocca, o chiede il permesso a un genitore.

homework_mode (policy per bambino): allow | tutor | ask_parent | block
Le richieste in attesa (ApprovalRequest) compaiono come notifica agli adulti, che decidono: approva (risposta
completa), approva come tutor, nega — una volta o per il resto della giornata.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..models import ApprovalRequest, Role, User

log = logging.getLogger(__name__)
settings = get_settings()

MODES = ["allow", "tutor", "ask_parent", "block"]

# segnali rapidi: se non compaiono, niente chiamata al modello (zero costo/latenza per le chat normali)
HOMEWORK_HINTS = re.compile(
    r"\b(compit[oi]|esercizi?o?|verifica|interrogazione|tema\b|riassunt[oi]|problem[ai]\b|equazion|frazion|analisi (logica|grammaticale|del periodo)|"
    r"traduci|traduzione|pagina \d+|pag\.? ?\d+|n[°.]? ?\d+|libro di|quaderno|maestr[ao]|prof(essor[ea]|\.)?|scuola|lezione|"
    r"risolvi|risolvere|calcola|scrivi (un|il|la) (tema|testo|riassunto|relazione|ricerca)|fammi (il|la|i|le|un) (tema|riassunto|compiti|esercizi)|"
    r"risposte? (del|alla|alle|dell')|soluzion[ei])\b", re.I)

CLASSIFY_SYSTEM = """Sei il filtro "compiti" di un assistente per bambini. Classifica la richiesta del bambino in UNA categoria:
- "do_homework": chiede di fare il compito al posto suo o la soluzione pronta da copiare (risolvi l'esercizio, scrivi il tema,
  fammi il riassunto del libro, traduci il testo da consegnare, le risposte della verifica, calcola il problema di pag. X).
- "learn": chiede di capire o esercitarsi (spiegami come si fa, ho fatto cosi' e' giusto?, fammi un esempio diverso, interrogami,
  cosa significa questa parola, aiutami a trovare l'errore).
- "other": non riguarda la scuola.
Rispondi SOLO con JSON: {"category":"do_homework|learn|other","confidence":1-5,"subject":"materia o vuoto","reason":"breve"}"""

TUTOR_RULES = ("\n# Modalita' tutor (compiti)\n"
               "Il bambino sta facendo i compiti: NON dare la soluzione completa ne' il testo pronto da copiare. Guidalo a piccoli "
               "passi con domande, suggerimenti e un esempio simile ma diverso; chiedigli di provare e correggi i suoi tentativi. "
               "Se insiste per la risposta, spiega con gentilezza che il compito deve farlo lui e che puoi aiutarlo a capire.")


@dataclass
class Verdict:
    category: str          # do_homework | learn | other
    confidence: int
    subject: str
    reason: str


def mode_for(policy: dict, user: User) -> str:
    if user.role != Role.child:
        return "allow"
    m = str(policy.get("homework_mode") or "ask_parent").lower()
    return m if m in MODES else "ask_parent"


async def classify(text: str, history_tail: list[str]) -> Verdict | None:
    """Ritorna None se la richiesta non ha nemmeno l'aria di riguardare i compiti (nessuna chiamata al modello)."""
    if not HOMEWORK_HINTS.search(text or ""):
        return None
    from .memory import _cheap_provider_model
    from ..providers.registry import get_provider

    pm = _cheap_provider_model()
    if not pm:
        # senza modello: euristica pura — "fare al posto mio" vs "spiegare"
        learn = re.search(r"\b(spiega|spiegami|come si fa|perche|perché|cosa (vuol dire|significa)|giusto\?|corretto\?|interroga|esempio)\b", text, re.I)
        return Verdict("learn" if learn else "do_homework", 2, "", "euristica")
    pid, model = pm
    ctx = "\n".join(history_tail[-3:])[-800:]
    prompt = (f"Ultimi scambi:\n{ctx}\n\n" if ctx else "") + f"Richiesta del bambino:\n{text[:1500]}"
    out = ""
    try:
        async for ev in get_provider(pid).stream(model, CLASSIFY_SYSTEM, [{"role": "user", "content": [{"type": "text", "text": prompt}]}], [],
                                                 max_tokens=120):
            if ev.type == "text_delta":
                out += ev.text
        m = re.search(r"\{.*\}", out, re.S)
        d = json.loads(m.group(0)) if m else {}
        cat = str(d.get("category", "other")).lower()
        return Verdict(cat if cat in ("do_homework", "learn", "other") else "other", int(d.get("confidence", 3) or 3),
                       str(d.get("subject", ""))[:40], str(d.get("reason", ""))[:160])
    except Exception as e:
        log.warning("Classificatore compiti non disponibile: %s", e)
        return Verdict("do_homework", 2, "", "classificatore non disponibile")


async def has_pass(db: AsyncSession, user: User) -> str | None:
    """Permesso 'per oggi' gia' concesso? Ritorna la decisione ('approve' o 'approve_tutor') valida, altrimenti None."""
    now = datetime.now(timezone.utc)
    rows = (await db.execute(select(ApprovalRequest).where(ApprovalRequest.child_id == user.id, ApprovalRequest.status != "pending",
                                                            ApprovalRequest.valid_until != None))).scalars().all()  # noqa: E711
    for r in rows:
        if r.valid_until and r.valid_until.replace(tzinfo=timezone.utc) > now and r.decision in ("approve", "approve_tutor"):
            return r.decision
    return None


async def create_request(db: AsyncSession, user: User, conv_id: str, text: str, verdict: Verdict) -> ApprovalRequest:
    req = ApprovalRequest(family_id=user.family_id, child_id=user.id, conversation_id=conv_id, kind="homework",
                          text=text[:1000], reason=verdict.reason, subject=verdict.subject, status="pending")
    db.add(req)
    await db.commit()
    await _notify_parents(db, user, req)
    return req


async def _notify_parents(db: AsyncSession, child: User, req: ApprovalRequest) -> None:
    """Email ai genitori (se la posta e' configurata). La notifica in-app la vedono comunque dal campanello."""
    try:
        from . import mailer
        if not mailer.email_enabled():
            return
        parents = (await db.execute(select(User).where(User.family_id == child.family_id, User.role.in_([Role.admin, Role.adult]),
                                                       User.is_active == True, User.email != None))).scalars().all()  # noqa: E711,E712
        base = f"https://{settings.public_host}" if settings.public_host and settings.public_host != "localhost" else settings.webauthn_origin
        for p in parents:
            text = (f"Ciao {p.display_name},\n{child.display_name} ha chiesto a {settings.app_name} di fare un compito:\n\n\"{req.text[:300]}\"\n\n"
                    f"Apri {base} e decidi dal campanello in alto (approva, aiuto guidato, nega).")
            await mailer.send_email(p.email, f"{settings.app_name}: {child.display_name} chiede il permesso per i compiti", text, None)
    except Exception as e:
        log.warning("Notifica email ai genitori non inviata: %s", e)


def decide(req: ApprovalRequest, decision: str, scope: str, by: User) -> None:
    req.status = "approved" if decision in ("approve", "approve_tutor") else "denied"
    req.decision = decision
    req.decided_by = by.id
    req.decided_at = datetime.now(timezone.utc)
    if scope == "today" and req.status == "approved":
        end = datetime.now(timezone.utc).replace(hour=23, minute=59, second=59)
        req.valid_until = end
