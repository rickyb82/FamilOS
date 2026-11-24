from fastapi import APIRouter,Depends,Request
from pydantic import BaseModel,ConfigDict
from ..security.sessions import require_admin
from ..db import get_db
from ..services.homepulse import module
from .updates import recent_admin
from .auth import audit

router=APIRouter(prefix='/admin/wellness-module',tags=['wellness-installation'])

@router.get('')
async def status(ctx=Depends(require_admin)):
    return {'enabled':module.enabled(),'bundled':True,'data_preserved':True}

class Change(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    enabled:bool
    confirm_maintenance:bool=False

@router.put('')
async def change(body:Change,request:Request,ctx=Depends(require_admin),db=Depends(get_db)):
    recent_admin(ctx,body.confirm_maintenance)
    result=module.configure(body.enabled)
    await audit(db,'wellness_module',request,ctx.user,'enabled' if body.enabled else 'disabled')
    return result
