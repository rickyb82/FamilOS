import secrets
from typing import Literal
from urllib.parse import urlsplit
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from ..db import get_db
from ..models import BrowserSiteApproval, now
from ..security.sessions import require_admin, require_adult, RateLimiter
from ..services.safe_browser import normalize_url
from ..services.browser_approvals import decision, creation
from .auth import audit

router = APIRouter(prefix="/admin/browser/approvals", tags=["browser"])
parent_router = APIRouter(prefix="/parental/browser", tags=["browser"])
review_limiter = RateLimiter(max_hits=10, window_s=300)


class Decision(BaseModel):
    domain: str = Field(min_length=1, max_length=253)
    age_group: Literal["young", "kid", "teen"]
    status: Literal["approved", "blocked", "review"]


@router.get("")
async def listing(ctx=Depends(require_admin), db=Depends(get_db)):
    rows = (await db.execute(select(BrowserSiteApproval).where(BrowserSiteApproval.family_id == ctx.user.family_id)
        .order_by(BrowserSiteApproval.domain, BrowserSiteApproval.age_group))).scalars().all()
    return [{"id": row.id, "domain": row.domain, "age_group": row.age_group, "status": row.status,
             "source": row.source, "updated_at": row.updated_at, "last_checked_at": row.last_checked_at,
             "last_error": row.last_error} for row in rows]


@router.put("")
async def change(body: Decision, request: Request, ctx=Depends(require_admin), db=Depends(get_db)):
    url = normalize_url(body.domain)
    if urlsplit(url).path != "/" or urlsplit(url).query:
        raise HTTPException(400, "Indica soltanto il dominio, senza percorsi o parametri.")
    domain = urlsplit(url).hostname
    async with creation:
        record = await decision(db, ctx.user.family_id, domain, body.age_group)
        if not record:
            record = BrowserSiteApproval(family_id=ctx.user.family_id, domain=domain, age_group=body.age_group)
            db.add(record)
        record.status, record.source = body.status, "admin"
        record.last_error = ""
        record.revision, record.updated_at = secrets.token_hex(16), now()
        await db.commit()
    await audit(db, "browser_site_" + body.status, request, ctx.user, domain + ":" + body.age_group)
    return {"status": body.status}


class Schedule(BaseModel):
    weekday: int = Field(default=6, ge=0, le=6)
    hour: int = Field(default=3, ge=0, le=23)
    minute: int = Field(default=0, ge=0, le=59)
    timezone: Literal["Europe/Rome"] = "Europe/Rome"
    model: str = Field(default="", max_length=200)


@router.get("/schedule")
async def get_schedule(ctx=Depends(require_admin), db=Depends(get_db)):
    from ..services.browser_reviews import schedule_for
    row = await schedule_for(db, ctx.user.family_id)
    return {key: getattr(row, key) for key in ("weekday", "hour", "minute", "timezone", "model",
        "last_started_at", "last_finished_at", "last_error")}


@router.put("/schedule")
async def set_schedule(body: Schedule, request: Request, ctx=Depends(require_admin), db=Depends(get_db)):
    from ..services.browser_reviews import schedule_for, slot
    row = await schedule_for(db, ctx.user.family_id)
    for key, value in body.model_dump().items():
        setattr(row, key, value)
    row.last_slot = slot(row, now())
    await db.commit()
    await audit(db, "browser_schedule_changed", request, ctx.user, "Ollama / Europe/Rome")
    return body.model_dump()


class ParentReview(BaseModel):
    domain: str = Field(min_length=1, max_length=253)
    age_group: Literal["young", "kid", "teen"]
    provider: Literal["ollama", "openai"] = "ollama"


@parent_router.post("/review")
async def parent_review(body: ParentReview, request: Request, ctx=Depends(require_adult), db=Depends(get_db)):
    from ..services.browser_approvals import approve_once
    from ..services.browser_reviews import inspect_site, schedule_for
    review_limiter.check(ctx.user.id)
    url = normalize_url(body.domain)
    if urlsplit(url).path != "/" or urlsplit(url).query:
        raise HTTPException(400, "Indica solo il dominio.")
    domain = urlsplit(url).hostname
    schedule = await schedule_for(db, ctx.user.family_id)
    model = schedule.model if body.provider == "ollama" else ""
    async def review():
        await inspect_site(ctx.user.family_id, domain, body.age_group, body.provider, model)
    await approve_once(db, ctx.user.family_id, domain, body.age_group, review, source=body.provider)
    await audit(db, "browser_parent_review", request, ctx.user, domain + ":" + body.age_group)
    return {"status": "approved"}
