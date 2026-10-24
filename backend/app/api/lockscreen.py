from fastapi import APIRouter,Depends,HTTPException,Request
from pydantic import BaseModel,Field
from sqlalchemy.ext.asyncio import AsyncSession
from ..db import get_db
from ..models import Role
from ..security.sessions import require_auth,require_pre_mfa,login_limiter,client_ip
from ..security.passwords import verify_secret
from ..security import webauthn_svc
from .auth import audit,register_failure,locked

router=APIRouter(prefix="/auth",tags=["lock"])

@router.post("/lock")
async def lock_screen(request:Request,ctx=Depends(require_auth),db:AsyncSession=Depends(get_db)):
    ctx.session.screen_locked=True
    await db.commit()
    await audit(db,"screen_locked",request,ctx.user)
    return {"locked":True}

class UnlockSecret(BaseModel):
    secret:str=Field(min_length=1,max_length=200)

@router.post("/unlock/password")
async def unlock_password(body:UnlockSecret,request:Request,ctx=Depends(require_pre_mfa),db:AsyncSession=Depends(get_db)):
    login_limiter.check("unlock:"+ctx.user.id+":"+client_ip(request))
    hashed=ctx.user.pin_hash if ctx.user.role==Role.child else ctx.user.password_hash
    if locked(ctx.user) or not hashed or not verify_secret(hashed,body.secret):
        await register_failure(db,ctx.user)
        raise HTTPException(401,"Password o PIN non validi.")
    ctx.session.screen_locked=False;ctx.user.failed_logins=0
    await db.commit()
    await audit(db,"screen_unlocked",request,ctx.user)
    return {"locked":False}

@router.post("/unlock/passkey/options")
async def unlock_options(ctx=Depends(require_pre_mfa),db:AsyncSession=Depends(get_db)):
    options=await webauthn_svc.authentication_options(db,ctx.user,"unlock:"+ctx.session.id)
    options["userVerification"]="required"
    return options

class PasskeyVerify(BaseModel):
    credential:dict

@router.post("/unlock/passkey/verify")
async def unlock_verify(body:PasskeyVerify,request:Request,ctx=Depends(require_pre_mfa),db:AsyncSession=Depends(get_db)):
    login_limiter.check("unlock-passkey:"+ctx.user.id+":"+client_ip(request))
    try:
        await webauthn_svc.authentication_verify(db,ctx.user,"unlock:"+ctx.session.id,body.credential,require_uv=True)
    except Exception:
        raise HTTPException(401,"Passkey non verificata. Riprova.")
    ctx.session.screen_locked=False;await db.commit()
    await audit(db,"screen_unlocked_passkey",request,ctx.user)
    return {"locked":False}
