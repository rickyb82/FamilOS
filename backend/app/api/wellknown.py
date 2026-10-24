"""I file `/.well-known/` che legano il dominio di Emily all'app nativa: cosi' le passkey (Face ID, impronta) funzionano anche
dentro l'app e i link di Emily si aprono nell'app.
- apple-app-site-association: Team ID + bundle id (APNS_TEAM_ID, APNS_BUNDLE_ID);
- assetlinks.json: pacchetto Android + impronte SHA-256 del certificato di firma (ANDROID_CERT_SHA256).
Senza le impostazioni rispondono 404: l'app continua a funzionare, ma le passkey nell'app no.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from ..config import get_settings

router = APIRouter(prefix="/.well-known", tags=["app"])
settings = get_settings()


@router.get("/apple-app-site-association")
async def apple_app_site_association():
    if not (settings.apns_team_id and settings.apns_bundle_id):
        raise HTTPException(404)
    app_id = f"{settings.apns_team_id}.{settings.apns_bundle_id}"
    body = {"webcredentials": {"apps": [app_id]},
            "applinks": {"details": [{"appIDs": [app_id], "components": [{"/": "/app"}, {"/": "/people*"}, {"/": "/messages*"}, {"/": "/c/*"}]}]}}
    return JSONResponse(body, media_type="application/json", headers={"Cache-Control": "public, max-age=3600"})


@router.get("/assetlinks.json")
async def assetlinks():
    prints = [p.strip().upper() for p in (settings.android_cert_sha256 or "").split(",") if p.strip()]
    if not prints:
        raise HTTPException(404)
    target = {"namespace": "android_app", "package_name": settings.apns_bundle_id, "sha256_cert_fingerprints": prints}
    body = [{"relation": ["delegate_permission/common.handle_all_urls", "delegate_permission/common.get_login_creds"], "target": target}]
    return JSONResponse(body, media_type="application/json", headers={"Cache-Control": "public, max-age=3600"})
