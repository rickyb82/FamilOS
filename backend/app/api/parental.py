from datetime import datetime,timedelta,timezone
from zoneinfo import ZoneInfo
from fastapi import APIRouter,Depends,HTTPException
from pydantic import BaseModel
from typing import Literal
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ..db import get_db
from ..models import User,Role
from ..security.sessions import require_adult
from ..policy.education import mode_for

router=APIRouter(prefix="/parental",tags=["family"])
class Override(BaseModel):
    mode:Literal["tutor","standard"]
    duration:Literal["hour","today"]="hour"

@router.get("/children")
async def children(ctx=Depends(require_adult),db:AsyncSession=Depends(get_db)):
    users=(await db.execute(select(User).where(User.family_id==ctx.user.family_id,User.role==Role.child,User.is_active==True))).scalars().all()
    return [{"id":u.id,"display_name":u.display_name,"avatar":u.avatar,"age_group":u.age_group.value,
             "browser_ai_review":(u.policy or {}).get("browser_ai_review") is True,"education_mode":mode_for(u),"override_until":(u.policy or {}).get("tutor_override_until","")} for u in users]

@router.put("/children/{user_id}/education")
async def override(user_id:str,body:Override,ctx=Depends(require_adult),db:AsyncSession=Depends(get_db)):
    child=await db.get(User,user_id)
    if not child or child.family_id!=ctx.user.family_id or child.role!=Role.child:raise HTTPException(404)
    policy=dict(child.policy or {})
    if body.mode=="tutor":
        policy.pop("tutor_override_until",None)
    else:
        now=datetime.now(timezone.utc)
        until=now+timedelta(hours=1) if body.duration=="hour" else now.astimezone(ZoneInfo("Europe/Rome")).replace(hour=23,minute=59,second=59)
        policy["tutor_override_until"]=until.isoformat()
    child.policy=policy;await db.commit()
    return {"education_mode":mode_for(child),"override_until":policy.get("tutor_override_until","")}


class BrowserReviewPreference(BaseModel):
    enabled: bool = False

@router.put("/children/{user_id}/browser-review")
async def browser_review_preference(user_id: str, body: BrowserReviewPreference, ctx=Depends(require_adult), db: AsyncSession=Depends(get_db)):
    child = await db.get(User, user_id)
    if not child or not child.is_active or child.family_id != ctx.user.family_id or child.role != Role.child:
        raise HTTPException(404)
    child.policy = {**(child.policy or {}), "browser_ai_review": body.enabled}
    await db.commit()
    return {"browser_ai_review": body.enabled}
