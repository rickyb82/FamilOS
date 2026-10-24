"""Installation branding, public identity and safe administrator logo uploads."""
from __future__ import annotations
import asyncio
import io
import json
import os
import re
import uuid
from pathlib import Path
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from PIL import Image, ImageOps, UnidentifiedImageError
from ..config import get_settings
from ..security.sessions import require_admin, require_auth

router = APIRouter(tags=["installation"])
_lock = asyncio.Lock()
DEFAULT_LOGO = Path(__file__).resolve().parents[1] / "assets" / "familos.png"


def directory() -> Path:
    path = get_settings().data_dir / "branding"
    path.mkdir(parents=True, exist_ok=True)
    return path


def identity() -> dict:
    path = directory() / "identity.json"
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError):
        value = {}
    name = str(value.get("name") or get_settings().brand_name).strip()[:60] or "FamilOS"
    logo = str(value.get("logo") or "")
    if not re.fullmatch(r"[a-f0-9]{32}\.png", logo) or not (directory() / logo).is_file():
        logo = ""
    revision = str(path.stat().st_mtime_ns) if path.exists() else "default"
    return {"name": name, "logo": logo, "revision": revision,
            "logo_url": f"/api/branding/icon/512?v={revision}", "custom_logo": bool(logo)}


@router.get("/branding")
async def public_branding():
    return {k: v for k, v in identity().items() if k != "logo"}


@router.get("/branding/icon/{size}")
async def icon(size: int):
    if size not in (64, 180, 192, 512):
        raise HTTPException(404)
    current = identity()
    path = directory() / current["logo"] if current["logo"] else DEFAULT_LOGO
    with Image.open(path) as source:
        image = ImageOps.contain(source.convert("RGBA"), (size, size))
        canvas = Image.new("RGBA", (size, size), "white")
        canvas.alpha_composite(image, ((size-image.width)//2, (size-image.height)//2))
        output = io.BytesIO()
        canvas.save(output, format="PNG")
    return Response(output.getvalue(), media_type="image/png",
                    headers={"Cache-Control": "public, max-age=60", "X-Content-Type-Options": "nosniff"})


@router.get("/branding/manifest.webmanifest")
async def manifest():
    current = identity()
    return Response(json.dumps({"id": "/app", "name": current["name"], "short_name": current["name"][:24],
        "start_url": "/app", "scope": "/", "display": "standalone", "orientation": "any", "lang": "it",
        "background_color": "#efedf4", "theme_color": "#e7e1ef",
        "icons": [{"src": f"/api/branding/icon/{size}?v={current['revision']}", "sizes": f"{size}x{size}",
                   "type": "image/png", "purpose": "any"} for size in (192, 512)]}),
        media_type="application/manifest+json", headers={"Cache-Control": "no-cache"})


@router.post("/admin/branding")
async def update_branding(name: str = Form(..., min_length=1, max_length=60),
                          reset: bool = Form(False), logo: UploadFile | None = File(None),
                          ctx=Depends(require_admin)):
    name = name.strip()
    if not name or any(ord(c) < 32 for c in name):
        raise HTTPException(400, "Indica un nome valido, fino a 60 caratteri.")
    encoded = None
    if logo and not reset:
        data = await logo.read(2 * 1024 * 1024 + 1)
        if len(data) > 2 * 1024 * 1024:
            raise HTTPException(413, "Il logo deve essere inferiore a 2 MB.")
        try:
            with Image.open(io.BytesIO(data)) as source:
                if source.format not in ("PNG", "JPEG", "WEBP") or max(source.size) > 4096:
                    raise ValueError()
                image = ImageOps.exif_transpose(source).convert("RGBA")
                image.thumbnail((1024, 1024))
                output = io.BytesIO()
                image.save(output, format="PNG")
                encoded = output.getvalue()
        except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
            raise HTTPException(400, "Usa un logo PNG, JPEG o WebP, massimo 4096 × 4096.")
    async with _lock:
        previous = identity()
        filename = previous["logo"]
        if reset:
            filename = ""
            name = get_settings().brand_name
        elif encoded:
            filename = uuid.uuid4().hex + ".png"
            (directory() / filename).write_bytes(encoded)
        config = directory() / "identity.json"
        temp = config.with_suffix(".tmp")
        temp.write_text(json.dumps({"name": name, "logo": filename}))
        os.replace(temp, config)
        if previous["logo"] and previous["logo"] != filename:
            (directory() / previous["logo"]).unlink(missing_ok=True)
    get_settings().webauthn_rp_name = name
    return await public_branding()

def _personal_directory(user_id: str) -> Path:
    path = directory() / "users" / user_id
    path.mkdir(parents=True, exist_ok=True)
    return path


@router.get("/branding/profile")
async def personal_identity(ctx=Depends(require_auth)):
    path = _personal_directory(ctx.user.id) / "logo.png"
    revision = str(path.stat().st_mtime_ns) if path.exists() else ""
    wallpaper = _personal_directory(ctx.user.id) / "wallpaper.webp"
    return {"wallpaper_url": f"/api/branding/profile/wallpaper?v={wallpaper.stat().st_mtime_ns}" if wallpaper.exists() else "", "custom_logo": bool(revision), "revision": revision,
            "logo_url": f"/api/branding/profile/icon/512?v={revision}" if revision else ""}


@router.get("/branding/profile/icon/{size}")
async def personal_icon(size: int, ctx=Depends(require_auth)):
    if size not in (64,180,192,512):
        raise HTTPException(404)
    path = _personal_directory(ctx.user.id) / "logo.png"
    if not path.exists():
        raise HTTPException(404)
    with Image.open(path) as image:
        image = ImageOps.pad(image.convert("RGBA"),(size,size),color="white")
        output = io.BytesIO()
        image.save(output,format="PNG")
    return Response(output.getvalue(),media_type="image/png",
                    headers={"Cache-Control":"private, no-store","X-Content-Type-Options":"nosniff"})


@router.post("/branding/profile")
async def update_personal_logo(reset: bool = Form(False), logo: UploadFile | None = File(None),
                              ctx=Depends(require_auth)):
    path = _personal_directory(ctx.user.id) / "logo.png"
    if reset:
        path.unlink(missing_ok=True)
        return await personal_identity(ctx)
    if not logo:
        raise HTTPException(400,"Scegli un logo.")
    data = await logo.read(2*1024*1024+1)
    if len(data) > 2*1024*1024:
        raise HTTPException(413,"Il logo deve essere inferiore a 2 MB.")
    try:
        with Image.open(io.BytesIO(data)) as source:
            if source.format not in ("PNG","JPEG","WEBP") or max(source.size)>4096:
                raise ValueError()
            image = ImageOps.exif_transpose(source).convert("RGBA")
            image.thumbnail((1024,1024))
            output = io.BytesIO(); image.save(output,format="PNG")
    except (UnidentifiedImageError,OSError,ValueError,Image.DecompressionBombError):
        raise HTTPException(400,"Usa PNG, JPEG o WebP, massimo 4096 × 4096.")
    async with _lock:
        temp = path.with_name(uuid.uuid4().hex + ".tmp")
        temp.write_bytes(output.getvalue())
        os.replace(temp,path)
    return await personal_identity(ctx)

@router.post("/branding/profile/wallpaper")
async def update_wallpaper(reset: bool = Form(False), image: UploadFile | None = File(None), ctx=Depends(require_auth)):
    path = _personal_directory(ctx.user.id) / "wallpaper.webp"
    if reset:
        path.unlink(missing_ok=True)
        return await personal_identity(ctx)
    if not image:
        raise HTTPException(400,"Scegli un’immagine.")
    data=await image.read(8*1024*1024+1)
    if len(data)>8*1024*1024:
        raise HTTPException(413,"Lo sfondo deve essere inferiore a 8 MB.")
    try:
        with Image.open(io.BytesIO(data)) as source:
            if source.format not in ("PNG","JPEG","WEBP") or max(source.size)>8192:raise ValueError()
            source=ImageOps.exif_transpose(source).convert("RGB")
            source.thumbnail((2560,1600))
            output=io.BytesIO();source.save(output,format="WEBP",quality=88)
    except (UnidentifiedImageError,OSError,ValueError,Image.DecompressionBombError):
        raise HTTPException(400,"Usa PNG, JPEG o WebP, massimo 8192 × 8192.")
    async with _lock:
        temp=path.with_name(uuid.uuid4().hex+".tmp")
        temp.write_bytes(output.getvalue());os.replace(temp,path)
    return await personal_identity(ctx)


@router.get("/branding/profile/wallpaper")
async def personal_wallpaper(ctx=Depends(require_auth)):
    path=_personal_directory(ctx.user.id)/"wallpaper.webp"
    if not path.exists():raise HTTPException(404)
    return Response(path.read_bytes(),media_type="image/webp",headers={"Cache-Control":"private, no-store","X-Content-Type-Options":"nosniff"})
