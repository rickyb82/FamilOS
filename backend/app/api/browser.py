import asyncio,json,os,re
from fastapi import APIRouter,Depends,HTTPException
from pydantic import BaseModel,Field,field_validator
from ..security.sessions import require_auth,require_admin,RateLimiter
from ..models import Role
from ..services import safe_browser
router=APIRouter(tags=["browser"])
limiter=RateLimiter(max_hits=20,window_s=300)
_lock=asyncio.Lock()

class BrowserConfig(BaseModel):
    sites:list[str]=Field(max_length=80)
    pihole_enabled:bool=False
    dns_server:str="auto"
    @field_validator("sites")
    @classmethod
    def sites_valid(cls,values):
        return list(dict.fromkeys(safe_browser.normalize_url("https://"+value.strip().lower()).split("/")[2] for value in values if value.strip()))
    @field_validator("dns_server")
    @classmethod
    def dns_valid(cls,value):
        import ipaddress
        if value not in ("auto","localhost","host.docker.internal"):
            try:ipaddress.ip_address(value)
            except ValueError:raise ValueError("Indica auto oppure un indirizzo IP DNS.")
        return value

class ReadPage(BaseModel):
    url:str=Field(min_length=1,max_length=2000)

@router.get("/browser/sites")
async def sites(ctx=Depends(require_auth)):
    return {"sites":safe_browser.config()["sites"]}

@router.post("/browser/read")
async def read_page(body:ReadPage,ctx=Depends(require_auth)):
    limiter.check(ctx.user.id)
    if ctx.user.role == Role.child:
        from ..policy.permissions import effective_policy,check_time_window
        check_time_window(effective_policy(ctx.user))
    config=safe_browser.config()
    sites=config["sites"] if ctx.user.role==Role.child else None
    try:
        page=await safe_browser.fetch(body.url,sites,config)
        return await safe_browser.check_page(page,ctx.user.age_group.value) if ctx.user.role==Role.child else page
    except HTTPException:raise
    except Exception:raise HTTPException(502,"Non riesco ad aprire questa pagina. Riprova o chiedi a un genitore.")

@router.get("/admin/browser")
async def browser_config(ctx=Depends(require_admin)):
    return safe_browser.config()

@router.put("/admin/browser")
async def update_browser(body:BrowserConfig,ctx=Depends(require_admin)):
    async with _lock:
        path=safe_browser.config_path();temp=path.with_suffix(".tmp")
        temp.write_text(json.dumps(body.model_dump()));os.replace(temp,path)
    return body.model_dump()

@router.post("/admin/browser/test-dns")
async def test_dns(ctx=Depends(require_admin)):
    config=safe_browser.config()
    try:
        addresses=await safe_browser.resolve_public("www.nasa.gov",config)
        return {"ok":True,"server":safe_browser.dns_server(config) if config["pihole_enabled"] else "DNS del sistema","addresses":addresses}
    except HTTPException as e:return {"ok":False,"error":e.detail}
    except Exception:return {"ok":False,"error":"Il resolver DNS non è raggiungibile."}
