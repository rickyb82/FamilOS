"""Test del backend contro un database SQLite temporaneo (nessuna rete, nessun provider).

L'ambiente va impostato PRIMA di importare l'app: `get_settings()` legge le variabili una volta sola."""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="emily-test-"))
os.environ.update({
    "DATA_DIR": str(TMP),
    "DATABASE_URL": f"sqlite+aiosqlite:///{TMP}/test.db",
    "COOKIE_SECURE": "0",
    "REQUIRE_MFA_FOR_ADULTS": "0",
    "SECRET_KEY": "test-secret",
    "LOG_LEVEL": "warning",
    "ADMIN_EMAIL": "admin@test.local",
    "ADMIN_PASSWORD": "test-password",
    "FAMILY_NAME": "Famiglia Test",
})
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402
import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import AgeGroup, Family, Role, Session, User  # noqa: E402
from app.security.passwords import hash_secret  # noqa: E402
from app.security.sessions import SESSION_COOKIE  # noqa: E402

HEADERS = {"X-Requested-With": "FamilAI"}
_ready = False


async def _seed() -> None:
    """Una famiglia con: admin (super amministratore), anna (adulta, tutti i software, reparto tecnico), bob (adulto senza
    software), kid (bambino)."""
    global _ready
    if _ready:
        return
    await init_db()
    async with SessionLocal() as db:
        if (await db.execute(select(User.id).limit(1))).scalar_one_or_none() is None:
            fam = Family(name="Famiglia Test")
            db.add(fam)
            await db.flush()
            db.add(User(family_id=fam.id, email="admin@test.local", username="admin", display_name="Admin", role=Role.admin,
                        age_group=AgeGroup.adult, password_hash=hash_secret("test-password")))
            db.add(User(family_id=fam.id, email="anna@test.local", username="anna", display_name="Anna", role=Role.adult,
                        age_group=AgeGroup.adult, password_hash=hash_secret("x"), departments=["tecnico"]))
            db.add(User(family_id=fam.id, email="bob@test.local", username="bob", display_name="Bob", role=Role.adult,
                        age_group=AgeGroup.adult, password_hash=hash_secret("x"), apps=[]))
            db.add(User(family_id=fam.id, username="kid", display_name="Kid", role=Role.child, age_group=AgeGroup.kid,
                        pin_hash=hash_secret("1234")))
            await db.commit()
    _ready = True


async def session_cookie(username: str) -> str:
    """Una sessione gia' verificata per l'utente (si salta il login: qui si testano i Documenti, non l'accesso)."""
    async with SessionLocal() as db:
        user = (await db.execute(select(User).where(User.username == username))).scalar_one()
        sess = Session(user_id=user.id, expires_at=datetime.now(timezone.utc) + timedelta(hours=1), mfa_verified=True)
        db.add(sess)
        await db.commit()
        return sess.id


def make_client(cookie: str | None = None, token: str | None = None) -> httpx.AsyncClient:
    headers = dict(HEADERS)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    cookies = {SESSION_COOKIE: cookie} if cookie else None
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", headers=headers, cookies=cookies)


@pytest_asyncio.fixture
async def seeded():
    await _seed()


@pytest_asyncio.fixture
async def admin(seeded):
    async with make_client(await session_cookie("admin")) as c:
        yield c


@pytest_asyncio.fixture
async def anna(seeded):
    async with make_client(await session_cookie("anna")) as c:
        yield c


@pytest_asyncio.fixture
async def bob(seeded):
    async with make_client(await session_cookie("bob")) as c:
        yield c


@pytest_asyncio.fixture
async def kid(seeded):
    async with make_client(await session_cookie("kid")) as c:
        yield c


async def pair_device(browser: httpx.AsyncClient, name: str = "PC di prova", platform: str = "windows") -> tuple[str, dict]:
    """Collega un dispositivo come farebbe l'agente: codice da Impostazioni, poi /sync/pair."""
    from app.api.sync import pair_limiter
    pair_limiter.hits.clear()   # tutti i test arrivano dallo stesso IP: il limite (10 ogni 5 minuti) ha un test suo
    r = await browser.post("/api/sync/devices/pairing-code")
    assert r.status_code == 200, r.text
    code = r.json()["code"]
    async with make_client() as anon:
        r = await anon.post("/api/sync/pair", json={"code": code, "name": name, "platform": platform, "app_version": "0.1"})
    assert r.status_code == 200, r.text
    body = r.json()
    return body["token"], body


@pytest.fixture
def device():
    """Factory: `token, info = await device(browser_client)`; i client con il token si creano con make_client(token=...)."""
    return pair_device
