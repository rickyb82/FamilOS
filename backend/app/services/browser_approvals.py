"""Persist AI decisions; a revoked domain cannot silently approve itself again."""
import asyncio
import secrets
from sqlalchemy import select, update
from fastapi import HTTPException
from ..models import BrowserSiteApproval, now

creation = asyncio.Lock()
reviews = [asyncio.Lock() for _ in range(64)]


async def decision(db, family, domain, age):
    return (await db.execute(select(BrowserSiteApproval).where(BrowserSiteApproval.family_id == family,
        BrowserSiteApproval.domain == domain, BrowserSiteApproval.age_group == age).execution_options(populate_existing=True))).scalar_one_or_none()


async def check_blocked(db, family, domain, age):
    record = await decision(db, family, domain, age)
    if record and record.status == "blocked":
        raise HTTPException(403, "Sito bloccato. Solo un amministratore può richiedere una nuova verifica.")
    return record


async def approve_once(db, family, domain, age, review, source="openai"):
    key = (family, domain, age)
    lock = reviews[hash(key) % len(reviews)]
    async with lock:
        async with creation:
            record = await check_blocked(db, family, domain, age)
            if record and record.status == "approved":
                return
            if not record:
                record = BrowserSiteApproval(family_id=family, domain=domain, age_group=age)
                db.add(record)
                await db.commit()
        record_id, revision = record.id, record.revision
        # Do not hold a database transaction while the external provider evaluates the page.
        await db.commit()
        verdict = "approved"
        error = None
        try:
            await review()
        except HTTPException as exc:
            if exc.status_code != 403:
                await db.execute(update(BrowserSiteApproval).where(BrowserSiteApproval.id == record_id,
                    BrowserSiteApproval.revision == revision).values(last_checked_at=now(),
                    last_error="Verifica non completata. Il sito resta da verificare."))
                await db.commit()
                raise
            verdict, error = "blocked", exc
        updated = await db.execute(update(BrowserSiteApproval).where(BrowserSiteApproval.id == record_id,
            BrowserSiteApproval.revision == revision, BrowserSiteApproval.status == "review").values(
                status=verdict, source=source, revision=secrets.token_hex(16), updated_at=now(), last_checked_at=now(), last_error=""))
        await db.commit()
        if updated.rowcount != 1:
            raise HTTPException(409, "La decisione è stata modificata da un amministratore. Riapri il sito.")
        if error:
            raise error
