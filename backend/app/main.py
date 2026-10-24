import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from .api import admin, approvals, artifacts, auth, chat, events, files, messaging, push, wellknown, native_app, folders, google_auth, projects, shares, tts, mail, sync
from .config import get_settings
from .db import SessionLocal, init_db
from .models import AgeGroup, Family, Role, User
from .security.passwords import hash_secret
from .services import settings_store
from .tools.mcp_client import mcp_manager

settings = get_settings()
logging.basicConfig(level=settings.log_level.upper())
log = logging.getLogger("familai")


async def bootstrap_admin() -> None:
    async with SessionLocal() as db:
        if (await db.execute(select(User.id).limit(1))).scalar_one_or_none():
            return
        if settings.cookie_secure and (len(settings.admin_password) < 12 or settings.admin_password == "change-me-before-production"):
            raise RuntimeError("Imposta ADMIN_PASSWORD personale di almeno 12 caratteri prima dell'avvio")
        fam = Family(name=settings.family_name)
        db.add(fam)
        await db.flush()
        db.add(User(family_id=fam.id, email=settings.admin_email.lower(), username="admin",
                    display_name=settings.admin_name, avatar="👨‍💻", role=Role.admin, age_group=AgeGroup.adult,
                    password_hash=hash_secret(settings.admin_password)))
        await db.commit()
        log.warning("Creato amministratore iniziale %s — cambia la password e registra una passkey!", settings.admin_email)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.cookie_secure and (len(settings.secret_key) < 32 or settings.secret_key == "change-me-before-production"):
        raise RuntimeError("Imposta SECRET_KEY casuale (almeno 32 caratteri) prima dell'avvio HTTPS")
    from .services import backup as _backup
    try:
        _backup.apply_pending()   # ripristino richiesto dal pannello: va fatto prima di aprire il DB
    except Exception:
        log.exception("Ripristino del backup fallito: continuo con i dati attuali")
    await init_db()
    await bootstrap_admin()
    async with SessionLocal() as db:
        await settings_store.apply(db)   # chiavi/modelli salvati dal pannello admin
    try:
        from .services import workspace as _ws
        async with SessionLocal() as db:
            n = await _ws.backfill_chat_folders(db)   # file di chat gia' esistenti -> Documenti > CHAT > <titolo>
            if n:
                log.info("Cartella CHAT: sistemati %d file di conversazioni esistenti", n)
    except Exception:
        log.exception("Sistemazione della cartella CHAT fallita (continuo)")
    from .api.installation import apply_runtime
    apply_runtime()
    await mcp_manager.start()
    from .services.security_alerts import worker
    from contextlib import suppress
    alerts = asyncio.create_task(worker())
    try:
        yield
    finally:
        alerts.cancel()
        with suppress(asyncio.CancelledError):
            await alerts
        await mcp_manager.stop()


from pathlib import Path as _P

_VERSION = next((p.read_text().strip() for p in (_P("VERSION"), _P("../VERSION"), _P("/app/VERSION")) if p.exists()), "dev")
app = FastAPI(title="FamilOS", version=_VERSION, lifespan=lifespan, docs_url="/api/docs", openapi_url="/api/openapi.json")

if not settings.cookie_secure:  # solo sviluppo: frontend Vite su un'altra porta
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173"], allow_credentials=True,
                       allow_methods=["*"], allow_headers=["*"])

from .services import activity as _activity  # noqa: E402

app.middleware("http")(_activity.middleware)   # registro attivita' (Organizzazione › Log)


@app.middleware("http")
async def private_response_cache(request, call_next):
    response = await call_next(request)
    if request.url.path.startswith(("/api/auth/", "/api/admin/", "/api/mail/")):
        response.headers["Cache-Control"] = "no-store, private"
    return response

for r in (auth.router, google_auth.router, chat.router, projects.router, shares.router, admin.router, artifacts.router, tts.router, approvals.router, mail.router, folders.router, push.router, native_app.router, messaging.router, events.router, sync.router):
    app.include_router(r, prefix="/api")
from .api import branding, installation, education, browser, parental, lockscreen, app_access, updates
app.include_router(updates.router, prefix="/api")
app.include_router(app_access.router, prefix="/api")
app.include_router(branding.router, prefix="/api")
app.include_router(education.router, prefix="/api")
app.include_router(browser.router, prefix="/api")
app.include_router(parental.router, prefix="/api")
app.include_router(lockscreen.router, prefix="/api")
app.include_router(installation.router, prefix="/api")
app.include_router(files.router, prefix="/api")   # /api/files (upload/lista)
app.include_router(files.router)                  # /files/{id} (download inline, dietro Caddy)
app.include_router(wellknown.router)              # /.well-known/* (app nativa: passkey e link nell'app)


@app.get("/api/health")
async def health():
    return {"status": "ok", "version": app.version}
