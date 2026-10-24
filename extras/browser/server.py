"""Ephemeral Chromium renderer. No direct Internet, host volumes or Docker socket.

Every HTTP request is fulfilled by FamilOS' authenticated public-network broker.
Never expose this service publicly. Each session owns a separate browser process.
"""
import asyncio
import base64
from contextlib import asynccontextmanager, suppress
import hmac
import os
import re
import time
from typing import Literal

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from playwright.async_api import async_playwright, Error as BrowserError

TOKEN = os.getenv("BROWSER_WORKER_TOKEN", "")
BROKER = os.getenv("BROWSER_BROKER_URL", "http://backend:8000/api/browser/gateway")
sessions = {}
creation = asyncio.Lock()
playwright = None


async def auth(authorization: str = Header(default="")):
    if len(TOKEN) < 32 or not hmac.compare_digest(authorization, "Bearer " + TOKEN):
        raise HTTPException(401)


async def reap():
    while True:
        await asyncio.sleep(30)
        for sid, session in list(sessions.items()):
            if time.monotonic() - session["last"] > 600:
                sessions.pop(sid, None)
                await session["browser"].close()


@asynccontextmanager
async def lifespan(app):
    global playwright
    if len(TOKEN) < 32:
        raise RuntimeError("Configurare BROWSER_WORKER_TOKEN con almeno 32 caratteri")
    os.makedirs(os.environ.get("HOME", "/tmp/browser"), exist_ok=True)
    playwright = await async_playwright().start()
    task = asyncio.create_task(reap())
    yield
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
    for session in sessions.values():
        await session["browser"].close()
    await playwright.stop()


app = FastAPI(lifespan=lifespan, docs_url=None, openapi_url=None)


class Start(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lease: str = Field(pattern=r"^[a-f0-9]{64}$")
    ticket: str = Field(min_length=40, max_length=100)


class Action(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["navigate", "back", "forward", "reload", "click", "scroll", "key", "text"]
    url: str = Field(default="", max_length=2000)
    text: str = Field(default="", max_length=4000)
    key: str = Field(default="", max_length=40)
    x: float = Field(default=0, ge=0, le=1280)
    y: float = Field(default=0, ge=0, le=800)
    delta: float = Field(default=0, ge=-1600, le=1600)
    expected_url: str | None = None


def get_session(sid):
    session = sessions.get(sid)
    if not session:
        raise HTTPException(410, "Sessione browser scaduta. Riapri il browser.")
    session["last"] = time.monotonic()
    return session


@app.get("/health", dependencies=[Depends(auth)])
async def health():
    return {"ready": True, "sessions": len(sessions)}


@app.post("/sessions", dependencies=[Depends(auth)])
async def start(body: Start):
    async with creation:
        if body.lease in sessions:
            raise HTTPException(409)
        if len(sessions) >= 3:
            raise HTTPException(429, "Sono già aperte tre sessioni browser. Chiudine una e riprova.")
        try:
            browser = await playwright.chromium.launch(executable_path="/usr/bin/chromium", headless=True,
                chromium_sandbox=True, args=["--disable-quic", "--disable-background-networking",
                    "--force-webrtc-ip-handling-policy=disable_non_proxied_udp"])
        except BrowserError:
            raise HTTPException(503, "Chromium non può avviare la sandbox. Verifica il profilo seccomp sulla VM.")
        try:
            context = await browser.new_context(viewport={"width": 1280, "height": 800}, locale="it-IT",
                service_workers="block", accept_downloads=False, permissions=[])
            session = {"browser": browser, "context": context, "last": time.monotonic(), "lock": asyncio.Lock(), "notice": "", "document_ok": False}
            async def route_request(route):
                request = route.request
                if request.is_navigation_request() and request.frame == session["page"].main_frame:
                    session["document_ok"] = False
                try:
                    if not request.url.startswith("https://"):
                        return await route.abort()
                    raw = request.post_data_buffer or b""
                    if len(raw) > 1024 * 1024:
                        return await route.abort()
                    async with httpx.AsyncClient(timeout=25, trust_env=False) as client:
                        result = await client.post(BROKER, headers={"X-Browser-Lease": body.lease, "X-Browser-Ticket": body.ticket}, json={
                            "url": request.url, "method": request.method, "headers": await request.all_headers(),
                            "body": base64.b64encode(raw).decode(), "document": request.is_navigation_request()})
                    if result.status_code != 200:
                        session["notice"] = "Una richiesta è stata bloccata dalle regole di navigazione o dal DNS."
                        return await route.abort()
                    data = result.json()
                    if request.is_navigation_request() and request.frame == session["page"].main_frame:
                        session["document_ok"] = 200 <= data["status"] < 300
                    await route.fulfill(status=data["status"], headers=data["headers"], body=base64.b64decode(data["body"]))
                except Exception:
                    session["notice"] = "Risorsa non disponibile. Il gateway potrebbe essere occupato o non raggiungibile."
                    with suppress(BrowserError):
                        await route.abort()
            await context.route("**/*", route_request)
            await context.route_web_socket("**/*", lambda ws: ws.close())
            page = await context.new_page()
            page.set_default_timeout(5000)
            async def popup(new_page):
                if new_page != page:
                    session["notice"] = "La finestra aggiuntiva è stata bloccata. Apri il collegamento in questa pagina."
                    await new_page.close()
            context.on("page", popup)
            page.on("dialog", lambda dialog: dialog.dismiss())
            page.on("download", lambda download: download.cancel())
            session["page"] = page
            sessions[body.lease] = session
        except Exception:
            await browser.close()
            raise
    return {"width": 1280, "height": 800}


@app.delete("/sessions/{sid}", dependencies=[Depends(auth)])
async def close(sid: str):
    session = sessions.pop(sid, None)
    if session:
        await session["browser"].close()
    return {"closed": True}


@app.post("/sessions/{sid}/action", dependencies=[Depends(auth)])
async def action(sid: str, body: Action):
    session = get_session(sid)
    async with session["lock"]:
        page = session["page"]
        if body.expected_url is not None and page.url != body.expected_url:
            raise HTTPException(409, "La pagina è cambiata. Attendi la verifica prima di interagire.")
        session["notice"] = ""
        try:
            if body.action in ("navigate", "back", "forward", "reload"):
                session["document_ok"] = False
            if body.action == "navigate":
                if not body.url.startswith("https://"):
                    raise HTTPException(400, "Solo indirizzi HTTPS.")
                await page.goto(body.url, wait_until="domcontentloaded", timeout=20000)
            elif body.action == "back":
                await page.go_back(wait_until="domcontentloaded", timeout=20000)
            elif body.action == "forward":
                await page.go_forward(wait_until="domcontentloaded", timeout=20000)
            elif body.action == "reload":
                await page.reload(wait_until="domcontentloaded", timeout=20000)
            elif body.action == "click":
                await page.mouse.click(body.x, body.y)
            elif body.action == "scroll":
                await page.mouse.move(body.x, body.y)
                await page.mouse.wheel(0, body.delta)
            elif body.action == "text":
                await page.keyboard.insert_text(body.text)
            elif body.action == "key":
                if body.key not in {"Enter", "Tab", "Shift+Tab", "Backspace", "Delete", "Escape", "ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End", "PageUp", "PageDown", "Control+a"}:
                    raise HTTPException(400, "Tasto non supportato")
                await page.keyboard.press(body.key)
        except BrowserError:
            session["notice"] = "La pagina non ha completato il caricamento. Verifica il sito o le regole di navigazione."
        return {"url": page.url, "notice": session["notice"], "document_ok": session["document_ok"]}


@app.get("/sessions/{sid}/location", dependencies=[Depends(auth)])
async def location(sid: str):
    return {"url": get_session(sid)["page"].url}


@app.get("/sessions/{sid}/frame", dependencies=[Depends(auth)])
async def frame(sid: str):
    session = get_session(sid)
    async with session["lock"]:
        page = session["page"]
        if page.url != "about:blank" and not page.url.startswith("https://"):
            raise HTTPException(403, "Questa destinazione non è consentita.")
        # Only pixels cross into the user's browser; website JS never executes in FamilOS' origin.
        raw = await page.screenshot(type="jpeg", quality=75, timeout=5000)
        text = await page.locator("body").inner_text(timeout=3000)
        return {"url": page.url, "title": await page.title(), "text": text[:12000],
                "image": base64.b64encode(raw).decode(), "width": 1280, "height": 800, "notice": session["notice"], "document_ok": session["document_ok"]}
