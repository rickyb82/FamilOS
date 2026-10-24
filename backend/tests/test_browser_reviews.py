from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from fastapi import HTTPException
from sqlalchemy import select, update
from app.db import SessionLocal
from app.models import BrowserSiteApproval, User
from app.services import browser_approvals as approvals, browser_reviews as reviews


@pytest.mark.parametrize('instant,expected', [
    ('2026-10-25T00:59:00+00:00', '2026-10-18'),
    ('2026-10-25T01:59:00+00:00', '2026-10-18'),
    ('2026-10-25T02:00:00+00:00', '2026-10-25'),
    ('2026-03-29T00:59:00+00:00', '2026-03-22'),
    ('2026-03-29T01:00:00+00:00', '2026-03-29'),
])
def test_sunday_rome_dst(instant, expected):
    schedule = SimpleNamespace(timezone='Europe/Rome', weekday=6, hour=3, minute=0)
    assert reviews.slot(schedule, datetime.fromisoformat(instant)) == expected


async def family(db):
    return (await db.execute(select(User.family_id).where(User.username == 'admin'))).scalar_one()


@pytest.mark.asyncio
async def test_cache_and_age_isolation(seeded):
    check = AsyncMock()
    async with SessionLocal() as db:
        fam = await family(db)
        await approvals.approve_once(db, fam, 'cache.example.com', 'kid', check)
        await approvals.approve_once(db, fam, 'cache.example.com', 'kid', check)
        assert check.await_count == 1
        await approvals.approve_once(db, fam, 'cache.example.com', 'teen', check)
        assert check.await_count == 2


@pytest.mark.asyncio
async def test_revocation_wins_inflight_ai(seeded):
    async with SessionLocal() as db:
        fam = await family(db)
        async def concurrent_admin():
            async with SessionLocal() as other:
                await other.execute(update(BrowserSiteApproval).where(BrowserSiteApproval.domain == 'race.example.com')
                    .values(status='blocked', revision='admin-revoked'))
                await other.commit()
        with pytest.raises(HTTPException) as error:
            await approvals.approve_once(db, fam, 'race.example.com', 'kid', concurrent_admin)
        assert error.value.status_code == 409
        check = AsyncMock()
        with pytest.raises(HTTPException):
            await approvals.approve_once(db, fam, 'race.example.com', 'kid', check)
        check.assert_not_awaited()


@pytest.mark.asyncio
async def test_nightly_local_failure_preserves_decision(seeded, monkeypatch):
    check = AsyncMock(side_effect=HTTPException(503))
    monkeypatch.setattr(reviews, 'inspect_site', check)
    async with SessionLocal() as db:
        fam = await family(db)
        await approvals.approve_once(db, fam, 'local.example.com', 'kid', AsyncMock())
        row = await approvals.decision(db, fam, 'local.example.com', 'kid')
        await reviews.recheck(db, row, 'local-vision')
        await db.refresh(row)
        assert row.status == 'approved' and row.last_error
        assert check.call_args.args[-2:] == ('ollama', 'local-vision')
        check.side_effect = HTTPException(403)
        await reviews.recheck(db, row, 'local-vision')
        await db.refresh(row)
        assert row.status == 'blocked' and row.source == 'ollama'
        count = check.await_count
        await reviews.recheck(db, row, 'local-vision')
        assert check.await_count == count


@pytest.mark.asyncio
async def test_parent_review_default_and_admin_only_changes(admin, anna, kid, monkeypatch):
    check = AsyncMock()
    monkeypatch.setattr(reviews, 'inspect_site', check)
    body = {'domain': 'parent.example.com', 'age_group': 'kid'}
    assert (await anna.post('/api/parental/browser/review', json=body)).status_code == 200
    assert check.call_args.args[3] == 'ollama'
    assert (await kid.post('/api/parental/browser/review', json=body)).status_code == 403
    decision = {**body, 'status': 'blocked'}
    assert (await anna.put('/api/admin/browser/approvals', json=decision)).status_code == 403
    assert (await admin.put('/api/admin/browser/approvals', json=decision)).status_code == 200
    count = check.await_count
    assert (await anna.post('/api/parental/browser/review', json=body)).status_code == 403
    assert check.await_count == count


@pytest.mark.asyncio
async def test_schedule_permissions_and_bounds(admin, anna):
    response = await admin.get('/api/admin/browser/approvals/schedule')
    assert response.status_code == 200
    config = response.json()
    assert (config['weekday'], config['hour'], config['timezone']) == (6, 3, 'Europe/Rome')
    assert (await anna.put('/api/admin/browser/approvals/schedule', json=config)).status_code == 403
    assert (await admin.put('/api/admin/browser/approvals/schedule', json={**config, 'hour': 24})).status_code == 422


@pytest.mark.asyncio
async def test_openai_first_review_does_not_fallback(monkeypatch):
    from app.api.remote_browser import review_frame
    from app.providers import registry
    provider = SimpleNamespace(supports_images=True)
    monkeypatch.setattr(registry, '_providers', lambda: {'ollama': provider})
    with pytest.raises(HTTPException) as error:
        await review_frame({}, 'kid')
    assert error.value.status_code == 503


@pytest.mark.asyncio
async def test_gateway_rejects_unknown_ticket(admin):
    response = await admin.post('/api/browser/gateway', json={'url': 'https://example.com', 'method': 'GET'},
        headers={'X-Browser-Lease': 'unknown', 'X-Browser-Ticket': 'unknown'})
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_browser_session_cannot_be_used_by_other_users(admin, anna, monkeypatch):
    from app.api import remote_browser
    fake = AsyncMock(return_value={})
    monkeypatch.setattr(remote_browser, 'worker', fake)
    response = await admin.post('/api/browser/sessions')
    assert response.status_code == 200
    key = response.json()['id']
    try:
        count = fake.await_count
        assert (await anna.get('/api/browser/sessions/' + key + '/frame')).status_code == 404
        assert (await anna.post('/api/browser/sessions/' + key + '/action', json={'action': 'text', 'text': 'secret'})).status_code == 404
        assert (await anna.delete('/api/browser/sessions/' + key)).status_code == 404
        assert fake.await_count == count
    finally:
        remote_browser.leases.pop(key, None)


@pytest.mark.asyncio
async def test_cache_is_family_scoped(seeded):
    from app.models import Family
    check = AsyncMock()
    async with SessionLocal() as db:
        first = await family(db)
        other = Family(name='Other browser family')
        db.add(other)
        await db.commit()
        await approvals.approve_once(db, first, 'separate.example.com', 'kid', check)
        await approvals.approve_once(db, other.id, 'separate.example.com', 'kid', check)
        assert check.await_count == 2


@pytest.mark.asyncio
async def test_nightly_cannot_overwrite_admin_revocation(seeded, monkeypatch):
    async with SessionLocal() as db:
        fam = await family(db)
        await approvals.approve_once(db, fam, 'night-race.example.com', 'kid', AsyncMock())
        row = await approvals.decision(db, fam, 'night-race.example.com', 'kid')
        async def revoke(*args):
            async with SessionLocal() as other:
                await other.execute(update(BrowserSiteApproval).where(BrowserSiteApproval.id == row.id)
                    .values(status='blocked', source='admin', revision='night-admin-revoked'))
                await other.commit()
        monkeypatch.setattr(reviews, 'inspect_site', revoke)
        await reviews.recheck(db, row, '')
        await db.refresh(row)
        assert row.status == 'blocked' and row.source == 'admin'


@pytest.mark.asyncio
async def test_weekly_claim_survives_repeated_tick(seeded, monkeypatch):
    monkeypatch.setattr(reviews, 'inspect_site', AsyncMock())
    async with SessionLocal() as db:
        fam = await family(db)
        schedule = await reviews.schedule_for(db, fam)
        schedule.last_slot = '2000-01-01'
        await db.commit()
    check = AsyncMock()
    monkeypatch.setattr(reviews, 'recheck', check)
    await reviews.tick()
    count = check.await_count
    assert count > 0
    await reviews.tick()
    assert check.await_count == count
