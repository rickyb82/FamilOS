"""Documenti e fogli di calcolo con le app ArtCraft: WordCraft (word processor) e GridCraft (foglio di calcolo),
open source in Rust, ognuna con il proprio server MCP (https://github.com/rickyb82/wordcraft, .../gridcraft).

Le app girano nei container `wordcraft` / `gridcraft` (extras/artcraft) ed espongono il server MCP; il client MCP di
FamilAI le registra come server "wordcraft" e "gridcraft" (tool mcp__wordcraft__*, mcp__gridcraft__*). Qui ci sono i
tool "comodi" che fanno il lavoro piu' comune in un colpo solo e registrano il risultato come file scaricabile in chat:
  - write_document     Markdown -> .docx/.pdf/.odt… (o conversione di un allegato)          [WordCraft]
  - write_spreadsheet  tabelle di valori/formule -> .xlsx/.csv… con tabella, formati, grafici [GridCraft]
I file passano dalle cartelle condivise <DATA_DIR>/wordcraft e <DATA_DIR>/gridcraft (stesso percorso nei container).
"""
from __future__ import annotations

import logging
import mimetypes
import re
import uuid
from pathlib import Path

from ..config import get_settings
from ..models import FileAsset
from .registry import ToolContext, ToolResult, register, unregister_prefix

settings = get_settings()
log = logging.getLogger(__name__)

WORDCRAFT, GRIDCRAFT, PDFCRAFT, DECKCRAFT, IDEACRAFT = "wordcraft", "gridcraft", "pdfcraft", "deckcraft", "ideacraft"
# save_tools: tool MCP il cui argomento `path` e' un file scritto (registrato come allegato); vuoto = i percorsi si
# ricavano dal risultato (PdfCraft).
APPS = {
    WORDCRAFT: {"save_tools": ("save_document",), "native_tool": "write_document", "formats": ("docx", "pdf", "odt", "rtf", "html", "md", "txt")},
    GRIDCRAFT: {"save_tools": ("save_workbook",), "native_tool": "write_spreadsheet", "formats": ("xlsx", "csv", "tsv", "json", "html")},
    PDFCRAFT: {"save_tools": (), "native_tool": "pdf_tool", "formats": ("pdf",)},
    DECKCRAFT: {"save_tools": ("save_presentation", "export"), "native_tool": "write_presentation", "formats": ("pptx", "pdf", "deckcraft")},
    IDEACRAFT: {"save_tools": ("save_map",), "native_tool": "mind_map", "formats": ("svg", "json", "mm", "opml", "md", "drawio")},
}
# app senza MCP servite via HTTP (cli-server.py): nessuna al momento, il meccanismo resta per quelle future
HTTP_APPS = {a for a, cfg in APPS.items() if cfg.get("kind") == "http"}


def app_dir(app: str) -> Path:
    p = settings.data_dir / app
    p.mkdir(parents=True, exist_ok=True)
    return p


def host_for(app: str) -> str:
    return {WORDCRAFT: settings.wordcraft_host, GRIDCRAFT: settings.gridcraft_host, PDFCRAFT: settings.pdfcraft_host,
            DECKCRAFT: settings.deckcraft_host, IDEACRAFT: settings.ideacraft_host}.get(app, "")


async def cli_run(app: str, cmd: str, args: list[str], files: dict[str, bytes] | None = None,
                  outputs: list[str] | None = None, timeout: float = 120) -> dict:
    """Esegue la CLI di un'app 'http' nel suo container (cli-server.py) e restituisce exit/stdout/stderr/file."""
    import base64

    import httpx

    host = host_for(app)
    if not host:
        raise RuntimeError(f"{app} non configurato")
    body = {"cmd": cmd, "args": args, "files": {k: base64.b64encode(v).decode() for k, v in (files or {}).items()},
            "outputs": outputs or []}
    async with httpx.AsyncClient(timeout=timeout + 10) as c:
        r = await c.post(f"http://{host}/run", json=body)
    data = r.json()
    if r.status_code != 200:
        raise RuntimeError(data.get("error") or f"HTTP {r.status_code}")
    data["files"] = {k: base64.b64decode(v) for k, v in (data.get("files") or {}).items()}
    return data


async def http_app_ok(app: str) -> bool:
    import httpx

    host = host_for(app)
    if not host:
        return False
    try:
        async with httpx.AsyncClient(timeout=3) as c:
            return (await c.get(f"http://{host}/health")).status_code == 200
    except Exception:
        return False


def harvest_paths(result_text: str) -> list[str]:
    """Percorsi di file che un tool MCP dichiara di aver scritto (campi path/out/paths/files/parts nel JSON del risultato)
    dentro una cartella delle app: diventano allegati della chat. Usato per PdfCraft, dove molti tool producono file."""
    import json

    found: list[str] = []
    try:
        data = json.loads(result_text)
    except Exception:
        return found
    dirs = [str(app_dir(a).resolve()) for a in APPS]

    def walk(v, key=""):
        if isinstance(v, dict):
            for k, x in v.items():
                walk(x, k)
        elif isinstance(v, list):
            for x in v:
                walk(x, key)
        elif isinstance(v, str) and key in ("path", "out", "paths", "files", "parts", "file", "folder", "out_dir", "written"):
            try:
                rp = str(Path(v).resolve())
            except Exception:
                return
            if any(rp.startswith(d + "/") for d in dirs) and rp not in found:
                found.append(rp)
    walk(data)
    out: list[str] = []
    for f in found:
        p = Path(f)
        if p.is_file():
            out.append(f)
        elif p.is_dir():   # cartella di output (es. doc_split, doc_export_images): tutti i file dentro
            out.extend(str(x) for x in sorted(p.iterdir()) if x.is_file())
    return out[:40]


def _safe_name(name: str, ext: str) -> str:
    base = re.sub(r"[^\w.\- ]", "_", (name or "documento").strip())[:80].rstrip(". ") or "documento"
    if base.lower().endswith("." + ext):
        base = base[: -(len(ext) + 1)]
    return f"{base}.{ext}"


def _unique(path: Path) -> Path:
    from ..services.naming import unique_path
    return unique_path(path)


def sidecar_files(path: str) -> list[str]:
    """File da registrare accanto a quello salvato: per un SVG di IdeaCraft anche un PNG (WordCraft/DeckCraft inseriscono
    immagini raster; l'SVG resta per l'editor e il web)."""
    if not path or not path.lower().endswith(".svg"):
        return []
    png = path[:-4] + ".png"
    try:
        import cairosvg

        cairosvg.svg2png(url=path, write_to=png, output_width=1600, background_color="white")
        return [png]
    except Exception as e:   # cairosvg assente o SVG non convertibile: resta solo l'SVG
        log.info("PNG da %s non generato: %s", path, e)
        return []


async def register_saved_file(ctx: ToolContext, path: str, only_new: bool = False) -> dict | None:
    """Un file salvato da un'app nella sua cartella <DATA_DIR>/<app> diventa un FileAsset della chat (scaricabile).
    Con only_new, un file gia' registrato viene aggiornato (dimensione) ma non restituito (niente allegati doppi in chat)."""
    if not path:
        return None
    p = Path(path)
    try:
        if not p.is_file():
            return None
        parents = p.resolve().parents
        extra = [(settings.data_dir / d).resolve() for d in ("mailcraft",)]   # posta: cartella non-artcraft
        if not any(app_dir(a).resolve() in parents for a in APPS) and not any(d in parents for d in extra):
            return None
    except OSError:
        return None
    mime = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
    from sqlalchemy import select
    existing = (await ctx.db.execute(select(FileAsset).where(FileAsset.path == str(p), FileAsset.conversation_id == ctx.conversation_id))).scalars().first()
    if existing:   # stesso file risalvato: aggiorna la voce invece di duplicarla
        existing.size, existing.filename, existing.mime = p.stat().st_size, p.name, mime
        asset = existing
        if only_new:
            await ctx.db.commit()
            return None
    else:
        from ..services.naming import unique_filename
        shown = await unique_filename(ctx.db, ctx.user.id, None, p.name)   # in Documenti il nome non si ripete
        asset = FileAsset(id=uuid.uuid4().hex, family_id=ctx.user.family_id, owner_id=ctx.user.id, project_id=ctx.project_id,
                          conversation_id=ctx.conversation_id, filename=shown, mime=mime, size=p.stat().st_size,
                          path=str(p), kind="generated")
        from ..services import workspace as _ws
        await _ws.place_in_conversation(ctx.db, asset)   # CHAT/<titolo della chat>
        ctx.db.add(asset)
    await ctx.db.commit()
    from ..services import events as _events
    _events.publish(ctx.user.id, "files", {"reason": "generated", "file_id": asset.id})   # la pagina Documenti si aggiorna da sola
    if p.suffix.lower() in (".png", ".svg", ".jpg", ".jpeg", ".webp"):
        return {"type": "image", "file_id": asset.id, "url": f"/files/{asset.id}", "mime": mime}
    return {"type": "file", "file_id": asset.id, "url": f"/files/{asset.id}", "filename": p.name, "mime": mime}


async def _source_asset(ctx: ToolContext, src_id: str) -> FileAsset | ToolResult:
    asset = await ctx.db.get(FileAsset, src_id)
    if not asset or asset.family_id != ctx.user.family_id:
        return ToolResult(f"Allegato {src_id} non trovato.", is_error=True)
    if not asset.path or not Path(asset.path).is_file():
        return ToolResult("Il file dell'allegato non e' piu' disponibile.", is_error=True)
    return asset


def _done(out: Path, art: dict, app_label: str) -> ToolResult:
    return ToolResult(f"File {out.name} creato con {app_label} e mostrato all'utente come allegato scaricabile "
                      f"(id {art['file_id']}). Non inventare link: il file e' gia' in chat.", artifacts=[art])


# ---------------------------------------------------------------- WordCraft: write_document ----
async def write_document(args: dict, ctx: ToolContext) -> ToolResult:
    from .mcp_client import mcp_manager

    if not mcp_manager.is_connected(WORDCRAFT):
        return ToolResult("WordCraft non e' attivo su questo server (./familai.sh wordcraft up).", is_error=True)
    fmt = str(args.get("format") or "docx").lower().lstrip(".")
    if fmt not in APPS[WORDCRAFT]["formats"]:
        return ToolResult(f"Formato non supportato: {fmt}. Usa uno tra {', '.join(APPS[WORDCRAFT]['formats'])}.", is_error=True)
    title = str(args.get("title") or args.get("filename") or "documento")
    out = _unique(app_dir(WORDCRAFT) / _safe_name(str(args.get("filename") or title), fmt))
    src_id = str(args.get("source_file_id") or "").strip()
    content = str(args.get("content") or "")
    tmp: Path | None = None
    try:
        if src_id:
            asset = await _source_asset(ctx, src_id)
            if isinstance(asset, ToolResult):
                return asset
            await mcp_manager.call(WORDCRAFT, "open_document", {"path": asset.path})
            if content.strip():
                await mcp_manager.call(WORDCRAFT, "execute", {"command": "caret.docEnd", "params": {}})
                await mcp_manager.call(WORDCRAFT, "type_text", {"text": "\n" + content, "paragraphs": True})
        else:
            if not content.strip():
                return ToolResult("Serve il contenuto del documento (Markdown) oppure source_file_id.", is_error=True)
            tmp = app_dir(WORDCRAFT) / f".tmp-{uuid.uuid4().hex}.md"
            md = content if content.lstrip().startswith("#") or not title else f"# {title}\n\n{content}"
            tmp.write_text(md, encoding="utf-8")
            await mcp_manager.call(WORDCRAFT, "open_document", {"path": str(tmp)})
        await mcp_manager.call(WORDCRAFT, "save_document", {"path": str(out)})
    except Exception as e:
        log.warning("write_document fallito: %s", e)
        return ToolResult(f"WordCraft: {type(e).__name__}: {str(e)[:400]}", is_error=True)
    finally:
        if tmp:
            tmp.unlink(missing_ok=True)
    art = await register_saved_file(ctx, str(out))
    return _done(out, art, "WordCraft") if art else ToolResult("WordCraft non ha prodotto il file.", is_error=True)


# ---------------------------------------------------------------- GridCraft: write_spreadsheet ----
def _col(n: int) -> str:
    s = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


async def write_spreadsheet(args: dict, ctx: ToolContext) -> ToolResult:
    from .mcp_client import mcp_manager

    if not mcp_manager.is_connected(GRIDCRAFT):
        return ToolResult("GridCraft non e' attivo su questo server (./familai.sh gridcraft up).", is_error=True)
    fmt = str(args.get("format") or "xlsx").lower().lstrip(".")
    if fmt not in APPS[GRIDCRAFT]["formats"]:
        return ToolResult(f"Formato non supportato: {fmt}. Usa uno tra {', '.join(APPS[GRIDCRAFT]['formats'])}.", is_error=True)
    title = str(args.get("title") or args.get("filename") or "foglio")
    out = _unique(app_dir(GRIDCRAFT) / _safe_name(str(args.get("filename") or title), fmt))
    src_id = str(args.get("source_file_id") or "").strip()
    sheets = args.get("sheets") or []
    if args.get("values") and not sheets:
        sheets = [{"values": args["values"]}]
    notes: list[str] = []
    try:
        if src_id:
            asset = await _source_asset(ctx, src_id)
            if isinstance(asset, ToolResult):
                return asset
            await mcp_manager.call(GRIDCRAFT, "open_workbook", {"path": asset.path})
        else:
            if not sheets:
                return ToolResult("Servono i dati (values o sheets) oppure source_file_id.", is_error=True)
            await mcp_manager.call(GRIDCRAFT, "new_workbook", {})
        for i, sh in enumerate(sheets):
            if not isinstance(sh, dict):
                continue
            if i > 0 and not src_id:
                try:
                    await mcp_manager.call(GRIDCRAFT, "execute_command", {"command": "home.insertSheet", "params": {}})
                except Exception as e:
                    notes.append(f"foglio {i + 1} non aggiunto ({str(e)[:80]})")
            if sh.get("name"):
                try:
                    await mcp_manager.call(GRIDCRAFT, "execute_command", {"command": "sheet.rename", "params": {"name": str(sh["name"])}})
                except Exception:
                    notes.append(f"nome foglio '{sh['name']}' non applicato")
            values = sh.get("values") or []
            start = str(sh.get("range") or "A1")
            if values:
                await mcp_manager.call(GRIDCRAFT, "write_range", {"range": start, "values": values})
                m = re.match(r"([A-Za-z]+)(\d+)$", start.split("!")[-1])
                if m and (sh.get("table", True) or sh.get("header_bold", True)):
                    c0 = sum((ord(ch) - 64) * 26 ** k for k, ch in enumerate(reversed(m.group(1).upper())))
                    r0 = int(m.group(2))
                    width = max(len(r) for r in values if isinstance(r, list)) if values else 0
                    end = f"{_col(c0 + width - 1)}{r0 + len(values) - 1}"
                    rng = f"{start}:{end}"
                    if sh.get("table", True) and len(values) > 1:
                        try:
                            await mcp_manager.call(GRIDCRAFT, "create_table", {"range": rng, "style": str(sh.get("table_style") or "TableStyleMedium2")})
                        except Exception as e:
                            notes.append(f"tabella non creata ({str(e)[:80]})")
                            try:
                                await mcp_manager.call(GRIDCRAFT, "format_range", {"range": f"{start}:{_col(c0 + width - 1)}{r0}", "bold": True})
                            except Exception:
                                pass
            for f in sh.get("formats") or []:
                if isinstance(f, dict) and f.get("range"):
                    try:
                        await mcp_manager.call(GRIDCRAFT, "format_range", f)
                    except Exception as e:
                        notes.append(f"formato {f.get('range')} non applicato ({str(e)[:60]})")
            for ch in sh.get("charts") or []:
                if isinstance(ch, dict) and ch.get("range"):
                    try:
                        await mcp_manager.call(GRIDCRAFT, "insert_chart", {k: v for k, v in ch.items() if k in ("range", "type", "subtype", "title", "at")} | {"type": ch.get("type") or "column"})
                    except Exception as e:
                        notes.append(f"grafico {ch.get('range')} non inserito ({str(e)[:60]})")
        await mcp_manager.call(GRIDCRAFT, "save_workbook", {"path": str(out)})
    except Exception as e:
        log.warning("write_spreadsheet fallito: %s", e)
        return ToolResult(f"GridCraft: {type(e).__name__}: {str(e)[:400]}", is_error=True)
    art = await register_saved_file(ctx, str(out))
    if not art:
        return ToolResult("GridCraft non ha prodotto il file.", is_error=True)
    res = _done(out, art, "GridCraft")
    if notes:
        res.content += " Note: " + "; ".join(notes) + "."
    return res


# ---------------------------------------------------------------- PdfCraft: pdf_tool ----
PDF_ACTIONS = ("merge", "extract", "delete", "rotate", "split", "text", "render", "compress", "watermark", "from_images", "info")


def _pages(v) -> list[int]:
    """'1,3-5' | [1,3] -> [1,3,4,5]"""
    if isinstance(v, list):
        return [int(x) for x in v]
    out: list[int] = []
    for part in str(v or "").replace(" ", "").split(","):
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out.extend(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    return out


async def pdf_tool(args: dict, ctx: ToolContext) -> ToolResult:
    import json

    from .mcp_client import mcp_manager

    if not mcp_manager.is_connected(PDFCRAFT):
        return ToolResult("PdfCraft non e' attivo su questo server (./familai.sh pdfcraft up).", is_error=True)
    action = str(args.get("action") or "").lower()
    if action not in PDF_ACTIONS:
        return ToolResult(f"Azione sconosciuta: {action}. Azioni: {', '.join(PDF_ACTIONS)}.", is_error=True)
    ids = [str(x) for x in (args.get("file_ids") or ([args["file_id"]] if args.get("file_id") else []))]
    name = str(args.get("filename") or args.get("title") or "")

    async def call(tool: str, a: dict) -> dict | str:
        txt = await mcp_manager.call(PDFCRAFT, tool, a)
        try:
            return json.loads(txt)
        except Exception:
            return txt

    async def open_doc(fid: str) -> tuple[int, FileAsset]:
        asset = await _source_asset(ctx, fid)
        if isinstance(asset, ToolResult):
            raise RuntimeError(asset.content)
        r = await call("doc_open", {"path": asset.path})
        if not isinstance(r, dict) or "doc" not in r:
            raise RuntimeError(f"doc_open: {str(r)[:200]}")
        return int(r["doc"]), asset

    def out_path(base: str, ext: str = "pdf") -> Path:
        return _unique(app_dir(PDFCRAFT) / _safe_name(name or base, ext))

    artifacts: list[dict] = []
    text_out = ""
    try:
        if action == "from_images":
            srcs = []
            for fid in ids:
                a = await _source_asset(ctx, fid)
                if isinstance(a, ToolResult):
                    return a
                srcs.append(a.path)
            if not srcs:
                return ToolResult("from_images: servono file_ids di immagini (png/jpg).", is_error=True)
            r = await call("doc_create", {"from": "images", "paths": srcs})
            doc = int(r["doc"]) if isinstance(r, dict) else 0
            out = out_path("immagini")
            await call("doc_save", {"doc": doc, "path": str(out)})
            await call("doc_close", {"doc": doc, "discard_changes": True})
            artifacts.append(await register_saved_file(ctx, str(out)))
        elif action == "merge":
            if len(ids) < 2:
                return ToolResult("merge: servono almeno due file_ids.", is_error=True)
            paths = []
            for fid in ids:
                a = await _source_asset(ctx, fid)
                if isinstance(a, ToolResult):
                    return a
                paths.append(a.path)
            out = out_path("unito")
            await call("doc_combine", {"paths": paths, "out": str(out)})
            artifacts.append(await register_saved_file(ctx, str(out)))
        else:
            if not ids:
                return ToolResult(f"{action}: serve file_id del PDF.", is_error=True)
            doc, asset = await open_doc(ids[0])
            stem = Path(asset.filename).stem
            try:
                if action == "info":
                    r = await call("doc_info", {"doc": doc})
                    text_out = json.dumps(r, ensure_ascii=False)[:6000]
                elif action == "text":
                    a = {"doc": doc}
                    if args.get("pages"):
                        a["pages"] = _pages(args["pages"])
                    r = await call("text_extract", a)
                    text_out = (json.dumps(r, ensure_ascii=False) if not isinstance(r, str) else r)[:60000]
                elif action == "render":
                    pages = _pages(args.get("pages") or [1])[:6]
                    for pg in pages:
                        res = await mcp_manager.call_raw(PDFCRAFT, "page_render", {"doc": doc, "page": pg, "dpi": float(args.get("dpi") or 110)})
                        _, arts = await mcp_manager._collect(res, ctx)
                        artifacts.extend(arts)
                    text_out = f"Pagine {pages} mostrate all'utente come immagini."
                elif action == "extract":
                    out = out_path(f"{stem}-pagine")
                    await call("page_extract", {"doc": doc, "pages": _pages(args.get("pages")), "out": str(out)})
                    artifacts.append(await register_saved_file(ctx, str(out)))
                elif action in ("delete", "rotate", "watermark"):
                    if action == "delete":
                        await call("page_delete", {"doc": doc, "pages": _pages(args.get("pages"))})
                    elif action == "rotate":
                        a = {"doc": doc, "degrees": int(args.get("degrees") or 90)}
                        if args.get("pages"):
                            a["pages"] = _pages(args["pages"])
                        await call("page_rotate", a)
                    else:
                        a = {"doc": doc, "text": str(args.get("text") or "BOZZA"), "opacity": float(args.get("opacity") or 0.25)}
                        if args.get("pages"):
                            a["pages"] = _pages(args["pages"])
                        await call("doc_watermark", a)
                    out = out_path(f"{stem}-{action}")
                    await call("doc_save", {"doc": doc, "path": str(out), "full": True})
                    artifacts.append(await register_saved_file(ctx, str(out)))
                elif action == "compress":
                    out = out_path(f"{stem}-compresso")
                    await call("doc_optimize", {"doc": doc, "path": str(out), "discard_thumbnails": True})
                    artifacts.append(await register_saved_file(ctx, str(out)))
                elif action == "split":
                    folder = app_dir(PDFCRAFT) / f"{_safe_name(stem, 'd')[:-2]}-parti-{uuid.uuid4().hex[:6]}"
                    folder.mkdir(parents=True, exist_ok=True)
                    a = {"doc": doc, "out_dir": str(folder)}
                    if args.get("every"):
                        a["every"] = int(args["every"])
                    elif args.get("pages"):
                        a["before"] = _pages(args["pages"])
                    else:
                        a["every"] = 1
                    await call("doc_split", a)
                    for f in sorted(folder.iterdir()):
                        if f.is_file():
                            artifacts.append(await register_saved_file(ctx, str(f)))
            finally:
                try:
                    await call("doc_close", {"doc": doc, "discard_changes": True})
                except Exception:
                    pass
    except Exception as e:
        log.warning("pdf_tool %s fallito: %s", action, e)
        return ToolResult(f"PdfCraft ({action}): {type(e).__name__}: {str(e)[:400]}", is_error=True)
    artifacts = [a for a in artifacts if a]
    if text_out and not artifacts:
        return ToolResult(text_out)
    files = ", ".join(a.get("filename", "immagine") for a in artifacts)
    return ToolResult((text_out + " " if text_out else "") + f"Fatto con PdfCraft: {files or 'nessun file'} mostrat{'o' if len(artifacts) == 1 else 'i'} all'utente "
                      f"come allegat{'o' if len(artifacts) == 1 else 'i'} scaricabil{'e' if len(artifacts) == 1 else 'i'}. Non inventare link.",
                      artifacts=artifacts or None)


# ---------------------------------------------------------------- DeckCraft: write_presentation ----
DECK_THEMES = ("DeckCraft", "Harbor", "Ember", "Meadow", "Nocturne", "Paper", "Coral Reef", "Slate")
DECK_LAYOUTS = ("title", "titleAndContent", "sectionHeader", "twoContent", "comparison", "titleOnly", "blank", "contentWithCaption", "pictureWithCaption")


async def write_presentation(args: dict, ctx: ToolContext) -> ToolResult:
    from .mcp_client import mcp_manager

    if not mcp_manager.is_connected(DECKCRAFT):
        return ToolResult("DeckCraft non e' attivo su questo server (./familai.sh deckcraft up).", is_error=True)
    fmt = str(args.get("format") or "pptx").lower().lstrip(".")
    if fmt not in APPS[DECKCRAFT]["formats"]:
        return ToolResult(f"Formato non supportato: {fmt}. Usa uno tra {', '.join(APPS[DECKCRAFT]['formats'])}.", is_error=True)
    title = str(args.get("title") or args.get("filename") or "presentazione")
    out = _unique(app_dir(DECKCRAFT) / _safe_name(str(args.get("filename") or title), fmt))
    slides = [s for s in (args.get("slides") or []) if isinstance(s, dict)]
    src_id = str(args.get("source_file_id") or "").strip()
    notes: list[str] = []
    try:
        if src_id:
            asset = await _source_asset(ctx, src_id)
            if isinstance(asset, ToolResult):
                return asset
            await mcp_manager.call(DECKCRAFT, "open_presentation", {"path": asset.path})
        else:
            if not slides:
                return ToolResult("Servono le diapositive (slides: [{title, body, layout}]) oppure source_file_id.", is_error=True)
            theme = str(args.get("theme") or "DeckCraft")
            if theme not in DECK_THEMES:
                notes.append(f"tema '{theme}' sconosciuto: uso DeckCraft")
                theme = "DeckCraft"
            await mcp_manager.call(DECKCRAFT, "new_presentation", {"theme": theme, "blank": True})
        for i, sl in enumerate(slides):
            a: dict = {}
            layout = str(sl.get("layout") or ("title" if i == 0 and not src_id else "titleAndContent"))
            a["layout"] = layout if layout in DECK_LAYOUTS else "titleAndContent"
            if sl.get("title"):
                a["title"] = str(sl["title"])
            body = sl.get("body")
            if isinstance(body, list):
                body = "\n".join(str(x) for x in body)
            if body:
                a["body"] = str(body)
            try:
                await mcp_manager.call(DECKCRAFT, "add_slide", a)
            except Exception as e:
                notes.append(f"diapositiva {i + 1} non aggiunta ({str(e)[:80]})")
        if fmt == "pdf":
            await mcp_manager.call(DECKCRAFT, "export", {"path": str(out), "format": "pdf"})
        else:
            await mcp_manager.call(DECKCRAFT, "save_presentation", {"path": str(out)})
    except Exception as e:
        log.warning("write_presentation fallito: %s", e)
        return ToolResult(f"DeckCraft: {type(e).__name__}: {str(e)[:400]}", is_error=True)
    art = await register_saved_file(ctx, str(out))
    if not art:
        return ToolResult("DeckCraft non ha prodotto il file.", is_error=True)
    res = _done(out, art, "DeckCraft")
    if notes:
        res.content += " Note: " + "; ".join(notes) + "."
    return res


# ---------------------------------------------------------------- IdeaCraft: mind_map ----
async def mind_map(args: dict, ctx: ToolContext) -> ToolResult:
    import json

    from .mcp_client import mcp_manager

    if not mcp_manager.is_connected(IDEACRAFT):
        return ToolResult("IdeaCraft non e' attivo su questo server (./familai.sh ideacraft up).", is_error=True)
    title = str(args.get("title") or args.get("filename") or "mappa")
    outline = str(args.get("outline") or "")
    src_id = str(args.get("source_file_id") or "").strip()
    layout = "radial" if str(args.get("layout") or "tree").lower() == "radial" else "tree"
    want = [str(f).lower().lstrip(".") for f in (args.get("formats") or ["svg", "json"])]
    want = [f for f in want if f in APPS[IDEACRAFT]["formats"]] or ["svg"]
    base = _safe_name(title, "x")[:-2]
    exts = {"svg": ".svg", "json": ".ideacraft.json", "mm": ".mm", "opml": ".opml", "md": ".md", "drawio": ".drawio"}
    try:
        if src_id:
            asset = await _source_asset(ctx, src_id)
            if isinstance(asset, ToolResult):
                return asset
            await mcp_manager.call(IDEACRAFT, "open_map", {"path": asset.path})
        else:
            if not outline.strip():
                return ToolResult("Serve outline (elenco Markdown annidato: '- idea' / '  - sotto-idea') oppure source_file_id.", is_error=True)
            md = outline if outline.lstrip().startswith("#") else f"# {title}\n{outline}"
            await mcp_manager.call(IDEACRAFT, "import_text", {"text": md, "format": "md"})
        await mcp_manager.call(IDEACRAFT, "layout", {"kind": layout})
    except Exception as e:
        log.warning("mind_map fallito: %s", e)
        return ToolResult(f"IdeaCraft: {type(e).__name__}: {str(e)[:400]}", is_error=True)
    artifacts = []
    try:
        for fmt in want:
            out = _unique(app_dir(IDEACRAFT) / f"{base}{exts[fmt]}")
            await mcp_manager.call(IDEACRAFT, "save_map", {"path": str(out)})
            for extra in [str(out)] + sidecar_files(str(out)):
                art = await register_saved_file(ctx, extra)
                if art:
                    artifacts.append(art)
    except Exception as e:
        log.warning("mind_map fallito: %s", e)
        return ToolResult(f"IdeaCraft: {type(e).__name__}: {str(e)[:400]}", is_error=True)
    if not artifacts:
        return ToolResult("IdeaCraft non ha prodotto file.", is_error=True)
    names = ", ".join(a.get("filename", "immagine") for a in artifacts)
    return ToolResult(f"Mappa creata con IdeaCraft e mostrata all'utente: {names}. Il .ideacraft.json si apre nell'editor "
                      f"integrato (✏️ Modifica), il .mm con FreeMind/Freeplane/XMind, l'.opml con qualsiasi outliner, il .drawio con draw.io/Visio. "
                      f"Per inserire la mappa in un documento Word o in una slide usa il file .png (stesso nome dell'SVG). Non inventare link.",
                      artifacts=artifacts)


# ---------------------------------------------------------------- registrazione ----
def register_tools(app: str) -> None:
    """Chiamata dal client MCP quando il server dell'app e' connesso: il tool compare solo se l'app c'e'."""
    if app == WORDCRAFT:
        register(
            "write_document",
            "Crea un documento Word (.docx), PDF, ODT, RTF o HTML con il word processor WordCraft a partire da testo Markdown "
            "(titoli #, elenchi, grassetto, tabelle), oppure converte/estende un allegato (source_file_id) in un altro formato. "
            "Il file compare in chat pronto da scaricare. Per modifiche fini (stili, tabelle, revisioni) usa poi i tool "
            "mcp__wordcraft__* (open_document sul percorso del file, execute, save_document).",
            {"type": "object", "properties": {
                "title": {"type": "string", "description": "Titolo del documento (diventa anche il nome file)"},
                "content": {"type": "string", "description": "Contenuto in Markdown"},
                "format": {"type": "string", "enum": list(APPS[WORDCRAFT]["formats"]), "description": "Formato di uscita (default docx)"},
                "filename": {"type": "string", "description": "Nome file senza percorso (opzionale)"},
                "source_file_id": {"type": "string", "description": "Id di un allegato da aprire/convertire al posto di content"}},
             "required": []},
        )(write_document)
    elif app == GRIDCRAFT:
        register(
            "write_spreadsheet",
            "Crea un foglio di calcolo Excel (.xlsx) o CSV con GridCraft: passa le righe di valori (numeri, testo, formule che "
            "iniziano con =, es. \"=SUM(B2:B10)\"); la prima riga e' l'intestazione e diventa una tabella con filtri. Puoi "
            "aggiungere formati (format_range) e grafici (insert_chart) per intervallo, o convertire un allegato (source_file_id). "
            "Il file compare in chat pronto da scaricare e l'utente puo' aprirlo nell'editor. Per operazioni avanzate "
            "(pivot, ordinamenti, formati condizionali) usa poi i tool mcp__gridcraft__* (open_workbook sul percorso del file).",
            {"type": "object", "properties": {
                "title": {"type": "string", "description": "Titolo (diventa anche il nome file)"},
                "values": {"type": "array", "description": "Righe di celle del primo foglio (scorciatoia per sheets=[{values}])",
                           "items": {"type": "array", "items": {"type": ["string", "number", "boolean", "null"]}}},
                "sheets": {"type": "array", "description": "Piu' fogli: [{name, range (default A1), values, table (default true), "
                           "table_style, formats:[{range, bold, numberFormat, fillColor, fontColor, horizontalAlign…}], "
                           "charts:[{range, type: column|bar|line|pie|area|scatter, title, at}]}]",
                           "items": {"type": "object"}},
                "format": {"type": "string", "enum": list(APPS[GRIDCRAFT]["formats"]), "description": "Formato di uscita (default xlsx)"},
                "filename": {"type": "string", "description": "Nome file senza percorso (opzionale)"},
                "source_file_id": {"type": "string", "description": "Id di un allegato (xlsx/csv/ods) da aprire/convertire; i dati in sheets vengono scritti sopra"}},
             "required": []},
        )(write_spreadsheet)
    elif app == PDFCRAFT:
        register(
            "pdf_tool",
            "Lavora sui PDF allegati con PdfCraft: merge (unisce piu' file_ids), extract (estrae pages in un nuovo PDF), delete "
            "(toglie pages), rotate (degrees 90/180/270, pages opzionali), split (every N pagine, o pages dove iniziare le parti), "
            "text (testo delle pagine), render (mostra pages come immagini), compress (riduce il peso), watermark (text in "
            "filigrana), from_images (immagini -> PDF), info (metadati e numero pagine). I file prodotti compaiono in chat. "
            "Per tutto il resto (moduli, firme, commenti, redazione, OCR, confronto) usa i tool mcp__pdfcraft__* "
            "(doc_open con il percorso del file, poi tool_search/tool_call; salva in " + str(settings.data_dir / "pdfcraft") + ").",
            {"type": "object", "properties": {
                "action": {"type": "string", "enum": list(PDF_ACTIONS)},
                "file_id": {"type": "string", "description": "Id dell'allegato PDF (o immagine per from_images)"},
                "file_ids": {"type": "array", "items": {"type": "string"}, "description": "Piu' allegati (merge, from_images)"},
                "pages": {"type": "string", "description": "Pagine, es. \"1,3-5\" (extract, delete, rotate, text, render, split=inizio parti)"},
                "degrees": {"type": "integer", "description": "rotate: 90, 180, 270"},
                "every": {"type": "integer", "description": "split: una parte ogni N pagine"},
                "text": {"type": "string", "description": "watermark: testo"},
                "dpi": {"type": "number", "description": "render: risoluzione (default 110)"},
                "filename": {"type": "string", "description": "Nome del file prodotto (opzionale)"}},
             "required": ["action"]},
        )(pdf_tool)
    elif app == DECKCRAFT:
        register(
            "write_presentation",
            "Crea una presentazione PowerPoint (.pptx) o PDF con DeckCraft: passa le diapositive come elenco di {title, body, "
            "layout}; body = una riga per punto (tab iniziali = sottolivelli). Temi: DeckCraft, Harbor, Ember, Meadow, Nocturne, "
            "Paper, Coral Reef, Slate. Con source_file_id apre/converte una presentazione allegata (pptx) e vi aggiunge le slide. "
            "Il file compare in chat pronto da scaricare e l'utente puo' aprirlo nell'editor. Per immagini, tabelle, grafici e "
            "ritocchi usa poi i tool mcp__deckcraft__* (open_presentation sul percorso del file, insert_picture, insert_table, "
            "insert_chart, render_slide per vedere una slide, save_presentation in " + str(settings.data_dir / "deckcraft") + ").",
            {"type": "object", "properties": {
                "title": {"type": "string", "description": "Titolo (diventa anche il nome file)"},
                "slides": {"type": "array", "description": "Diapositive in ordine", "items": {"type": "object", "properties": {
                    "title": {"type": "string"}, "body": {"type": "string", "description": "Testo, una riga per punto"},
                    "layout": {"type": "string", "enum": list(DECK_LAYOUTS)}}}},
                "theme": {"type": "string", "enum": list(DECK_THEMES)},
                "format": {"type": "string", "enum": list(APPS[DECKCRAFT]["formats"]), "description": "Formato di uscita (default pptx)"},
                "filename": {"type": "string", "description": "Nome file senza percorso (opzionale)"},
                "source_file_id": {"type": "string", "description": "Id di una presentazione allegata da aprire/convertire"}},
             "required": []},
        )(write_presentation)
    elif app == IDEACRAFT:
        register(
            "mind_map",
            "Crea una mappa mentale / mappa concettuale con IdeaCraft a partire da un outline Markdown (prima riga '# Titolo', "
            "poi elenco annidato '- idea' / '  - sotto-idea'; righe 'A -> B: etichetta' aggiungono collegamenti liberi), "
            "oppure da un file allegato (.mm FreeMind, .xmind, .opml, .md, .mmd Mermaid). Produce l'immagine SVG della mappa "
            "(mostrata in chat) e il .ideacraft.json modificabile nell'editor; a richiesta .mm/.opml/.md. Ottima per "
            "brainstorming, schemi di studio, piani. Per ritocchi puntuali usa i tool mcp__ideacraft__* (open_map, add_idea, "
            "update_idea, link_ideas, layout, save_map). Per DIAGRAMMI (schemi di rete, flowchart, organigrammi, UML/BPMN) non usare "
            "questo tool: usa mcp__ideacraft__build_map (mode 'diagram', nodi con kind es. net.router/net.switch/flow.decision e props "
            "ip/vlan/gateway, link con stile) e poi mcp__ideacraft__save_map su .svg e .ideacraft.json (o .drawio).",
            {"type": "object", "properties": {
                "title": {"type": "string", "description": "Titolo (idea centrale e nome file)"},
                "outline": {"type": "string", "description": "Outline Markdown annidato"},
                "layout": {"type": "string", "enum": ["tree", "radial"], "description": "Disposizione (default tree)"},
                "formats": {"type": "array", "items": {"type": "string", "enum": list(APPS[IDEACRAFT]["formats"])},
                            "description": "File da produrre (default svg e json = formato nativo, modificabile nell'editor; mm per FreeMind/XMind; drawio per draw.io/Visio)"},
                "source_file_id": {"type": "string", "description": "Id di un allegato da importare al posto di outline"}},
             "required": []},
        )(mind_map)


def unregister_tools(app: str) -> None:
    unregister_prefix(APPS[app]["native_tool"])
