"""Admin-only bridge to the opt-in Ubuntu updater, through a local Unix socket."""
from datetime import datetime, timedelta, timezone
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from ..config import get_settings
from ..security.sessions import AuthContext, require_admin
from ..db import get_db
from .auth import audit

router = APIRouter(prefix="/admin/updates", tags=["updates"])
Target = Literal["core", "wordcraft", "gridcraft", "pdfcraft", "deckcraft", "ideacraft", "mailcraft"]


async def agent_request(method: str, path: str, body: dict | None = None) -> dict:
    transport = httpx.AsyncHTTPTransport(uds=get_settings().updater_socket)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://updater", timeout=5, trust_env=False) as client:
            response = await client.request(method, path, json=body)
            data = response.json()
    except (httpx.HTTPError, ValueError, OSError):
        raise HTTPException(503, "Agente aggiornamenti non disponibile sulla macchina Ubuntu.")
    if response.status_code >= 400:
        raise HTTPException(response.status_code, str(data.get("error", "Operazione non riuscita"))[:500])
    return data


@router.get("")
async def status(ctx: AuthContext = Depends(require_admin)):
    try:
        return {"connected": True, **await agent_request("GET", "/status")}
    except HTTPException as error:
        if error.status_code != 503:
            raise
        return {"connected": False, "channel": "stable", "setup_command": "./familos.sh updater install",
                "message": "Attiva una volta l’agente sulla VM Ubuntu, poi gestisci release e app da questa pagina."}


class CheckIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    channel: Literal["stable", "experimental", "development"] = "stable"
    target: Target = "core"


class InstallIn(CheckIn):
    offer_id: str = Field(min_length=32, max_length=64, pattern=r"^[a-f0-9]+$")
    accept_experimental: bool = False
    confirm_maintenance: bool = False


class DisableIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target: Literal["wordcraft", "gridcraft", "pdfcraft", "deckcraft", "ideacraft", "mailcraft"]
    confirm_maintenance: bool = False


def recent_admin(ctx: AuthContext, confirmed: bool) -> None:
    if not confirmed:
        raise HTTPException(400, "Conferma il backup e l’interruzione temporanea del servizio.")
    if ctx.session.created_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc)-timedelta(minutes=15):
        raise HTTPException(403, "Per modificare il server, esci e accedi di nuovo: serve un accesso negli ultimi 15 minuti.")


@router.post("/check")
async def check(body: CheckIn, request: Request, ctx: AuthContext = Depends(require_admin), db=Depends(get_db)):
    result = await agent_request("POST", "/jobs", {"action": "check", **body.model_dump()})
    await audit(db, "updates_check", request, ctx.user, body.target+":"+body.channel)
    return result


@router.post("/install")
async def install(body: InstallIn, request: Request, ctx: AuthContext = Depends(require_admin), db=Depends(get_db)):
    recent_admin(ctx, body.confirm_maintenance)
    result = await agent_request("POST", "/jobs", {"action": "install", **body.model_dump()})
    await audit(db, "updates_install", request, ctx.user, body.target+":"+body.channel)
    return result


@router.post("/disable")
async def disable(body: DisableIn, request: Request, ctx: AuthContext = Depends(require_admin), db=Depends(get_db)):
    recent_admin(ctx, body.confirm_maintenance)
    result = await agent_request("POST", "/jobs", {"action": "disable", **body.model_dump()})
    await audit(db, "updates_disable", request, ctx.user, body.target)
    return result
