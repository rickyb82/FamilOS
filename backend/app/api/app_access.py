"""Authorization gate for the productivity reverse proxy (including static assets)."""
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from ..security.sessions import AuthContext, require_auth, check_origin
from ..services.apps import app_enabled

router = APIRouter(prefix="/auth/apps", tags=["auth"])
CRAFT_APPS = {"wordcraft", "gridcraft", "pdfcraft", "deckcraft", "ideacraft", "mailcraft"}


@router.get("/{app_id}")
async def authorize_app(app_id: str, request: Request, ctx: AuthContext = Depends(require_auth)):
    if app_id not in CRAFT_APPS or not app_enabled(ctx.user, app_id):
        raise HTTPException(403, "App non abilitata per questo account")
    check_origin(request)
    method = request.headers.get("x-forwarded-method", "GET").upper()
    if method in {"POST", "PUT", "PATCH", "DELETE"}:
        if not request.headers.get("origin") and request.headers.get("sec-fetch-site") != "same-origin":
            raise HTTPException(403, "Origine della richiesta richiesta")
    return Response(status_code=204, headers={"Cache-Control": "no-store"})
