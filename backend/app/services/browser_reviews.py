"""Persistent weekly review schedule. Only local Ollama is used for scheduled work."""
import asyncio
import logging
import secrets
import time
from datetime import timedelta
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import select, update
from ..db import SessionLocal
from ..models import BrowserReviewSchedule, BrowserSiteApproval, now
from .browser_approvals import creation, reviews

log = logging.getLogger(__name__)
render_lock = asyncio.Lock()


def slot(schedule, instant):
    local = instant.astimezone(ZoneInfo(schedule.timezone))
    day = local.date() - timedelta(days=(local.weekday() - schedule.weekday) % 7)
    if day == local.date() and (local.hour, local.minute) < (schedule.hour, schedule.minute):
        day -= timedelta(days=7)
    return day.isoformat()


async def schedule_for(db, family):
    async with creation:
        row = await db.get(BrowserReviewSchedule, family)
        if row is None:
            row = BrowserReviewSchedule(family_id=family, weekday=6, hour=3, minute=0,
                timezone="Europe/Rome", model="", last_slot="")
            row.last_slot = slot(row, now())
            db.add(row)
            await db.commit()
        return row


async def inspect_site(family, domain, age, provider="ollama", model=""):
    """A fresh cookie-free renderer, no user login or admin session in the background."""
    from ..api.remote_browser import Lease, leases, creation as lease_lock, worker, review_frame
    async with render_lock:
        key = secrets.token_hex(32)
        async with lease_lock:
            if len(leases) >= 3:
                raise HTTPException(503, "Browser occupati: verifica non eseguita.")
            lease = Lease("", "", secrets.token_urlsafe(48), time.monotonic() + 180,
                review_family=family, review_age=age)
            leases[key] = lease
        try:
            await worker("POST", "/sessions", {"lease": key, "ticket": lease.ticket})
            await worker("POST", "/sessions/" + key + "/action", {"action": "navigate", "url": "https://" + domain + "/"})
            page = await worker("GET", "/sessions/" + key + "/frame")
            if urlsplit(page["url"]).hostname != domain or not page.get("document_ok"):
                raise HTTPException(503, "Pagina non verificabile o reindirizzata: controlla il dominio esatto.")
            await review_frame(page, age, provider, model or None)
        finally:
            leases.pop(key, None)
            try:
                await worker("DELETE", "/sessions/" + key)
            except HTTPException:
                pass


async def recheck(db, row, model):
    async with reviews[hash((row.family_id, row.domain, row.age_group)) % len(reviews)]:
        await db.refresh(row)
        if row.status != "approved":
            return
        ident, revision = row.id, row.revision
        family, domain, age = row.family_id, row.domain, row.age_group
        await db.commit()
        status, error = "approved", ""
        try:
            await inspect_site(family, domain, age, "ollama", model)
        except HTTPException as exc:
            if exc.status_code == 403:
                status = "blocked"
            else:
                error = "Verifica Ollama non completata. Ultima decisione conservata."
        except Exception:
            error = "Verifica Ollama non completata. Ultima decisione conservata."
        values = dict(last_checked_at=now(), last_error=error)
        if not error:
            values.update(status=status, source="ollama", revision=secrets.token_hex(16), updated_at=now())
        # An administrator's decision always wins over an in-flight model response.
        await db.execute(update(BrowserSiteApproval).where(BrowserSiteApproval.id == ident,
            BrowserSiteApproval.revision == revision, BrowserSiteApproval.status == "approved").values(**values))
        await db.commit()


async def tick():
    async with SessionLocal() as db:
        families = (await db.execute(select(BrowserSiteApproval.family_id).distinct())).scalars().all()
        for family in families:
            schedule = await schedule_for(db, family)
            if not schedule.enabled:
                continue
            due = slot(schedule, now())
            previous, model = schedule.last_slot, schedule.model
            if due <= previous:
                continue
            claimed = await db.execute(update(BrowserReviewSchedule).where(
                BrowserReviewSchedule.family_id == family, BrowserReviewSchedule.last_slot == previous
            ).values(last_slot=due, last_started_at=now(), last_finished_at=None, last_error=""))
            await db.commit()
            if claimed.rowcount != 1:
                continue
            error = ""
            try:
                rows = (await db.execute(select(BrowserSiteApproval).where(
                    BrowserSiteApproval.family_id == family, BrowserSiteApproval.status == "approved"))).scalars().all()
                for row in rows:
                    await recheck(db, row, model)
            except Exception:
                error = "Controllo interrotto. Verifica gli esiti dei singoli siti."
                await db.rollback()
                log.exception("Weekly browser review failed")
            await db.execute(update(BrowserReviewSchedule).where(BrowserReviewSchedule.family_id == family,
                BrowserReviewSchedule.last_slot == due).values(last_finished_at=now(), last_error=error))
            await db.commit()


async def worker_loop():
    while True:
        try:
            await tick()
        except Exception:
            log.exception("Browser review scheduler failed")
        await asyncio.sleep(30)
