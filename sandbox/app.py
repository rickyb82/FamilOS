"""FamilAI sandbox: esegue snippet di codice in una directory temporanea con limiti di tempo/memoria/processi.

Isolamento a più livelli (vedi docker-compose.yml):
- container separato, utente non root, filesystem read-only con tmpfs per /tmp e /work
- rete interna senza uscita verso internet (raggiungibile solo dal backend)
- limiti CPU/RAM/PID del container + rlimit per ogni esecuzione + timeout
"""
from __future__ import annotations

import asyncio
import base64
import mimetypes
import os
import resource
import shutil
import tempfile
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(title="FamilAI sandbox")

WORK_ROOT = Path(os.environ.get("SANDBOX_WORK", "/work"))
MAX_TIMEOUT = int(os.environ.get("SANDBOX_MAX_TIMEOUT", "120"))
MAX_OUTPUT = 200_000          # caratteri di stdout/stderr restituiti
MAX_FILE_BYTES = 15 * 1024 * 1024
MAX_FILES = 20
MEM_LIMIT = 768 * 1024 * 1024  # per processo

LANGS: dict[str, dict] = {
    "python": {"file": "main.py", "cmd": ["python3", "main.py"]},
    "javascript": {"file": "main.js", "cmd": ["node", "main.js"]},
    "typescript": {"file": "main.ts", "cmd": ["node", "--experimental-strip-types", "main.ts"]},
    "bash": {"file": "main.sh", "cmd": ["bash", "main.sh"]},
    "java": {"file": "Main.java", "cmd": ["bash", "-c", "javac Main.java && java Main"]},
}


class FileIn(BaseModel):
    name: str
    b64: str


class RunRequest(BaseModel):
    language: str
    code: str = Field(max_length=200_000)
    files: list[FileIn] = []
    args: list[str] = []
    stdin: str = ""
    timeout: int = 30


class FileOut(BaseModel):
    name: str
    mime: str
    size: int
    b64: str


class RunResult(BaseModel):
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool
    duration_ms: int
    files: list[FileOut]


def _limits():
    resource.setrlimit(resource.RLIMIT_AS, (MEM_LIMIT, MEM_LIMIT))
    resource.setrlimit(resource.RLIMIT_NPROC, (64, 64))
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_FILE_BYTES, MAX_FILE_BYTES))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    os.setsid()


def _safe_name(name: str) -> str:
    name = os.path.basename(name).replace("\x00", "")
    return name[:120] or "file"


@app.get("/health")
async def health():
    return {"status": "ok", "languages": list(LANGS)}


@app.post("/run", response_model=RunResult)
async def run(req: RunRequest):
    lang = req.language.lower()
    if lang == "py":
        lang = "python"
    if lang in ("js", "node"):
        lang = "javascript"
    if lang == "sh":
        lang = "bash"
    if lang not in LANGS:
        raise HTTPException(400, f"Linguaggio non supportato: {req.language}. Disponibili: {', '.join(LANGS)}")
    spec = LANGS[lang]
    timeout = max(1, min(req.timeout, MAX_TIMEOUT))

    WORK_ROOT.mkdir(parents=True, exist_ok=True)
    workdir = Path(tempfile.mkdtemp(prefix="run-", dir=WORK_ROOT))
    try:
        (workdir / spec["file"]).write_text(req.code)
        before = {}
        for f in req.files[:MAX_FILES]:
            p = workdir / _safe_name(f.name)
            p.write_bytes(base64.b64decode(f.b64))
            before[p.name] = p.stat().st_mtime_ns
        before[spec["file"]] = (workdir / spec["file"]).stat().st_mtime_ns

        env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": str(workdir), "LANG": "C.UTF-8",
               "MPLBACKEND": "Agg", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1",
               "JAVA_TOOL_OPTIONS": "-Xmx256m", "NODE_OPTIONS": "--max-old-space-size=256",
               "TMPDIR": str(workdir)}
        loop = asyncio.get_running_loop()
        start = loop.time()
        proc = await asyncio.create_subprocess_exec(
            *spec["cmd"], *req.args, cwd=workdir, env=env,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            preexec_fn=_limits,
        )
        timed_out = False
        try:
            out, err = await asyncio.wait_for(proc.communicate(req.stdin.encode()), timeout=timeout)
        except asyncio.TimeoutError:
            timed_out = True
            try:
                os.killpg(proc.pid, 9)
            except ProcessLookupError:
                pass
            out, err = await proc.communicate()
        duration = int((loop.time() - start) * 1000)

        files: list[FileOut] = []
        for p in sorted(workdir.rglob("*")):
            if not p.is_file() or p.name.startswith("."):
                continue
            rel = str(p.relative_to(workdir))
            if rel in before and p.stat().st_mtime_ns == before[rel]:
                continue  # input non modificato
            if rel == spec["file"] or rel.endswith(".class") or "__pycache__" in rel or "node_modules" in rel:
                continue
            # scorie di librerie e strumenti (cache dei font di matplotlib, log di npm, ecc.): non sono "file prodotti"
            low = p.name.lower()
            if low.endswith((".log", ".pyc", ".lock")) or low.startswith(("fontlist", "npm-debug")) or ".cache" in rel or "/.npm" in f"/{rel}":
                continue
            if p.stat().st_size > MAX_FILE_BYTES or len(files) >= MAX_FILES:
                continue
            mime = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
            files.append(FileOut(name=rel, mime=mime, size=p.stat().st_size, b64=base64.b64encode(p.read_bytes()).decode()))

        return RunResult(
            exit_code=proc.returncode, timed_out=timed_out, duration_ms=duration,
            stdout=out.decode(errors="replace")[-MAX_OUTPUT:],
            stderr=(("⏱ Tempo massimo superato (%ss)\n" % timeout) if timed_out else "") + err.decode(errors="replace")[-MAX_OUTPUT:],
            files=files,
        )
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
