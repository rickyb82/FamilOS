"""Registro delle operazioni della famiglia, senza contenuti delle richieste."""
from __future__ import annotations

import asyncio
import logging

from fastapi import Request


log = logging.getLogger(__name__)

# Sezioni non registrate dal middleware: auth scrive gia' i suoi eventi (con piu' dettaglio), events e tts sono rumore.
SKIP_SECTIONS = {"auth", "events", "tts", "health", "docs", "openapi.json"}
# Percorsi in sola lettura anche se usano POST (ricerche, anteprime) o che leggono il Log stesso.
SKIP_ROUTES = {"/api/admin/audit", "/api/admin/audit.csv"}
MUTATING = {"POST", "PUT", "PATCH", "DELETE"}
FAILURE_MARKERS = ("failed", "unknown", "denied", "refused", "blocked")

_tasks: set[asyncio.Task] = set()


def event_ok(event: str) -> bool:
    """Esito degli eventi scritti dai router: i nomi dei fallimenti lo dicono gia' (login_failed, mfa_totp_failed…)."""
    return not any(m in event for m in FAILURE_MARKERS)


def remember_actor(request: Request, user_id: str, family_id: str) -> None:
    """Chiamata da chi autentica la richiesta (sessione o token di frame): il middleware sa poi a chi attribuirla."""
    request.state.audit_actor = (user_id, family_id)


def describe(method: str, route: str, section: str) -> str:
    """Nome dell'evento dalla rotta: sezione + segmenti fissi + verbo (files.upload, projects.delete, admin.providers.update)."""
    parts = [p for p in route.split("/") if p and not p.startswith("{")]
    # /api/<section>/… oppure /files/… (download, non mutante)
    rest = parts[2:] if parts[:1] == ["api"] else parts[1:]
    verb = {"PUT": "update", "PATCH": "update", "DELETE": "delete"}.get(method, "" if rest else "create")
    name = ".".join([section, *rest, *([verb] if verb else [])])
    return name[:60]


async def _write(row: dict) -> None:
    from ..db import SessionLocal
    from ..models import AuditLog
    try:
        async with SessionLocal() as db:
            db.add(AuditLog(**row))
            await db.commit()
    except Exception:
        log.exception("Registro attivita': scrittura fallita")


async def middleware(request: Request, call_next):
    response = await call_next(request)
    try:
        path = request.url.path
        if (not path.startswith("/api/")
                or (path in SKIP_ROUTES and response.status_code != 403)):
            return response
        section = path.split("/")[2] if path.count("/") >= 2 else ""
        if section in SKIP_SECTIONS:
            return response
        actor = getattr(request.state, "audit_actor", None)
        if not actor:
            return response
        route_obj = request.scope.get("route")
        route = getattr(route_obj, "path", path)
        status_code = response.status_code
        # /api/<app>/session: la pagina di un'app che si apre (il percorso della rotta non ha il prefisso /api)
        opened = request.method == "GET" and path.endswith("/session") and path.count("/") == 3
        # una lettura rifiutata (403: non admin, app non abilitata…) e' un tentativo da registrare anche se non cambia nulla
        denied = status_code == 403 and request.method not in MUTATING
        if request.method not in MUTATING and not opened and not denied:
            return response
        params = request.scope.get("path_params") or {}
        event = (f"{section}.denied" if denied else f"{section}.opened" if opened else describe(request.method, route, section))[:60]
        from ..security.sessions import client_ip
        row = {
            "family_id": actor[1], "user_id": actor[0], "event": event, "app": section[:30], "ok": status_code < 400,
            "target": ",".join(str(v) for v in params.values())[:200],
            "detail": f"{request.method} {route} → {status_code}"[:500], "ip": client_ip(request)[:64],
        }
        t = asyncio.create_task(_write(row))
        _tasks.add(t)
        t.add_done_callback(_tasks.discard)
    except Exception:
        log.exception("Registro attivita': evento non registrato")
    return response
