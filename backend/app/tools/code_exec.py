"""Tool run_code: esegue codice nel servizio sandbox isolato e riporta output e file prodotti (grafici, CSV, immagini…)."""
from __future__ import annotations

import base64
import uuid
from pathlib import Path

import httpx

from ..config import get_settings
from ..models import FileAsset
from .registry import ToolContext, ToolResult, register

settings = get_settings()


@register(
    "run_code",
    "Esegue codice in una sandbox isolata (senza internet) e restituisce stdout, stderr e i file creati. Linguaggi: "
    "python (con numpy, pandas, matplotlib, pillow, sympy, scipy), javascript/typescript (Node 22), java (JDK), bash. "
    "Usalo per calcoli, elaborazione dati, generare grafici (salva con plt.savefig('grafico.png')), verificare che un "
    "programma funzioni prima di consegnarlo. I file prodotti vengono mostrati automaticamente all'utente. "
    "Puoi passare file della chat/progetto tramite file_ids: saranno disponibili nella directory corrente con il loro nome.",
    {"type": "object", "properties": {
        "language": {"type": "string", "enum": ["python", "javascript", "typescript", "java", "bash"]},
        "code": {"type": "string"},
        "file_ids": {"type": "array", "items": {"type": "string"}, "description": "File da rendere disponibili"},
        "stdin": {"type": "string"},
        "timeout": {"type": "integer", "description": "Secondi (max 120)"}},
     "required": ["language", "code"]},
    usage_kind="tool",
)
async def run_code(args: dict, ctx: ToolContext) -> ToolResult:
    if not settings.allow_code_execution:
        return ToolResult("Esecuzione codice disabilitata: richiede isolamento separato per ogni esecuzione.", is_error=True)
    if not settings.sandbox_url:
        return ToolResult("Esecuzione codice non configurata (SANDBOX_URL mancante)", is_error=True)
    files = []
    for fid in (args.get("file_ids") or [])[:10]:
        from ..services.access import require_access
        asset, _ = await require_access(ctx.db, "file", str(fid), ctx.user)
        if asset.path and Path(asset.path).exists():
            files.append({"name": asset.filename, "b64": base64.b64encode(Path(asset.path).read_bytes()).decode()})
    payload = {"language": args.get("language", "python"), "code": args.get("code", ""), "files": files,
               "stdin": args.get("stdin", "") or "", "timeout": min(int(args.get("timeout") or settings.sandbox_timeout), 120)}
    try:
        async with httpx.AsyncClient(timeout=payload["timeout"] + 15) as c:
            r = await c.post(f"{settings.sandbox_url.rstrip('/')}/run", json=payload)
            if r.status_code == 400:
                return ToolResult(r.json().get("detail", "richiesta non valida"), is_error=True)
            r.raise_for_status()
            res = r.json()
    except Exception as e:
        return ToolResult(f"Sandbox non raggiungibile: {e}", is_error=True)

    artifacts = []
    saved = []
    for f in res.get("files", []):
        data = base64.b64decode(f["b64"])
        fid = uuid.uuid4().hex
        ext = Path(f["name"]).suffix or ""
        path = settings.generated_dir / f"{fid}{ext}"
        path.write_bytes(data)
        asset = FileAsset(id=fid, family_id=ctx.user.family_id, owner_id=ctx.user.id, project_id=ctx.project_id,
                          conversation_id=ctx.conversation_id, filename=Path(f["name"]).name, mime=f["mime"],
                          size=len(data), path=str(path), kind="generated")
        from ..services import workspace as _ws
        await _ws.place_in_conversation(ctx.db, asset)
        ctx.db.add(asset)
        saved.append(f"{asset.filename} (id {fid}, {len(data)} byte)")
        kind = "image" if f["mime"].startswith("image/") else "video" if f["mime"].startswith("video/") else "file"
        artifacts.append({"type": kind, "file_id": fid, "url": f"/files/{fid}", "mime": f["mime"], "filename": asset.filename})
    if saved:
        await ctx.db.commit()

    parts = [f"exit code: {res.get('exit_code')}  ({res.get('duration_ms', 0)} ms)"]
    if res.get("timed_out"):
        parts.append("⏱ TIMEOUT")
    if res.get("stdout"):
        parts.append("--- stdout ---\n" + res["stdout"][-20_000:])
    if res.get("stderr"):
        parts.append("--- stderr ---\n" + res["stderr"][-10_000:])
    if saved:
        parts.append("--- file prodotti (già mostrati all'utente) ---\n" + "\n".join(saved))
    ok = (res.get("exit_code") == 0) and not res.get("timed_out")
    return ToolResult("\n".join(parts), is_error=not ok, artifacts=artifacts or None)
