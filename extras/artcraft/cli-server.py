#!/usr/bin/env python3
"""Servizio HTTP minimo per le app ArtCraft che (ancora) non hanno un server MCP ma solo una CLI (IdeaCraft).

POST /run  {"cmd": "<app>-cli" | "<app>-snapshot", "args": [...], "files": {"in.md": "<base64>"}, "outputs": ["out.png"]}
 -> {"exit": 0, "stdout": "...", "stderr": "...", "files": {"out.png": "<base64>"}}
Ogni richiesta gira in una cartella temporanea: i file in ingresso vengono scritti li', i comandi eseguiti con quella
cartella come directory corrente, i file di uscita richiesti letti e restituiti. Solo i binari dell'app sono ammessi.
GET /health -> {"ok": true, "app": "<app>"}
"""
import base64
import json
import os
import shutil
import subprocess
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

APP = os.environ.get("APP", "ideacraft")
PORT = int(os.environ.get("HTTP_PORT", "7986"))
ALLOWED = {f"{APP}-cli", f"{APP}-snapshot"}
TIMEOUT = int(os.environ.get("RUN_TIMEOUT", "120"))


class H(BaseHTTPRequestHandler):
    def _send(self, code: int, obj: dict) -> None:
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        self._send(200, {"ok": True, "app": APP})

    def do_POST(self):  # noqa: N802
        if self.path != "/run":
            return self._send(404, {"error": "not found"})
        try:
            req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
            cmd = str(req.get("cmd") or f"{APP}-cli")
            if cmd not in ALLOWED:
                return self._send(400, {"error": f"comando non ammesso: {cmd}"})
            args = [str(a) for a in req.get("args") or []]
            if any(a.startswith("/") or ".." in a for a in args if not a.startswith("--")):
                return self._send(400, {"error": "solo percorsi relativi alla cartella di lavoro"})
            work = tempfile.mkdtemp(prefix="run-")
            try:
                for name, b64 in (req.get("files") or {}).items():
                    name = os.path.basename(str(name))
                    with open(os.path.join(work, name), "wb") as f:
                        f.write(base64.b64decode(b64))
                p = subprocess.run([cmd, *args], cwd=work, capture_output=True, timeout=TIMEOUT)
                out = {}
                for name in req.get("outputs") or []:
                    path = os.path.join(work, os.path.basename(str(name)))
                    if os.path.isfile(path):
                        with open(path, "rb") as f:
                            out[os.path.basename(str(name))] = base64.b64encode(f.read()).decode()
                self._send(200, {"exit": p.returncode, "stdout": p.stdout.decode(errors="replace")[-20000:],
                                 "stderr": p.stderr.decode(errors="replace")[-20000:], "files": out})
            finally:
                shutil.rmtree(work, ignore_errors=True)
        except subprocess.TimeoutExpired:
            self._send(504, {"error": f"tempo massimo superato ({TIMEOUT}s)"})
        except Exception as e:  # noqa: BLE001
            self._send(500, {"error": f"{type(e).__name__}: {e}"})

    def log_message(self, fmt, *args):  # noqa: D102
        print(f"{APP}: " + fmt % args, flush=True)


if __name__ == "__main__":
    print(f">> {APP} cli-server in ascolto su :{PORT} (comandi: {', '.join(sorted(ALLOWED))})", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
