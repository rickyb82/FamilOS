"""Notifiche push sul telefono: chiave pubblica per il browser, iscrizione e disiscrizione del dispositivo, notifica di prova."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import NativeDevice, PushSubscription, Role
from ..security.sessions import AuthContext, require_auth
from ..services import native_push, push

router = APIRouter(prefix="/push", tags=["push"])


class SubIn(BaseModel):
    subscription: dict
    device: str = Field("", max_length=120)


class UnsubIn(BaseModel):
    endpoint: str = Field(max_length=1000)


class NativeIn(BaseModel):
    platform: str = Field(max_length=10)       # ios | android
    token: str = Field(max_length=400)
    device: str = Field("", max_length=120)
    app_version: str = Field("", max_length=40)


class NativeOut(BaseModel):
    token: str = Field(max_length=400)


@router.get("/key")
async def key(ctx: AuthContext = Depends(require_auth), db: AsyncSession = Depends(get_db)):
    _, public = await push.keys(db)
    n = (await db.execute(select(func.count()).select_from(PushSubscription).where(PushSubscription.user_id == ctx.user.id))).scalar_one()
    m = (await db.execute(select(func.count()).select_from(NativeDevice).where(NativeDevice.user_id == ctx.user.id))).scalar_one()
    return {"public_key": public, "devices": n, "native_devices": m, "native": {"ios": native_push.apns_enabled(), "android": native_push.fcm_enabled()}}


@router.post("/native")
async def native_register(body: NativeIn, ctx: AuthContext = Depends(require_auth), db: AsyncSession = Depends(get_db)):
    """L'app Emily sul telefono registra il suo token APNs / FCM."""
    if ctx.user.role == Role.child:
        raise HTTPException(403, "Notifiche non disponibili")
    try:
        await native_push.register(db, ctx.user.id, body.platform, body.token, body.device, body.app_version)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "enabled": native_push.apns_enabled() if body.platform == "ios" else native_push.fcm_enabled()}


@router.post("/native/unregister")
async def native_unregister(body: NativeOut, ctx: AuthContext = Depends(require_auth), db: AsyncSession = Depends(get_db)):
    await native_push.unregister(db, ctx.user.id, body.token)
    return {"ok": True}


@router.post("/subscribe")
async def subscribe(body: SubIn, ctx: AuthContext = Depends(require_auth), db: AsyncSession = Depends(get_db)):
    if ctx.user.role == Role.child:
        raise HTTPException(403, "Notifiche push non disponibili")
    try:
        await push.subscribe(db, ctx.user.id, body.subscription, body.device)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@router.post("/unsubscribe")
async def unsubscribe(body: UnsubIn, ctx: AuthContext = Depends(require_auth), db: AsyncSession = Depends(get_db)):
    await push.unsubscribe(db, ctx.user.id, body.endpoint)
    return {"ok": True}


@router.post("/test")
async def test(ctx: AuthContext = Depends(require_auth), db: AsyncSession = Depends(get_db)):
    n = await push.send_to_user(db, ctx.user.id, "Emily", "Le notifiche arrivano su questo dispositivo 👍", "/app", "test")
    n += await native_push.send_to_user(db, ctx.user.id, "Emily", "Le notifiche arrivano su questo dispositivo 👍", "/app", "test")
    if not n:
        raise HTTPException(400, "Nessun dispositivo ha ricevuto la notifica: riattivale da questo telefono")
    return {"sent": n}
