import asyncio
import json
import os
import re
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator
from ..security.sessions import require_admin
from ..policy import education

router = APIRouter(prefix="/admin/child-rules",tags=["family"])
_lock = asyncio.Lock()


class Profile(BaseModel):
    daily_message_limit: int = Field(ge=5,le=300)
    daily_image_limit: int = Field(ge=0,le=5)
    start: str
    end: str
    allow_web: bool
    allow_images: bool
    allow_cloud: bool

    @field_validator("start","end")
    @classmethod
    def time_valid(cls,value):
        if not re.fullmatch(r"([01][0-9]|2[0-3]):[0-5][0-9]",value):
            raise ValueError("Orario non valido")
        return value


class Profiles(BaseModel):
    young: Profile
    kid: Profile
    teen: Profile


class RulesUpdate(BaseModel):
    profiles: Profiles
    openai_zdr_confirmed: bool = False


@router.get("")
async def child_rules(ctx=Depends(require_admin)):
    return {**education.rules(),"ages":education.AGES}


@router.put("")
async def update_child_rules(body: RulesUpdate,ctx=Depends(require_admin)):
    async with _lock:
        path = education.config_path()
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(body.model_dump()))
        os.replace(temp,path)
    return await child_rules(ctx)
