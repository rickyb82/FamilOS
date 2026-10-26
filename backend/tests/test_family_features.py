import asyncio,io,json,socket
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
import pytest
from PIL import Image
from fastapi import HTTPException
from sqlalchemy import select
from app.db import SessionLocal
from app.models import User,Role,AgeGroup
from app.policy.permissions import effective_policy,system_prompt_for
from app.policy import education
from app.providers.openai_provider import OpenAIProvider
from app.providers.openai_responses import cache_key,response_input
from app.providers.base import ToolSpec
from app.services import safe_browser
from app.api import branding
from conftest import make_client


async def test_lock_blocks_private_apis_and_password_unlock_restores_session(admin):
    file=await admin.post("/api/files",files={"file":("lock.txt",b"private","text/plain")})
    assert file.status_code==200
    assert (await admin.post("/api/auth/lock")).status_code==200
    assert (await admin.get("/api/auth/me")).json()["session_locked"] is True
    assert (await admin.get(file.json()["url"])).status_code==423
    assert (await admin.put("/api/auth/prefs",json={"appearance":{"theme":"dark"}})).status_code==423
    assert (await admin.post("/api/auth/unlock/password",json={"secret":"incorrect"})).status_code==401
    assert (await admin.get("/api/files")).status_code==423
    assert (await admin.post("/api/auth/unlock/password",json={"secret":"test-password"})).status_code==200
    assert (await admin.get(file.json()["url"])).content==b"private"


async def test_lock_passkey_requires_user_verification(admin,monkeypatch):
    from app.security import webauthn_svc
    verify=AsyncMock()
    monkeypatch.setattr(webauthn_svc,"authentication_verify",verify)
    monkeypatch.setattr(webauthn_svc,"authentication_options",AsyncMock(return_value={"challenge":"sample","userVerification":"preferred"}))
    await admin.post("/api/auth/lock")
    assert (await admin.post("/api/auth/unlock/passkey/options")).json()["userVerification"]=="required"
    assert (await admin.post("/api/auth/unlock/passkey/verify",json={"credential":{}})).status_code==200
    assert verify.call_args.kwargs["require_uv"] is True
    assert (await admin.get("/api/files")).status_code==200


async def test_appearance_is_personal_and_does_not_reset_tool_preferences(anna,bob):
    assert (await anna.put("/api/auth/prefs",json={"tools_off":["web_search"]})).status_code==200
    look={"theme":"dark","accent":"green","wallpaper":"forest","lock_after_minutes":15,"desktop_style":"oceano","effects":"reduced"}
    assert (await anna.put("/api/auth/prefs",json={"appearance":look})).status_code==200
    prefs=(await anna.get("/api/auth/me")).json()["prefs"]
    assert prefs["appearance"]==look and prefs["tools_off"]==["web_search"]
    assert (await bob.get("/api/auth/me")).json()["prefs"].get("appearance")!=look
    assert (await anna.put("/api/auth/prefs",json={"appearance":{"accent":"url(evil)"}})).status_code==422


async def test_personal_artwork_is_authenticated_and_isolated(anna,bob,kid,tmp_path,monkeypatch):
    monkeypatch.setattr(branding,"directory",lambda:tmp_path)
    image=io.BytesIO();Image.new("RGB",(80,80),"red").save(image,format="PNG")
    files={"logo":("logo.png",image.getvalue(),"image/png")}
    assert (await kid.post("/api/branding/profile",files=files)).status_code==200
    assert (await anna.get("/api/branding/profile")).json()["custom_logo"] is False
    assert (await bob.get("/api/branding/profile/icon/512")).status_code==404
    async with make_client() as anonymous:
        assert (await anonymous.get("/api/branding/profile/icon/512")).status_code==401
    assert (await anna.post("/api/branding/profile/wallpaper",files={"image":("wallpaper.png",image.getvalue(),"image/png")})).status_code==200
    assert (await anna.get("/api/branding/profile/wallpaper")).headers["content-type"]=="image/webp"
    assert (await bob.get("/api/branding/profile/wallpaper")).status_code==404


def child(mode_until=""):
    return User(id="child-test",family_id="family",username="child",display_name="Child",role=Role.child,
                age_group=AgeGroup.kid,policy={"tutor_override_until":mode_until},apps=[])


def test_tutor_only_for_children_and_parent_override_expires(tmp_path,monkeypatch):
    monkeypatch.setattr(education,"config_path",lambda:tmp_path/"rules.json")
    kid=child()
    assert effective_policy(kid)["education_mode"]=="tutor"
    from app.providers import registry
    monkeypatch.setattr(registry,"_providers",lambda:{"openai":object()})
    assert effective_policy(kid)["provider"]=="openai"   # cloud consentito e nessuna scelta del genitore: OpenAI
    monkeypatch.setattr(registry,"_providers",lambda:{})
    assert effective_policy(kid)["provider"]==""         # OpenAI non configurato: primo provider consentito disponibile
    kid.policy={"provider":"gemini","model":"gemini-3.8-flash"}
    assert (effective_policy(kid)["provider"],effective_policy(kid)["model"])==("gemini","gemini-3.8-flash")
    kid.policy={"tutor_override_until":""}
    assert "Tutor educativo" in system_prompt_for(kid)
    kid.policy={"tutor_override_until":(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()}
    assert effective_policy(kid)["education_mode"]=="standard"
    assert "genitore ha autorizzato risposte dirette" in system_prompt_for(kid)
    kid.policy={"tutor_override_until":(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()}
    assert effective_policy(kid)["education_mode"]=="tutor"
    adult=User(id="adult-test",family_id="family",username="adult",display_name="Adult",role=Role.adult,age_group=AgeGroup.adult,policy={},apps=[])
    assert "Tutor educativo" not in system_prompt_for(adult)
    assert "run_code" in effective_policy(adult)["allowed_tools"]


async def test_only_parents_can_temporarily_unlock_tutor(anna,kid):
    me=(await kid.get("/api/auth/me")).json()
    cid=me["user"]["id"]
    assert (await kid.put(f"/api/parental/children/{cid}/education",json={"mode":"standard"})).status_code==403
    assert (await anna.put(f"/api/parental/children/{cid}/education",json={"mode":"standard","duration":"hour"})).status_code==200
    assert (await kid.get("/api/auth/me")).json()["user"]["effective_policy"]["education_mode"]=="standard"
    assert (await anna.put(f"/api/parental/children/{cid}/education",json={"mode":"tutor"})).status_code==200
    assert (await kid.get("/api/auth/me")).json()["user"]["effective_policy"]["education_mode"]=="tutor"


async def test_responses_stream_records_cache_reads_writes_and_tools(monkeypatch):
    requests=[]
    async def create(**kwargs):
        requests.append(kwargs)
        async def events():
            yield NS(type="response.output_text.delta",delta="Ciao")
            yield NS(type="response.output_item.done",item=NS(type="function_call",call_id="call-one",name="read_document",arguments='{"file_id":"one"}'))
            yield NS(type="response.completed",response=NS(usage=NS(input_tokens=2000,output_tokens=30,input_tokens_details=NS(cached_tokens=1000,cache_write_tokens=600))))
        return events()
    provider=OpenAIProvider("test-key","gpt-6-luna")
    monkeypatch.setattr(provider,"_client",lambda:NS(responses=NS(create=create)))
    tools=[ToolSpec("read_document","read",{"type":"object","properties":{}})]
    events=[e async for e in provider.stream("gpt-6-luna","Stable instructions",[],tools,system_dynamic="Current time",cache_scope="family:user")]
    assert requests[0]["store"] is False
    assert requests[0]["extra_body"]["prompt_cache_options"]["ttl"]=="30m"
    assert next(e for e in events if e.type=="tool_use").tool_use["id"]=="call-one"
    assert events[-1].usage=={"input_tokens":400,"cache_read_tokens":1000,"cache_write_tokens":600,"output_tokens":30}
    assert cache_key("family:user","gpt-6-luna","a")==cache_key("family:user","gpt-6-luna","b")
    assert cache_key("family:other","gpt-6-luna","a")!=cache_key("family:user","gpt-6-luna","a")


@pytest.mark.parametrize("url",["https://127.0.0.1","http://www.nasa.gov","https://user:password@www.nasa.gov","https://www.nasa.gov:8000","https://localhost","https://www.nasa.gov:bad"])
def test_browser_rejects_unsafe_urls(url):
    with pytest.raises(HTTPException):safe_browser.normalize_url(url,["www.nasa.gov"])


async def test_browser_rejects_private_dns_and_pins_checked_address(monkeypatch):
    loop=asyncio.get_running_loop()
    monkeypatch.setattr(loop,"getaddrinfo",AsyncMock(return_value=[(socket.AF_INET,socket.SOCK_STREAM,6,"",("127.0.0.1",443))]))
    with pytest.raises(HTTPException):await safe_browser.resolve_public("www.nasa.gov",{})
    monkeypatch.setattr(safe_browser,"resolve_public",AsyncMock(return_value=["93.184.216.34"]))
    network=safe_browser.PublicNetwork({})
    connect=AsyncMock(return_value=object())
    network.backend.connect_tcp=connect
    await network.connect_tcp("www.nasa.gov",443)
    assert connect.call_args.args[0]=="93.184.216.34"


def test_reader_returns_plain_text_without_active_content():
    parser=safe_browser.ReaderParser("https://www.nasa.gov/")
    parser.feed('<html><title>Space</title><script>alert("bad")</script><form>private field</form><p>Learn about stars.</p><a href="/learn">Read more</a><iframe>bad</iframe></html>')
    title,text,links=parser.result()
    assert title=="Space" and "Learn about stars" in text
    assert "alert" not in text and "private field" not in text
    assert links[0]["url"]=="https://www.nasa.gov/learn"


async def test_child_cannot_change_browser_or_teaching_rules(kid):
    assert (await kid.get("/api/admin/browser")).status_code==403
    assert (await kid.put("/api/admin/browser",json={"sites":[]})).status_code==403
    assert (await kid.get("/api/admin/child-rules")).status_code==403


@pytest.mark.parametrize("style", ["aurora", "oceano", "tramonto"])
async def test_desktop_collections_persist(anna, style):
    response = await anna.put("/api/auth/prefs", json={"appearance": {"desktop_style": style, "wallpaper": style, "effects": "off"}})
    assert response.status_code == 200
    prefs = (await anna.get("/api/auth/me")).json()["prefs"]["appearance"]
    assert prefs["desktop_style"] == style and prefs["wallpaper"] == style
    assert prefs["effects"] == "off"
    assert (await anna.put("/api/auth/prefs", json={"appearance": {"desktop_style": "invalid"}})).status_code == 422
