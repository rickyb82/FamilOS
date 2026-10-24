from __future__ import annotations

import hashlib
import logging
import mimetypes
import re
import uuid
import time
from collections import OrderedDict
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import get_db
from ..models import Chunk, FileAsset, Role, Share
from ..services.naming import unique_filename
from ..policy.permissions import effective_policy
from ..schemas import FileOut
from ..security.sessions import AuthContext, require_auth
from ..services import rag, workspace
from ..services import sync as _sync
from ..services.access import require_access
from ..services.file_locks import serialize_file_write
from ..tools.documents import extract_text

settings = get_settings()
log = logging.getLogger(__name__)
router = APIRouter(tags=["files"])

# Existing Craft images pass the save URL through unchanged. A per-window revision
# cursor lets those images participate in optimistic saves without rebuilding WASM.
_editor_revisions: OrderedDict[tuple[str, str, str], tuple[str, float]] = OrderedDict()


def _editor_key(request: Request, user_id: str, file_id: str):
    token = request.query_params.get("edit_session", "")
    return (user_id, file_id, token) if re.fullmatch(r"[0-9a-f]{32}", token) else None


def _expected_revision(request: Request, user_id: str, file_id: str) -> str:
    explicit = request.headers.get("if-match", "").strip().strip('"')
    if explicit:
        return explicit
    key = _editor_key(request, user_id, file_id)
    initial = request.query_params.get("base_revision", "")
    if key is None or not re.fullmatch(r"new|[0-9a-f]{64}", initial):
        return ""
    cached = _editor_revisions.get(key)
    return cached[0] if cached and time.monotonic() - cached[1] < 86400 else initial


def _advance_editor(request: Request, user_id: str, file_id: str, sha: str) -> None:
    key = _editor_key(request, user_id, file_id)
    if key:
        _editor_revisions[key] = (sha, time.monotonic())
        _editor_revisions.move_to_end(key)
        while len(_editor_revisions) > 1024:
            _editor_revisions.popitem(last=False)

ALLOWED_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf", ".txt", ".md", ".csv", ".tsv", ".json", ".docx", ".xlsx",
               ".xlsm", ".xls", ".xlsb", ".ods", ".mp4", ".mp3", ".wav", ".m4a", ".py", ".js", ".ts", ".html", ".odt", ".rtf", ".doc", ".tex",
               ".pptx", ".potx", ".ppsx", ".deckcraft", ".mm", ".opml", ".xmind", ".mmd", ".drawio"}
# formati che gli editor nel browser (WordCraft / GridCraft) possono aprire e salvare
WORDCRAFT_EXT = {".docx", ".docm", ".dotx", ".doc", ".odt", ".rtf", ".txt", ".md", ".html", ".htm", ".tex", ".json", ".pdf", ".png"}
GRIDCRAFT_EXT = {".xlsx", ".xlsm", ".xls", ".xlsb", ".ods", ".csv", ".tsv", ".txt", ".json", ".html", ".pdf", ".png"}
DECKCRAFT_EXT = {".pptx", ".potx", ".ppsx", ".deckcraft", ".pdf", ".png", ".jpg", ".jpeg", ".txt"}
IDEACRAFT_EXT = {".json", ".mm", ".opml", ".md", ".txt", ".mmd", ".xmind", ".drawio"}
EDITOR_EXT = WORDCRAFT_EXT | GRIDCRAFT_EXT | DECKCRAFT_EXT | IDEACRAFT_EXT


def _out(f: FileAsset) -> FileOut:
    o = FileOut.model_validate(f)
    o.url = f"/files/{f.id}"
    return o


async def _scan_upload(path: Path) -> str:
    """Antivirus opzionale: UPLOAD_SCAN_CMD e' un comando che riceve il percorso del file (es. "clamdscan --no-summary" o
    "clamscan --no-summary"); esce con 0 = pulito, altro = infetto/errore (testo = motivo). Vuoto = nessun controllo."""
    cmd = (settings.upload_scan_cmd or "").strip()
    if not cmd:
        return ""
    import asyncio
    import shlex
    try:
        proc = await asyncio.create_subprocess_exec(*shlex.split(cmd), str(path), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=120)
        if proc.returncode == 0:
            return ""
        text = (out or b"").decode(errors="replace").strip().splitlines()
        return (text[-1] if text else f"codice {proc.returncode}")[:200]
    except Exception as e:
        log.warning("Controllo antivirus non eseguito su %s: %s", path, e)
        return "" if settings.upload_scan_fail_open else f"scanner non disponibile ({type(e).__name__})"


@router.post("/files", response_model=FileOut)
async def upload(background: BackgroundTasks, file: UploadFile = File(...), project_id: str | None = Form(None),
                 conversation_id: str | None = Form(None), folder_id: str | None = Form(None),
                 ctx: AuthContext = Depends(require_auth), db: AsyncSession = Depends(get_db)):
    pol = effective_policy(ctx.user)
    name = re.sub(r"[^\w.\- ]", "_", file.filename or "file")[:200]
    ext = Path(name).suffix.lower()
    # Gli adulti caricano qualsiasi tipo di file (anche .exe, .zip, .iso…): sono i loro documenti di lavoro. L'elenco dei tipi
    # vale solo per i bambini. Il controllo antivirus, se configurato (UPLOAD_SCAN_CMD, es. clamdscan), gira dopo la scrittura.
    if ctx.user.role == Role.child and ext not in ALLOWED_EXT:
        raise HTTPException(400, f"Tipo di file non consentito ({ext})")
    if project_id:
        await require_access(db, "project", project_id, ctx.user, write=True)
    if conversation_id:
        conv, _ = await require_access(db, "conversation", conversation_id, ctx.user, write=True)
        # allegato in una chat di progetto: appartiene anche al progetto (visibile ai membri, usato nel RAG di progetto)
        if not project_id and conv.project_id:
            project_id = conv.project_id
    data = await file.read()
    max_bytes = int(pol.get("max_upload_mb", 20)) * 1024 * 1024
    if len(data) > max_bytes:
        raise HTTPException(413, f"File troppo grande (max {pol.get('max_upload_mb')} MB)")
    from ..services import quota as _quota
    await _quota.ensure_space(db, ctx.user, len(data))
    fid = uuid.uuid4().hex
    path = settings.uploads_dir / f"{fid}{ext or '.bin'}"
    path.write_bytes(data)
    verdict = await _scan_upload(path)
    if verdict:
        path.unlink(missing_ok=True)
        raise HTTPException(422, f"File rifiutato dal controllo antivirus: {verdict}")
    mime = file.content_type or mimetypes.guess_type(name)[0] or "application/octet-stream"
    dest = await workspace.folder_for_write(db, folder_id, ctx.user)
    owner_id = dest.owner_id if dest is not None and workspace.is_org(dest) else ctx.user.id   # file dell'organizzazione: dell'archivio, non di chi li carica
    name = await unique_filename(db, owner_id, folder_id or None, name)
    asset = FileAsset(id=fid, family_id=ctx.user.family_id, owner_id=owner_id, project_id=project_id,
                      conversation_id=conversation_id, filename=name, mime=mime, size=len(data), path=str(path),
                      folder_id=folder_id or None)
    await workspace.place_in_conversation(db, asset)   # allegato di chat senza cartella: CHAT/<titolo della chat>
    if not mime.startswith(("image/", "video/", "audio/")):
        asset.extracted_text = extract_text(path, mime) or ""
    asset.index_status = "pending" if rag.is_indexable(asset) else "none"
    asset.sha256 = hashlib.sha256(data).hexdigest()
    db.add(asset)
    await _sync.touch_file(db, asset, content_changed=False)   # Craft Sync: compare nel prossimo /changes
    await db.commit()
    if asset.index_status == "pending":
        _schedule_reindex(asset.id)
    if dest is not None and workspace.is_org(dest):
        # nuovo documento di reparto: tutte le schede Documenti aperte dai colleghi si aggiornano
        from ..models import User as _User
        from ..services import events as _events
        for uid in (await db.execute(select(_User.id).where(_User.family_id == ctx.user.family_id, _User.id != ctx.user.id))).scalars().all():
            _events.publish(uid, "files", {"reason": "org_upload", "file_id": asset.id})
    return _out(asset)


_reindex_gen: dict[str, int] = {}


def _schedule_reindex(file_id: str, delay: float = 20.0) -> None:
    """L'AutoSave degli editor salva ogni pochi secondi: reindicizza per il RAG solo quando il file e' fermo da un po',
    cosi' un documento in scrittura non tiene occupato il modello di embedding a ogni battuta."""
    import asyncio

    gen = _reindex_gen.get(file_id, 0) + 1
    _reindex_gen[file_id] = gen

    async def later():
        await asyncio.sleep(delay)
        if _reindex_gen.get(file_id) == gen:   # nessun salvataggio piu' recente nel frattempo
            _reindex_gen.pop(file_id, None)
            await rag.index_file_background(file_id)

    asyncio.get_running_loop().create_task(later())


@router.post("/files/{fid}/reindex", response_model=FileOut)
async def reindex(fid: str, ctx: AuthContext = Depends(require_auth), db: AsyncSession = Depends(get_db)):
    asset, _ = await require_access(db, "file", fid, ctx.user)
    await rag.index_file(asset.id, db)
    await db.refresh(asset)
    return _out(asset)


class BlankPdf(BaseModel):
    filename: str = "documento.pdf"
    pages: int = 1
    project_id: str | None = None
    folder_id: str | None = None


@router.post("/files/blank-pdf", response_model=FileOut)
async def blank_pdf(body: BlankPdf, ctx: AuthContext = Depends(require_auth), db: AsyncSession = Depends(get_db)):
    """PDF vuoto (A4) da aprire subito in PdfCraft: e' il "nuovo PDF" della cartella APP."""
    from ..services.apps import app_enabled
    if ctx.user.role == Role.child and not app_enabled(ctx.user, "pdfcraft"):
        raise HTTPException(403, "App non abilitata per questo account")
    from pypdf import PdfWriter
    w = PdfWriter()
    for _ in range(max(1, min(int(body.pages or 1), 20))):
        w.add_blank_page(width=595.28, height=841.89)
    await workspace.folder_for_write(db, body.folder_id, ctx.user)
    nid = uuid.uuid4().hex
    path = settings.generated_dir / f"{nid}.pdf"
    settings.generated_dir.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as fh:
        w.write(fh)
    stem = re.sub(r"[^\w.\- ]", "_", body.filename.strip()).strip(". ") or "documento"
    stem = re.sub(r"\.pdf$", "", stem, flags=re.I) or "documento"
    fname = await unique_filename(db, ctx.user.id, body.folder_id or None, f"{stem}.pdf"[:200])
    asset = FileAsset(id=nid, family_id=ctx.user.family_id, owner_id=ctx.user.id, project_id=body.project_id,
                      filename=fname, mime="application/pdf", size=path.stat().st_size, path=str(path),
                      kind="generated", folder_id=body.folder_id or None)
    db.add(asset)
    await _sync.touch_file(db, asset)
    await db.commit()
    return _out(asset)


@router.post("/files/wordcraft-save", response_model=FileOut)
@serialize_file_write("file_id")
async def wordcraft_save(request: Request, background: BackgroundTasks, file_id: str, conversation_id: str | None = None,
                         project_id: str | None = None, filename: str | None = None, folder_id: str | None = None,
                         ctx: AuthContext = Depends(require_auth),
                         db: AsyncSession = Depends(get_db)):
    """Salvataggio dagli editor nel browser, WordCraft e GridCraft (File > Salva / Esporta): il corpo e' il file, il nome arriva
    nell'header X-Filename. `file_id` e' il documento aperto: se esiste e l'estensione coincide viene sovrascritto
    (e reindicizzato); se non esiste (documento nuovo: id generato dal client) viene creato con quell'id; se
    l'estensione e' diversa (es. esportazione in PDF) nasce un nuovo file accanto all'originale."""
    from urllib.parse import unquote

    if not re.fullmatch(r"[0-9a-f]{32}", file_id or ""):
        raise HTTPException(400, "file_id non valido")
    name = re.sub(r"[^\w.\- ]", "_", unquote(request.headers.get("X-Filename", "") or "documento.docx"))[:200]
    ext = Path(name).suffix.lower()
    existing = await db.get(FileAsset, file_id)
    if existing is not None:
        await require_access(db, "file", file_id, ctx.user, write=True)
        expected = _expected_revision(request, ctx.user.id, file_id)
        if not expected:
            raise HTTPException(428, "Riapri il documento: serve la versione di partenza per salvare")
        current = await _sync.ensure_sha(db, existing)
        if expected != current:
            raise HTTPException(412, "Il documento è stato modificato in un'altra finestra. Esporta una copia prima di riaprirlo.")
    if not ext:
        # nome senza estensione (PdfCraft lo ricava dall'URL): eredita quella del file aperto, altrimenti .docx
        ext = Path(existing.filename).suffix.lower() if existing else ".docx"
        name = f"{name}{ext}"
    if existing is not None and Path(existing.filename).suffix.lower() == ext:
        # il nome lo decide FamilAI (titolo dell'editor / elenco Documenti), non il nome interno dell'app
        name = existing.filename
    elif existing is None and filename:
        # documento nuovo: il nome scelto nella pagina Editor (l'estensione resta quella del contenuto salvato)
        stem = Path(re.sub(r"[^\w.\- ]", "_", filename)).stem.strip() or Path(name).stem
        name = f"{stem}{ext}"[:200]
    if ctx.user.role == Role.child:
        from ..services.apps import user_apps
        formats = {"wordcraft": WORDCRAFT_EXT, "gridcraft": GRIDCRAFT_EXT,
                   "deckcraft": DECKCRAFT_EXT, "ideacraft": IDEACRAFT_EXT, "pdfcraft": {".pdf", ".png", ".jpg", ".jpeg"}}
        if not any(ext in formats.get(app, set()) for app in user_apps(ctx.user)):
            raise HTTPException(403, "App non abilitata per questo formato")
    if ext not in EDITOR_EXT:
        raise HTTPException(400, f"Formato non consentito ({ext})")
    data = await request.body()
    log.info("editor-save: %s da %s (%d byte, file_id=%s)", name, ctx.user.username, len(data), file_id)
    max_bytes = int(effective_policy(ctx.user).get("max_upload_mb", 20)) * 1024 * 1024
    if len(data) > max_bytes:
        raise HTTPException(413, "File troppo grande")
    mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
    from ..services import quota as _quota
    await _quota.ensure_space(db, ctx.user, len(data) - (existing.size if existing is not None and Path(existing.filename).suffix.lower() == ext else 0))

    asset = existing
    if asset is not None:
        await require_access(db, "file", file_id, ctx.user, write=True)
        if Path(asset.filename).suffix.lower() != ext:
            # esportazione in un altro formato: nuovo file nella stessa chat/progetto
            nid = uuid.uuid4().hex
            path = settings.generated_dir / f"{nid}{ext}"
            path.write_bytes(data)
            name = await unique_filename(db, ctx.user.id, asset.folder_id, name)
            new = FileAsset(id=nid, family_id=ctx.user.family_id, owner_id=ctx.user.id, project_id=asset.project_id,
                            conversation_id=asset.conversation_id, filename=name, mime=mime, size=len(data),
                            path=str(path), kind="generated", folder_id=asset.folder_id, sha256=hashlib.sha256(data).hexdigest())
            db.add(new)
            await _sync.touch_file(db, new, content_changed=False)
            await db.commit()
            return _out(new)
        if not asset.path:
            raise HTTPException(400, "Questo file non e' modificabile")
        from ..services import versions as _versions
        await _versions.snapshot(db, asset, ctx.user, "editor")
        import os
        tmp = Path(f"{asset.path}.{uuid.uuid4().hex}.tmp")
        try:
            tmp.write_bytes(data)
            verdict = await _scan_upload(tmp)
            if verdict:
                raise HTTPException(422, f"File rifiutato dal controllo antivirus: {verdict}")
            os.replace(tmp, asset.path)
        finally:
            tmp.unlink(missing_ok=True)
        asset.filename, asset.mime, asset.size = name, mime, len(data)
        asset.sha256 = hashlib.sha256(data).hexdigest()
    else:
        if conversation_id:
            conv, _ = await require_access(db, "conversation", conversation_id, ctx.user, write=True)
            project_id = project_id or conv.project_id
        if project_id:
            await require_access(db, "project", project_id, ctx.user, write=True)
        await workspace.folder_for_write(db, folder_id, ctx.user)
        path = settings.generated_dir / f"{file_id}{ext}"
        path.write_bytes(data)
        name = await unique_filename(db, ctx.user.id, folder_id or None, name)
        asset = FileAsset(id=file_id, family_id=ctx.user.family_id, owner_id=ctx.user.id, project_id=project_id,
                          conversation_id=conversation_id, filename=name, mime=mime, size=len(data), path=str(path),
                          kind="generated", folder_id=folder_id or None, sha256=hashlib.sha256(data).hexdigest())
        await workspace.place_in_conversation(db, asset)
        db.add(asset)
        await _sync.touch_file(db, asset, content_changed=False)
    if not mime.startswith(("image/", "video/", "audio/")):
        asset.extracted_text = extract_text(Path(asset.path), mime) or ""
        asset.index_status = "pending"
        await db.execute(delete(Chunk).where(Chunk.file_id == asset.id))
    await db.commit()
    if asset.index_status == "pending":
        background.add_task(rag.index_file_background, asset.id)
    _advance_editor(request, ctx.user.id, file_id, asset.sha256 or "")
    return _out(asset)


class FileRename(BaseModel):
    filename: str | None = None
    folder_id: str | None = None   # "" = radice; spostamento in una cartella di Documenti (solo il proprietario)


@router.patch("/files/{fid}", response_model=FileOut)
async def rename_file(fid: str, body: FileRename, ctx: AuthContext = Depends(require_auth), db: AsyncSession = Depends(get_db)):
    """Rinomina un file (proprietario o permesso di scrittura). L'estensione resta quella originale."""
    asset, level = await require_access(db, "file", fid, ctx.user, write=True)
    if body.folder_id is not None:
        from ..models import Folder
        cur = await db.get(Folder, asset.folder_id) if asset.folder_id else None
        old_owner = asset.owner_id
        from ..services.apps import is_app_admin
        if asset.owner_id != ctx.user.id and not (workspace.is_org(cur) and is_app_admin(ctx.user, "archivio")):
            raise HTTPException(403, "Solo il proprietario puo' spostare il file nelle sue cartelle")
        if workspace.is_org(cur) and not is_app_admin(ctx.user, "archivio"):
            raise HTTPException(403, "I file dell'organizzazione li sposta solo l'amministratore")
        dest = await workspace.folder_for_write(db, body.folder_id, ctx.user)
        if dest is not None and workspace.is_org(dest):
            asset.owner_id = dest.owner_id        # entra nell'archivio: diventa dell'organizzazione
        elif workspace.is_org(cur):
            asset.owner_id = ctx.user.id          # esce dall'archivio: lo prende chi lo sposta (admin)
        asset.folder_id = body.folder_id or None
        asset.filename = await unique_filename(db, asset.owner_id, asset.folder_id, asset.filename, exclude_id=asset.id)
        await workspace.move_chunks(db, asset.id, asset.folder_id)
        await _sync.folder_left(db, asset, cur, old_owner, dest)   # Craft Sync: chi non lo vede piu' riceve una lapide
    if body.filename is not None:
        low = asset.filename.lower()
        old_ext = ".ideacraft.json" if low.endswith(".ideacraft.json") else Path(asset.filename).suffix
        clean = re.sub(r"[^\w.\- ]", "_", body.filename.strip())
        stem = re.sub(r"(\.ideacraft)?\.[A-Za-z0-9]{1,8}$", "", clean).strip(". ") if "." in clean else clean.strip(". ")
        if not stem:
            raise HTTPException(400, "Nome non valido")
        wanted = f"{stem}{old_ext}"[:200]
        if (await unique_filename(db, asset.owner_id, asset.folder_id, wanted, exclude_id=asset.id)) != wanted:
            raise HTTPException(409, f"Esiste gia' un file chiamato \"{wanted}\" in questa cartella")
        asset.filename = wanted
    await _sync.touch_file(db, asset, content_changed=False)
    await db.commit()
    return _out(asset)


@router.get("/files/rag-status")
async def rag_status(ctx: AuthContext = Depends(require_auth)):
    return {"embeddings": rag.embeddings_available(), "provider": settings.embedding_provider,
            "mode": "ibrida (semantica + parole chiave)" if rag.embeddings_available() else "solo parole chiave (BM25)"}


@router.get("/files", response_model=list[FileOut])
async def list_files(project_id: str | None = None, ctx: AuthContext = Depends(require_auth),
                     db: AsyncSession = Depends(get_db)):
    if project_id:
        await require_access(db, "project", project_id, ctx.user)
        rows = (await db.execute(select(FileAsset).where(FileAsset.project_id == project_id)
                                 .order_by(FileAsset.created_at.desc()))).scalars().all()
        from ..services.access import access_level
        out = []
        for f in rows:
            o = _out(f)
            o.access = "owner" if f.owner_id == ctx.user.id else (await access_level(db, "file", f, ctx.user) or "read")
            out.append(o)
        return out
    shared_ids = (await db.execute(select(Share.resource_id).where(
        Share.resource_type == "file", Share.family_id == ctx.user.family_id,
        or_(Share.target_user_id == ctx.user.id, Share.target_user_id.is_(None))))).scalars().all()
    org_ids = [f.id for f in await workspace.visible_org_folders(db, ctx.user)]
    rows = (await db.execute(select(FileAsset).where(
        FileAsset.family_id == ctx.user.family_id,
        or_(FileAsset.owner_id == ctx.user.id, FileAsset.id.in_(shared_ids or ["-"]), FileAsset.folder_id.in_(org_ids or ["-"]))
    ).order_by(FileAsset.created_at.desc()).limit(600))).scalars().all()
    from ..models import User as _U
    from ..services.access import access_level
    names = dict((await db.execute(select(_U.id, _U.display_name).where(_U.family_id == ctx.user.family_id))).all())
    out = []
    for f in rows:
        o = _out(f)
        o.access = "owner" if f.owner_id == ctx.user.id else (await access_level(db, "file", f, ctx.user) or "read")
        o.owner_name = names.get(f.owner_id, "")
        out.append(o)
    return out


@router.get("/files/{fid}")
async def download(fid: str, ctx: AuthContext = Depends(require_auth), db: AsyncSession = Depends(get_db)):
    asset, _ = await require_access(db, "file", fid, ctx.user)
    if not asset.path:   # file virtuale (es. archivio cronologia chat): serviamo il testo
        from fastapi.responses import PlainTextResponse
        return PlainTextResponse(asset.extracted_text or "", headers={"Content-Disposition": f'inline; filename="{asset.id}.txt"'})
    # file generati in una conversazione: accessibili a chi vede la conversazione.
    # no-store: i documenti vengono riscritti dagli editor, il browser non deve riproporre una copia vecchia.
    # inline solo i tipi che il browser mostra senza eseguire nulla (immagini raster, PDF, audio/video, testo semplice):
    # tutto il resto (HTML, SVG, eseguibili, archivi…) viene scaricato come allegato e mai interpretato nell'origine di Emily
    mime = asset.mime or "application/octet-stream"
    low = asset.filename.lower()
    raster = {"image/png", "image/jpeg", "image/gif", "image/webp", "image/avif", "image/bmp"}
    inline = mime in raster or mime.startswith(("video/", "audio/")) \
        or mime == "application/pdf" or mime in ("text/plain", "text/csv", "text/markdown")
    headers = {"Cache-Control": "no-store, private", "X-Content-Type-Options": "nosniff",
               "Content-Security-Policy": "sandbox; default-src 'none'; style-src 'unsafe-inline'"}
    if not inline:
        mime = "application/octet-stream"
    if low.endswith((".html", ".htm", ".xhtml", ".js", ".mjs")):
        inline, mime = False, "application/octet-stream"
    elif low.endswith((".svg", ".svgz")):
        # le mappe IdeaCraft sono SVG mostrati con <img> (dove gli script non girano comunque): tipo corretto, ma aperto
        # direttamente si scarica e una CSP sandbox spegne gli script
        inline, mime = False, "image/svg+xml"
        headers["Content-Security-Policy"] = "sandbox; script-src 'none'"
    return FileResponse(asset.path, media_type=mime, filename=asset.filename,
                        content_disposition_type="inline" if inline else "attachment", headers=headers)


@router.get("/files/{fid}/metadata", response_model=FileOut)
async def file_metadata(fid: str, ctx: AuthContext = Depends(require_auth), db: AsyncSession = Depends(get_db)):
    asset, level = await require_access(db, "file", fid, ctx.user)
    await _sync.ensure_sha(db, asset)
    out = _out(asset)
    out.access = level
    return out


@router.delete("/files/{fid}")
async def delete_file(fid: str, ctx: AuthContext = Depends(require_auth), db: AsyncSession = Depends(get_db)):
    asset, level = await require_access(db, "file", fid, ctx.user)
    if level not in ("owner", "parent", "write") or (level == "write" and not await workspace.protected_template(db, asset)):
        raise HTTPException(403)
    if asset.path:
        Path(asset.path).unlink(missing_ok=True)
    await db.execute(delete(Chunk).where(Chunk.file_id == asset.id))
    from ..models import Folder as _Folder
    _fo = await db.get(_Folder, asset.folder_id) if asset.folder_id else None
    await _sync.tombstone(db, "file", asset.id, asset.family_id, asset.owner_id, workspace.is_org(_fo), (_fo.dept if _fo else "") or "")
    await db.delete(asset)
    await db.commit()
    return {"status": "ok"}


# ---------------------------------------------------------------- cronologia delle versioni ----
@router.get("/files/{fid}/versions")
async def file_versions(fid: str, ctx: AuthContext = Depends(require_auth), db: AsyncSession = Depends(get_db)):
    """Le versioni precedenti del file (chi, quando, quanto), dalla piu' recente."""
    from ..services import versions as _versions
    asset, _ = await require_access(db, "file", fid, ctx.user)
    rows = await _versions.list_versions(db, asset.id)
    return [{"id": v.id, "created_at": v.created_at, "author": v.author_name, "note": v.note, "size": v.size,
             "url": f"/files/{asset.id}/versions/{v.id}"} for v in rows]


@router.get("/files/{fid}/versions/{vid}")
async def file_version_download(fid: str, vid: str, ctx: AuthContext = Depends(require_auth), db: AsyncSession = Depends(get_db)):
    from ..models import FileVersion
    asset, _ = await require_access(db, "file", fid, ctx.user)
    v = await db.get(FileVersion, vid)
    if not v or v.file_id != asset.id or not Path(v.path).is_file():
        raise HTTPException(404, "Versione non trovata")
    stem, ext = Path(asset.filename).stem, Path(asset.filename).suffix
    return FileResponse(v.path, media_type=asset.mime, filename=f"{stem} ({v.created_at.strftime('%Y-%m-%d %H%M')}){ext}",
                        headers={"Cache-Control": "no-store"})


@router.post("/files/{fid}/versions/{vid}/restore", response_model=FileOut)
@serialize_file_write("fid")
async def file_version_restore(fid: str, vid: str, background: BackgroundTasks, ctx: AuthContext = Depends(require_auth), db: AsyncSession = Depends(get_db)):
    from ..models import FileVersion
    from ..services import versions as _versions
    asset, _ = await require_access(db, "file", fid, ctx.user, write=True)
    v = await db.get(FileVersion, vid)
    if not v or v.file_id != asset.id or not Path(v.path).is_file():
        raise HTTPException(404, "Versione non trovata")
    await _versions.restore(db, asset, v, ctx.user)
    asset.extracted_text = None
    await db.commit()
    _schedule_reindex(asset.id)
    return _out(asset)
