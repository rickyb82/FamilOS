from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select, delete

from app.config import get_settings
from app.db import SessionLocal
from app.models import SecurityNotice, User
from app.services import security_alerts, mailer


@pytest.fixture
async def notices(seeded,monkeypatch):
    monkeypatch.setattr(get_settings(),"security_login_alerts",True)
    monkeypatch.setattr(mailer,"email_enabled",lambda:True)
    send=AsyncMock()
    monkeypatch.setattr(mailer,"send_email",send)
    async with SessionLocal() as db:
        await db.execute(delete(SecurityNotice));await db.commit()
    yield send
    async with SessionLocal() as db:
        await db.execute(delete(SecurityNotice));await db.commit()


async def enqueue(event="login_google",detail="",ip="203.0.113.5",unknown=False):
    async with SessionLocal() as db:
        user=None if unknown else (await db.execute(select(User).where(User.username=="anna"))).scalar_one()
        await security_alerts.enqueue(db,event,user,ip,detail)


@pytest.mark.parametrize("event",["login_google","login_passkey","login_pin","mfa_totp_ok","sync_device_login","login_failed","login_refused","login_unknown","google_login_failed"])
async def test_success_and_failure_notices_go_to_admins_only(notices,event):
    await enqueue(event,detail="secret-password-and-code-never-include-this")
    await security_alerts.deliver_pending()
    assert notices.await_count>=1
    recipients={call.args[0] for call in notices.await_args_list}
    assert "admin@test.local" in recipients
    assert "anna@test.local" not in recipients
    for call in notices.await_args_list:
        assert "secret-password-and-code" not in call.args[2]
        assert "203.0.113.5" in call.args[2]
        assert "anna@test.local" in call.args[2]


async def test_repeated_failures_are_deduplicated_and_hourly_capped(notices):
    for _ in range(5):await enqueue("login_failed")
    async with SessionLocal() as db:
        assert len((await db.execute(select(SecurityNotice))).scalars().all())==1
    for n in range(30):await enqueue("login_failed",ip=f"203.0.113.{n+10}")
    async with SessionLocal() as db:
        assert len((await db.execute(select(SecurityNotice))).scalars().all())==20


async def test_mail_failure_retried_without_affecting_authentication(notices):
    notices.side_effect=RuntimeError("SMTP unavailable")
    await enqueue()
    await security_alerts.deliver_pending()
    async with SessionLocal() as db:
        notice=(await db.execute(select(SecurityNotice))).scalar_one()
        assert notice.sent_at is None and notice.attempts==1
        notice.next_attempt_at=datetime.now(timezone.utc)-timedelta(seconds=1)
        await db.commit()
    notices.side_effect=None
    await security_alerts.deliver_pending()
    async with SessionLocal() as db:
        notice=(await db.execute(select(SecurityNotice))).scalar_one()
        assert notice.sent_at is not None and notice.attempts==2
    count=notices.await_count
    await security_alerts.deliver_pending()
    assert notices.await_count==count


async def test_password_step_alone_does_not_report_completed_mfa_login(notices):
    await enqueue("login_password","mfa_required")
    async with SessionLocal() as db:
        assert not (await db.execute(select(SecurityNotice))).scalars().all()


async def test_notifications_can_be_disabled_and_require_mail_setup(notices,monkeypatch):
    monkeypatch.setattr(get_settings(),"security_login_alerts",False)
    await enqueue()
    monkeypatch.setattr(get_settings(),"security_login_alerts",True)
    monkeypatch.setattr(mailer,"email_enabled",lambda:False)
    await enqueue()
    async with SessionLocal() as db:
        assert not (await db.execute(select(SecurityNotice))).scalars().all()
