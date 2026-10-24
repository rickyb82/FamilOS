"""Adversarial authentication tests: isolated database, no live OAuth or email."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse
import re
import uuid

import pytest
from sqlalchemy import select
from starlette.requests import Request

from app.api import google_auth
from app.config import get_settings
from app.db import SessionLocal
from app.main import app
from app.models import AgeGroup, Role, Session, User, TrustedDevice
from app.security.passwords import hash_secret
from app.security.sessions import SESSION_COOKIE, DEVICE_COOKIE, client_ip, login_limiter, pin_limiter
from conftest import make_client, session_cookie


@pytest.fixture(autouse=True)
def reset_limits():
    login_limiter.hits.clear()
    pin_limiter.hits.clear()
    google_auth._pending.clear()


async def new_user(role=Role.adult):
    async with SessionLocal() as db:
        admin = (await db.execute(select(User).where(User.username == "admin"))).scalar_one()
        name = "security-" + uuid.uuid4().hex
        user = User(family_id=admin.family_id, username=name, email=name+"@example.com", display_name=name,
                    role=role, age_group=AgeGroup.kid if role == Role.child else AgeGroup.adult,
                    password_hash=hash_secret("security-password"), pin_hash=hash_secret("1234"))
        db.add(user)
        await db.commit()
        return user


async def new_session(user, **kwargs):
    async with SessionLocal() as db:
        sess = Session(user_id=user.id, expires_at=datetime.now(timezone.utc)+timedelta(hours=1),
                       mfa_verified=kwargs.pop("mfa_verified", True), **kwargs)
        db.add(sess)
        await db.commit()
        return sess.id


@pytest.fixture
def google_mock(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "google_client_id", "test-client")
    monkeypatch.setattr(settings, "google_client_secret", "test-secret")
    info = {"email":"admin@test.local", "email_verified":True, "sub":"test-google-sub"}
    class GoogleClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, *args, **kwargs):
            return SimpleNamespace(raise_for_status=lambda:None, json=lambda:{"access_token":"test"})
        async def get(self, *args, **kwargs):
            return SimpleNamespace(raise_for_status=lambda:None,
                json=lambda:info)
    monkeypatch.setattr(google_auth, "httpx", SimpleNamespace(AsyncClient=lambda **kwargs:GoogleClient()))
    return info


async def test_google_callback_cannot_be_transferred_to_another_browser(seeded, google_mock):
    async with make_client() as initiator, make_client() as victim:
        start = await initiator.get("/api/auth/google/start")
        state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
        response = await victim.get("/api/auth/google/callback", params={"state":state,"code":"valid"})
        assert SESSION_COOKIE not in response.cookies
        assert "non_valido" in response.headers["location"]
        good = await initiator.get("/api/auth/google/callback", params={"state":state,"code":"valid"})
        assert SESSION_COOKIE in good.cookies
        again = await initiator.get("/api/auth/google/callback", params={"state":state,"code":"valid"})
        assert SESSION_COOKIE not in again.cookies


async def test_promotion_invalidates_previous_pin_session(admin):
    user = await new_user(Role.child)
    async with SessionLocal() as db:
        dev=TrustedDevice(family_id=user.family_id,name="test",approved_by=user.id)
        db.add(dev);await db.commit()
    sid = await new_session(user, auth_method="pin", device_id=dev.id)
    async with make_client(sid) as child:
        assert (await child.get("/api/admin/members")).status_code == 403
        assert (await admin.patch(f"/api/admin/members/{user.id}", json={"role":"admin"})).status_code == 200
        assert (await child.get("/api/admin/members")).status_code == 401


def test_client_cannot_spoof_ip_with_untrusted_headers():
    request = Request({"type":"http","client":("203.0.113.1",5555),
                       "headers":[(b"x-real-ip",b"198.51.100.99"),(b"x-forwarded-for",b"198.51.100.99")]})
    assert client_ip(request) == "203.0.113.1"


@pytest.mark.parametrize("path,body", [
    ("/api/auth/login", {"email":"admin@test.local","password":"test-password"}),
    ("/api/auth/logout", {}),
    ("/api/auth/passkey/options", {}),
])
async def test_cross_origin_auth_mutations_rejected(seeded,path,body):
    async with make_client() as client:
        response = await client.post(path,json=body,headers={"Origin":"https://attacker.invalid"})
        assert response.status_code == 403


ADMIN_ROUTES = [(method.upper(),path) for path, methods in app.openapi()["paths"].items()
                if path.startswith("/api/admin/") and not path.endswith("/email/google/callback")
                for method in methods if method in {"get","post","put","patch","delete"}]
@pytest.mark.parametrize("method,path", ADMIN_ROUTES)
@pytest.mark.parametrize("actor", ["anna","kid","anonymous"])
async def test_non_admin_cannot_call_any_admin_route(seeded,actor,method,path):
    path = re.sub(r"\{[^}]+\}","unknown",path)
    async with make_client(None if actor == "anonymous" else await session_cookie(actor)) as client:
        response = await client.request(method,path,json={})
        assert response.status_code == (401 if actor == "anonymous" else 403), (method,path,response.text)


@pytest.mark.parametrize("app_id", ["wordcraft","gridcraft","pdfcraft","deckcraft","ideacraft","mailcraft"])
async def test_craft_gate_enforces_role_session_and_lock(seeded, app_id):
    for actor,expected in [(None,401),("kid",403),("bob",403),("anna",204)]:
        async with make_client(await session_cookie(actor) if actor else None) as client:
            assert (await client.get(f"/api/auth/apps/{app_id}")).status_code==expected
            if actor=="anna":
                assert (await client.get(f"/api/auth/apps/{app_id}",headers={"X-Forwarded-Method":"POST","Origin":"https://evil.example"})).status_code==403
                await client.post("/api/auth/lock")
                assert (await client.get(f"/api/auth/apps/{app_id}")).status_code==423


async def test_totp_cannot_be_replayed_and_rotates_pre_mfa_cookie(seeded):
    import time
    from app.security import totp
    from app.services.settings_store import encrypt
    user=await new_user()
    secret=totp.new_secret()
    async with SessionLocal() as db:
        u=await db.get(User,user.id);u.totp_secret=encrypt(secret);await db.commit()
    first=await new_session(user,mfa_verified=False)
    second=await new_session(user,mfa_verified=False)
    code=totp._code(secret,int(time.time()//30))
    async with make_client(first) as client:
        result=await client.post("/api/auth/totp/verify",json={"code":code})
        assert result.status_code==200
        assert result.cookies[SESSION_COOKIE]!=first
    async with make_client(first) as stolen:
        assert (await stolen.get("/api/files")).status_code==401
    async with make_client(second) as other:
        assert (await other.post("/api/auth/totp/verify",json={"code":code})).status_code==401
        assert (await other.get("/api/files")).status_code==401


async def test_email_reset_code_single_use_under_concurrent_requests(seeded):
    import asyncio
    from app.services import email_codes
    user=await new_user()
    async with SessionLocal() as db:
        code=await email_codes.issue(db,user,"reset")
    async def redeem():
        async with SessionLocal() as db:
            return await email_codes.verify(db,user,("reset",),code)
    assert sorted(await asyncio.gather(redeem(),redeem()))==[False,True]


async def test_recovery_code_single_use_under_concurrent_requests(seeded):
    import asyncio
    from app.security.passwords import generate_recovery_codes
    user=await new_user();plain,hashed=generate_recovery_codes()
    async with SessionLocal() as db:
        u=await db.get(User,user.id);u.recovery_codes=hashed;await db.commit()
    async def redeem():
        async with make_client(await new_session(user,mfa_verified=False)) as client:
            return (await client.post("/api/auth/recovery",json={"code":plain[0]})).status_code
    assert sorted(await asyncio.gather(redeem(),redeem()))==[200,401]


async def test_password_reset_revokes_sync_tokens_and_pairing_codes(seeded):
    from app.services import sync, email_codes
    user=await new_user()
    async with SessionLocal() as db:
        _,token=await sync.create_device(db,user,"pc","linux","test","127.0.0.1")
        pair,_=await sync.create_pairing_code(db,user)
        reset=await email_codes.issue(db,user,"reset")
    async with make_client() as client:
        assert (await client.post("/api/auth/reset",json={"email":user.email,"code":reset,"new_password":"changed-secure-password"})).status_code==200
        assert (await client.post("/api/sync/pair",json={"code":pair})).status_code==400
    async with make_client(token=token) as agent:
        assert (await agent.get("/api/sync/me")).status_code==401


async def test_sync_cannot_bypass_mandatory_mfa_enrollment(seeded,monkeypatch):
    user=await new_user()
    monkeypatch.setattr(get_settings(),"require_mfa_for_adults",True)
    monkeypatch.setattr(get_settings(),"mfa_optional","0")
    async with make_client() as client:
        result=await client.post("/api/sync/login",json={"email":user.email,"password":"security-password"})
        assert result.status_code==403
        assert "token" not in result.json()


async def test_google_does_not_bypass_existing_local_mfa(seeded,google_mock,monkeypatch):
    from unittest.mock import AsyncMock
    monkeypatch.setattr(google_auth,"user_has_second_factor",AsyncMock(return_value=True))
    async with make_client() as client:
        start=await client.get("/api/auth/google/start")
        state=parse_qs(urlparse(start.headers["location"]).query)["state"][0]
        result=await client.get("/api/auth/google/callback",params={"state":state,"code":"valid"})
        assert SESSION_COOKIE in result.cookies
        assert (await client.get("/api/admin/members")).status_code==401


async def test_gmail_callback_checks_browser_binding_and_current_admin_session(admin,monkeypatch):
    from app.api import admin as api
    monkeypatch.setattr(get_settings(),"google_client_id","test")
    monkeypatch.setattr(get_settings(),"google_client_secret","test")
    start=await admin.get("/api/admin/email/google/start")
    state=parse_qs(urlparse(start.json()["url"]).query)["state"][0]
    async with make_client() as outsider:
        result=await outsider.get("/api/admin/email/google/callback",params={"state":state,"code":"test"})
        assert "scaduto" in result.headers["location"]
    await admin.post("/api/auth/logout")
    result=await admin.get("/api/admin/email/google/callback",params={"state":state,"code":"test"})
    assert "scaduto" in result.headers["location"]


async def test_live_event_stream_rechecks_revocation_and_lock(seeded):
    from app.api.events import stream_authorized
    user=await new_user();sid=await new_session(user)
    request=Request({"type":"http","method":"GET","path":"/api/events","query_string":b"",
        "headers":[(b"cookie",f"{SESSION_COOKIE}={sid}".encode())],"client":("127.0.0.1",1234)})
    assert await stream_authorized(request)
    async with SessionLocal() as db:
        sess=await db.get(Session,sid);sess.screen_locked=True;await db.commit()
    assert not await stream_authorized(request)


@pytest.mark.parametrize("mutation", ["valid","origin","rp_id","challenge","no_uv","signature","user_handle"])
async def test_real_signed_passkey_rejects_tampering_and_replay(seeded,mutation):
    import base64, hashlib, json, os
    import cbor2
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives import hashes
    from app.models import WebAuthnCredential
    def b64(value): return base64.urlsafe_b64encode(value).decode().rstrip("=")
    user=await new_user();key=ec.generate_private_key(ec.SECP256R1());pub=key.public_key().public_numbers()
    raw_id=os.urandom(32)
    async with SessionLocal() as db:
        db.add(WebAuthnCredential(user_id=user.id,credential_id=raw_id,sign_count=0,
            public_key=cbor2.dumps({1:2,3:-7,-1:1,-2:pub.x.to_bytes(32,"big"),-3:pub.y.to_bytes(32,"big")})))
        await db.commit()
    async with make_client() as client:
        options=(await client.post("/api/auth/passkey/options")).json()
        data=json.dumps({"type":"webauthn.get", "challenge":b64(os.urandom(32)) if mutation=="challenge" else options["options"]["challenge"],
                         "origin":"https://evil.example" if mutation=="origin" else get_settings().webauthn_origin}).encode()
        rp="evil.example" if mutation=="rp_id" else get_settings().webauthn_rp_id
        auth=hashlib.sha256(rp.encode()).digest()+bytes([1 if mutation=="no_uv" else 5])+(1).to_bytes(4,"big")
        signature=key.sign(auth+hashlib.sha256(data).digest(),ec.ECDSA(hashes.SHA256()))
        credential={"id":b64(raw_id),"rawId":b64(raw_id),"type":"public-key","response":{
            "clientDataJSON":b64(data),"authenticatorData":b64(auth),
            "signature":b64(b"invalid" if mutation=="signature" else signature),
            "userHandle":b64(b"someone-else" if mutation=="user_handle" else user.id.encode())}}
        body={"key":options["key"],"credential":credential}
        result=await client.post("/api/auth/passkey/verify",json=body)
        assert result.status_code==(200 if mutation=="valid" else 401),result.text
        assert (await client.post("/api/auth/passkey/verify",json=body)).status_code==401


@pytest.mark.parametrize("member_type", ["symlink","hardlink","fifo","traversal"])
def test_backup_rejects_links_and_path_traversal(tmp_path,monkeypatch,member_type):
    import io,tarfile
    from app.services import backup
    stream=io.BytesIO()
    with tarfile.open(fileobj=stream,mode="w:gz") as archive:
        for name in ("manifest.json","familai.db"):
            archive.addfile(tarfile.TarInfo(name),io.BytesIO())
        bad=tarfile.TarInfo("../outside" if member_type=="traversal" else "uploads/evil")
        if member_type=="symlink": bad.type=tarfile.SYMTYPE;bad.linkname="/tmp"
        if member_type=="hardlink": bad.type=tarfile.LNKTYPE;bad.linkname="../../outside"
        if member_type=="fifo": bad.type=tarfile.FIFOTYPE
        archive.addfile(bad)
    monkeypatch.setattr(backup,"_open",lambda *args:stream.getvalue())
    monkeypatch.setattr(backup,"_pending_dir",lambda:tmp_path/"pending")
    with pytest.raises(ValueError):backup.stage(b"ignored","test")
    assert not (tmp_path/"pending").exists()


def test_invalid_backup_database_is_not_applied_on_next_start(tmp_path,monkeypatch):
    import io,tarfile,sqlite3
    from app.services import backup
    stream=io.BytesIO()
    with tarfile.open(fileobj=stream,mode="w:gz") as archive:
        for name,data in [("manifest.json",b'{"format":1}'),("familai.db",b"invalid database")]:
            item=tarfile.TarInfo(name);item.size=len(data);archive.addfile(item,io.BytesIO(data))
    monkeypatch.setattr(backup,"_open",lambda *args:stream.getvalue())
    monkeypatch.setattr(backup,"_pending_dir",lambda:tmp_path/"pending")
    with pytest.raises(sqlite3.Error):backup.stage(b"ignored","test")
    assert not (tmp_path/"pending").exists()
    assert backup.apply_pending() is False


async def test_audit_export_neutralizes_spreadsheet_formulas(admin):
    import csv,io
    from app.models import AuditLog
    async with SessionLocal() as db:
        user=(await db.execute(select(User).where(User.username=="admin"))).scalar_one()
        db.add(AuditLog(family_id=user.family_id,user_id=user.id,event="security_csv_test",detail="=1+1",ip="127.0.0.1"))
        await db.commit()
    response=await admin.get("/api/admin/audit.csv?event=security_csv_test")
    assert response.status_code==200
    rows=list(csv.reader(io.StringIO(response.text.lstrip("\ufeff")),delimiter=";"))
    assert rows[1][6]=="'=1+1"


async def test_arbitrary_code_execution_disabled_by_default(monkeypatch):
    from app.tools.code_exec import run_code
    from app.policy.permissions import tool_allowed
    monkeypatch.setattr(get_settings(),"allow_code_execution",False)
    assert not tool_allowed({"allowed_tools":["run_code"]},"run_code")
    result=await run_code({"code":"raise Exception('must not run')"},None)
    assert result.is_error


PUBLIC_PATHS = {
    "/api/health", "/api/branding", "/api/branding/icon/{size}", "/api/branding/manifest.webmanifest",
    "/api/auth/login", "/api/auth/logout", "/api/auth/pin-login", "/api/auth/device", "/api/auth/features",
    "/api/auth/forgot", "/api/auth/reset", "/api/auth/passkey/options", "/api/auth/passkey/verify",
    "/api/auth/google/start", "/api/auth/google/callback", "/api/admin/email/google/callback",
    "/api/sync/pair", "/api/sync/login", "/.well-known/apple-app-site-association", "/.well-known/assetlinks.json",
}
PRIVATE_ROUTES = [(method.upper(),path) for path,methods in app.openapi()["paths"].items()
                  if path not in PUBLIC_PATHS and not path.startswith("/api/admin/")
                  for method in methods if method in {"get","post","put","patch","delete"}]


@pytest.mark.parametrize("method,path",PRIVATE_ROUTES)
async def test_every_private_route_rejects_anonymous_requests(seeded,method,path):
    path=re.sub(r"\{[^}]+\}","1",path)
    async with make_client() as client:
        response=await client.request(method,path,json={})
        assert response.status_code==401,(method,path,response.status_code,response.text)


async def test_auth_cookies_and_password_session_fixation(seeded,monkeypatch):
    from app.security.sessions import create_session
    from fastapi import Response
    user=await new_user()
    monkeypatch.setattr(get_settings(),"cookie_secure",True)
    request=Request({"type":"http","method":"POST","path":"/api/auth/login",
        "headers":[(b"cookie",f"{SESSION_COOKIE}=attacker-selected-id".encode())],"client":("127.0.0.1",1234)})
    response=Response()
    async with SessionLocal() as db:
        sess=await create_session(db,response,user,request,mfa_verified=False)
        assert sess.id!="attacker-selected-id"
        assert sess.expires_at-datetime.now(timezone.utc)<timedelta(minutes=11)
    cookie=response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "secure" in cookie and "samesite=strict" in cookie


async def test_wrong_password_and_unknown_account_have_same_response(seeded):
    user=await new_user()
    async with make_client() as client:
        known=await client.post("/api/auth/login",json={"email":user.email,"password":"wrong"})
        unknown=await client.post("/api/auth/login",json={"email":"not-registered@example.com","password":"wrong"})
        assert known.status_code==unknown.status_code==401
        assert known.json()==unknown.json()


async def test_known_expired_and_disabled_reset_codes_cannot_restore_access(seeded):
    from app.services import email_codes
    from app.models import EmailToken
    user=await new_user()
    async with SessionLocal() as db:
        code=await email_codes.issue(db,user,"reset")
        token=(await db.execute(select(EmailToken).where(EmailToken.user_id==user.id))).scalar_one()
        token.expires_at=datetime.now(timezone.utc)-timedelta(seconds=1);await db.commit()
    async with make_client() as client:
        assert (await client.post("/api/auth/reset",json={"email":user.email,"code":code,"new_password":"not-accepted-password"})).status_code==400

    async with SessionLocal() as db:
        u=await db.get(User,user.id);u.is_active=False;await db.commit()
        code=await email_codes.issue(db,u,"reset")
    async with make_client() as client:
        assert (await client.post("/api/auth/reset",json={"email":user.email,"code":code,"new_password":"not-accepted-password"})).status_code==400


@pytest.mark.parametrize("invalid",[False,None,"true"])
async def test_google_requires_boolean_verified_email(seeded,google_mock,invalid):
    google_mock["email_verified"]=invalid
    async with make_client() as client:
        start=await client.get("/api/auth/google/start")
        state=parse_qs(urlparse(start.headers["location"]).query)["state"][0]
        result=await client.get("/api/auth/google/callback",params={"state":state,"code":"valid"})
        assert SESSION_COOKIE not in result.cookies
        assert "non_verificata" in result.headers["location"]


async def test_https_startup_rejects_default_secret(monkeypatch):
    from app.main import lifespan
    monkeypatch.setattr(get_settings(),"cookie_secure",True)
    monkeypatch.setattr(get_settings(),"secret_key","change-me-before-production")
    with pytest.raises(RuntimeError,match="SECRET_KEY"):
        async with lifespan(app):
            pytest.fail("Production must not start with the example secret")


@pytest.mark.parametrize("state,expected", [("expired",401),("unverified",401),("locked",423),("inactive",401)])
async def test_session_restrictions_apply_to_private_endpoints(seeded,state,expected):
    user=await new_user()
    sid=await new_session(user,mfa_verified=state!="unverified",screen_locked=state=="locked")
    async with SessionLocal() as db:
        if state=="expired":
            sess=await db.get(Session,sid);sess.expires_at=datetime.now(timezone.utc)-timedelta(seconds=1)
        if state=="inactive":
            u=await db.get(User,user.id);u.is_active=False
        await db.commit()
    async with make_client(sid) as client:
        for path in ("/api/files","/api/auth/sessions","/api/parental/children","/api/mail/session"):
            assert (await client.get(path)).status_code==expected
        assert (await client.put("/api/auth/prefs",json={"role":"admin"})).status_code==expected


async def test_pin_requires_trusted_device_and_never_authenticates_an_adult(seeded):
    child=await new_user(Role.child);adult=await new_user()
    async with make_client() as client:
        assert (await client.post("/api/auth/pin-login",json={"user_id":child.id,"pin":"1234"})).status_code==403
        async with SessionLocal() as db:
            dev=TrustedDevice(family_id=child.family_id,name="test",approved_by=adult.id)
            db.add(dev);await db.commit()
        client.cookies.set(DEVICE_COOKIE,dev.id)
        assert (await client.post("/api/auth/pin-login",json={"user_id":adult.id,"pin":"1234"})).status_code==401
        assert (await client.post("/api/auth/pin-login",json={"user_id":child.id,"pin":"1234"})).status_code==200


async def test_password_bruteforce_lockout_survives_ip_rotation(seeded):
    user=await new_user()
    async with make_client() as client:
        for n in range(5):
            response=await client.post("/api/auth/login",json={"email":user.email,"password":"incorrect"},
                                       headers={"X-Real-IP":f"198.51.100.{n}"})
            assert response.status_code==401
        response=await client.post("/api/auth/login",json={"email":user.email,"password":"security-password"})
        assert response.status_code==401
        assert SESSION_COOKIE not in response.cookies


async def test_logout_revokes_server_session(seeded):
    user=await new_user();sid=await new_session(user)
    async with make_client(sid) as client:
        assert (await client.post("/api/auth/logout")).status_code==200
    async with make_client(sid) as stolen:
        assert (await stolen.get("/api/files")).status_code==401


async def test_preferences_cannot_promote_or_change_parental_policy(kid):
    response=await kid.put("/api/auth/prefs",json={"role":"admin","policy":{"education_mode":"standard"}})
    assert response.status_code in (200,422)
    assert (await kid.get("/api/admin/members")).status_code==403
    assert (await kid.get("/api/auth/me")).json()["user"]["role"]=="child"
