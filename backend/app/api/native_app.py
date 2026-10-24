"""L'app Android di Emily distribuita da Emily stessa (senza Play Store): l'amministratore copia l'APK firmato in
data/app/ (es. emily-1.0.0.apk); la pagina /app lo propone agli Android aperti nel browser. L'iPhone passa da TestFlight.
Il download chiede la sessione: l'APK e' un file dell'organizzazione, non pubblico."""
from __future__ import annotations

import re
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from ..config import get_settings
from ..security.sessions import AuthContext, require_auth

router = APIRouter(prefix="/app", tags=["app"])
settings = get_settings()


def _apk() -> Path | None:
    d = settings.data_dir / "app"
    try:
        files = sorted((p for p in d.glob("*.apk") if p.is_file()), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return None
    return files[0] if files else None


def _version(p: Path) -> str:
    m = re.search(r"(\d+(?:\.\d+)+)", p.stem)
    return m.group(1) if m else ""


@router.get("/android")
async def android_info(ctx: AuthContext = Depends(require_auth)):
    p = _apk()
    if not p:
        return {"available": False}
    return {"available": True, "version": _version(p), "size": p.stat().st_size, "url": "/api/app/android/emily.apk",
            "testflight": settings.testflight_url}


@router.get("/android/emily.apk")
async def android_download(ctx: AuthContext = Depends(require_auth)):
    p = _apk()
    if not p:
        raise HTTPException(404, "APK non disponibile")
    v = _version(p)
    return FileResponse(p, media_type="application/vnd.android.package-archive", filename=f"emily-{v}.apk" if v else "emily.apk")
