#!/usr/bin/env python3
"""Opt-in local updater. Protocol 1; no TCP listener or commands supplied by clients.

Run the installed copy through systemd. GitHub repositories in the host config are
trusted code sources, including their Dockerfiles. See docs/UPDATES.md.
"""
import base64
import json
import os
from pathlib import Path
import re
import secrets
import socketserver
import subprocess
import sys
import tarfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler

APPS = ("wordcraft", "gridcraft", "pdfcraft", "deckcraft", "ideacraft", "mailcraft")
CHANNELS = ("stable", "experimental")
TAG = re.compile(r"v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-((?:alpha|beta|rc|preview)\.[0-9]+))?\Z")
SLUG = re.compile(r"[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+\Z")


def version(tag):
    match = TAG.fullmatch(tag)
    if not match:
        raise ValueError("Versione non supportata: usare v1.2.3 oppure v1.2.3-rc.1.")
    major, minor, patch, pre = match.groups()
    kind, number = pre.split(".") if pre else ("stable", "0")
    rank = {"alpha": 0, "beta": 1, "preview": 2, "rc": 3, "stable": 4}[kind]
    return int(major), int(minor), int(patch), rank, int(number)


def repository(remote):
    value = re.sub(r"^(https://github.com/|git@github.com:)", "", remote.strip())
    value = value.removesuffix(".git")
    if not SLUG.fullmatch(value) or any(x in (".", "..") for x in value.split("/")):
        raise ValueError("La sorgente deve essere un repository GitHub owner/repository.")
    return value


def select_release(releases, channel):
    if channel not in CHANNELS:
        raise ValueError("Canale non valido.")
    eligible = []
    for release in releases:
        tag = release.get("tag_name", "")
        if release.get("draft") or not TAG.fullmatch(tag):
            continue
        if channel == "stable" and (release.get("prerelease") or "-" in tag):
            continue
        eligible.append(release)
    return max(eligible, key=lambda r: version(r["tag_name"]), default=None)


def write_json(path, value):
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as stream:
        os.chmod(tmp, 0o600)
        json.dump(value, stream, ensure_ascii=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, path)


class Updater:
    def __init__(self, config):
        self.root = Path(config["root"]).resolve()
        self.directory = Path(config["state"])
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.repos = {k: repository(v) for k, v in config["repositories"].items()}
        if set(self.repos) != {"core", *APPS}:
            raise ValueError("Catalogo host non valido.")
        self.lock = threading.RLock()
        path = self.directory / "state.json"
        self.state = json.loads(path.read_text()) if path.exists() else {"channel": "stable", "offers": {}, "installed": {}, "job": None}
        if self.state.get("job", {}) and self.state["job"]["status"] == "running":
            self.state["job"].update(status="interrupted", message="Agente riavviato durante un lavoro. Verificare il server e il backup prima di riprovare.")
            self.save()

    def save(self):
        write_json(self.directory / "state.json", self.state)

    def status(self):
        with self.lock:
            return json.loads(json.dumps({**self.state, "repositories": self.repos,
                "version": (self.root / "VERSION").read_text().strip(), "protocol": 1}))

    def phase(self, message, **fields):
        with self.lock:
            self.state["job"].update(message=message, **fields)
            self.save()

    def github(self, target, suffix):
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "FamilOS-Updater"}
        token = self.root / "private/github-token"
        if token.exists() and token.read_text().strip():
            headers["Authorization"] = "Bearer " + token.read_text().strip()
        request = urllib.request.Request(f"https://api.github.com/repos/{self.repos[target]}/{suffix}", headers=headers)
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read(2_000_000))

    def run(self, *args, timeout=3600):
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
        if args[0] == "git":
            args = ("git", "-c", f"safe.directory={self.root}", *args[1:])
        token = self.root / "private/github-token"
        if args[0] == "git" and token.exists() and token.read_text().strip():
            auth = base64.b64encode(("x-access-token:" + token.read_text().strip()).encode()).decode()
            env.update(GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="http.https://github.com/.extraheader", GIT_CONFIG_VALUE_0="AUTHORIZATION: basic " + auth)
        # Output stays on the host. Never return build output, credentials or env to web clients.
        with open(self.directory / "last-command.log", "w") as log:
            result = subprocess.run(args, cwd=self.root, env=env, stdout=subprocess.PIPE, stderr=log,
                                    timeout=timeout, check=False, text=True)
            if result.returncode:
                log.write(result.stdout)
                raise RuntimeError("Comando server non riuscito. Consultare last-command.log sulla VM.")
        return result.stdout.strip()

    def compose(self, *args):
        return self.run("docker", "compose", "--project-directory", str(self.root), "-f", str(self.root / "docker-compose.yml"), *args)

    def check(self, target, channel):
        release = select_release(self.github(target, "releases?per_page=100"), channel)
        offer = None
        if release:
            tag = release["tag_name"]
            sha = self.github(target, "commits/" + tag)["sha"]
            if not re.fullmatch(r"[a-f0-9]{40}", sha):
                raise ValueError("Commit della release non valido.")
            installed = self.state["installed"].get(target, {}).get("tag")
            if target == "core":
                installed = (self.root / "VERSION").read_text().strip()
            newer = not installed or version(tag) > version(installed)
            offer = {"id": secrets.token_hex(16), "release_id": release["id"], "tag": tag, "sha": sha,
                     "channel": channel, "target": target, "created": time.time(), "newer": newer,
                     "experimental": bool(release.get("prerelease") or "-" in tag),
                     "notes": (release.get("body") or "Nessuna nota pubblicata.")[:12000]}
        with self.lock:
            self.state["channel"] = channel
            self.state["offers"] = {target: offer}
            self.save()

    def validate_offer(self, body):
        offer = self.state["offers"].get(body["target"])
        if not offer or offer["id"] != body.get("offer_id") or offer["channel"] != body["channel"]:
            raise ValueError("Ricontrollare la release prima di installare.")
        if time.time() - offer["created"] > 900 or not offer["newer"]:
            raise ValueError("Offerta scaduta o versione non successiva a quella installata.")
        if offer["experimental"] and body.get("accept_experimental") is not True:
            raise ValueError("Confermare esplicitamente la release sperimentale.")
        release = self.github(body["target"], "releases/" + str(offer["release_id"]))
        valid = select_release([release], body["channel"])
        if not valid or release["tag_name"] != offer["tag"] or bool(release.get("prerelease") or "-" in release["tag_name"]) != offer["experimental"]:
            raise ValueError("La release è cambiata: eseguire una nuova verifica.")
        if self.github(body["target"], "commits/" + offer["tag"])["sha"] != offer["sha"]:
            raise ValueError("Il tag è stato spostato: aggiornamento annullato.")
        return offer

    def submit(self, body):
        action, target = body.get("action"), body.get("target")
        if action not in ("check", "install", "disable") or target not in self.repos:
            raise ValueError("Operazione non valida.")
        allowed = {"action", "target", "channel"} if action == "check" else {"action", "target", "channel", "offer_id", "accept_experimental", "confirm_maintenance"}
        if set(body) - allowed or (action != "disable" and body.get("channel") not in CHANNELS):
            raise ValueError("Parametri non validi.")
        if action != "check" and body.get("confirm_maintenance") is not True:
            raise ValueError("Confermare l’interruzione temporanea.")
        if action == "disable" and target == "core":
            raise ValueError("Il sistema non può essere disattivato.")
        with self.lock:
            job = self.state.get("job")
            if job and job["status"] in ("running", "recovery_required", "interrupted"):
                raise ValueError("Un lavoro è in corso o richiede recupero manuale sulla VM.")
            self.state["job"] = {"id": secrets.token_hex(16), "status": "running", "action": action, "target": target, "message": "Operazione avviata"}
            self.save()
            threading.Thread(target=self.work, args=(dict(body),), daemon=True).start()
            return dict(self.state["job"])

    def work(self, body):
        try:
            if body["action"] == "check":
                self.check(body["target"], body["channel"])
            else:
                self.install(body)
            self.phase("Operazione completata.", status="complete")
        except Exception as error:
            # No exception text from urllib/subprocess (can include credentials/response bodies).
            message = str(error) if isinstance(error, ValueError) else "Operazione non riuscita. Consultare i log e la guida aggiornamenti sulla VM."
            recovery = self.state["job"].get("maintenance", False)
            self.phase(message, status="recovery_required" if recovery else "failed")

    def update_env(self, values):
        path = self.root / ".env"
        lines = path.read_text().splitlines()
        lines = [line for line in lines if line.split("=", 1)[0].strip() not in values]
        path.write_text("\n".join(lines + [f"{key}={value}" for key, value in values.items()]) + "\n")
        os.chmod(path, 0o600)

    def install(self, body):
        target = body["target"]
        disable = body["action"] == "disable"
        offer = None if disable else self.validate_offer(body)
        if (self.root / ".familai-mode").exists() and (self.root / ".familai-mode").read_text().strip() != "prod":
            raise ValueError("Gli aggiornamenti web richiedono un’installazione Docker in modalità prod.")
        if self.run("git", "status", "--porcelain"):
            raise ValueError("Sono presenti modifiche locali: salvarle nel proprio fork prima di aggiornare.")
        old_commit = self.run("git", "rev-parse", "HEAD")
        if target == "core" and version(offer["tag"]) <= version((self.root / "VERSION").read_text().strip()):
            raise ValueError("La release non è successiva alla versione attuale.")
        original_env = (self.root / ".env").read_bytes()
        running = self.compose("ps", "--services", "--status", "running").splitlines()
        if not {"backend", "frontend", "caddy"}.issubset(running):
            raise ValueError("Avviare prima l’installazione di produzione completa.")
        images = {}
        for service in running:
            container = self.compose("ps", "-q", service)
            if not container or "\n" in container:
                raise ValueError("Configurazione con repliche non supportata dall’agente.")
            images[service] = {"image": self.run("docker", "inspect", "--format", "{{.Image}}", container), "pull_policy": "never"}
        write_json(self.directory / "rollback-images.json", {"services": images})
        write_json(self.directory / "recovery.json", {"commit": old_commit, "services": running})
        recovery_env = self.directory / "recovery.env"
        recovery_env.write_bytes(original_env)
        os.chmod(recovery_env, 0o600)
        changed_code = False
        try:
            if target == "core":
                self.phase("Scaricamento del commit della release verificata…")
                self.run("git", "-c", "core.hooksPath=/dev/null", "fetch", "--no-tags", "https://github.com/" + self.repos[target] + ".git", "refs/tags/" + offer["tag"])
                if self.run("git", "rev-parse", "FETCH_HEAD^{commit}") != offer["sha"]:
                    raise ValueError("Il commit scaricato non corrisponde alla release.")
                if version(self.run("git", "show", offer["sha"] + ":VERSION")) != version(offer["tag"]):
                    raise ValueError("VERSION non corrisponde al tag pubblicato.")
                self.run("git", "-c", "core.hooksPath=/dev/null", "checkout", "--detach", offer["sha"])
                changed_code = True
            else:
                env = dict(line.split("=", 1) for line in original_env.decode().splitlines() if "=" in line and not line.startswith("#"))
                profiles = set(filter(None, env.get("COMPOSE_PROFILES", "").split(",")))
                if disable:
                    profiles.discard(target)
                else:
                    profiles.add(target)
                values = {"COMPOSE_PROFILES": ",".join(sorted(profiles))}
                prefix = target.upper()
                if not disable:
                    values.update({prefix + "_SHA": offer["sha"], prefix + "_REPO": "https://github.com/" + self.repos[target]})
                if target == "mailcraft":
                    values.update(MAILCRAFT_URL="" if disable else "http://mailcraft:7878", MAILCRAFT_MCP_HOST="" if disable else "mailcraft:7889")
                    if not disable and not env.get("MAILCRAFT_TOKEN"):
                        values["MAILCRAFT_TOKEN"] = secrets.token_hex(32)
                else:
                    port = 7982 + APPS.index(target)
                    values.update({prefix + "_HOST": "" if disable else f"{target}:{port}", prefix + "_WEB": "0" if disable else "1"})
                self.update_env(values)
            self.phase("Preparazione immagini. Il sistema resta disponibile durante la compilazione…")
            build = ["backend", "frontend", "sandbox"] if target == "core" else ["backend"] + ([] if disable else [target] + ([] if target == "mailcraft" else [target + "-web"]))
            self.compose("build", *build)
            # Stop writers before snapshotting SQLite/WAL and user files. Only this Compose project.
            self.phase("Modalità manutenzione: arresto servizi e backup…", maintenance=True, previous_commit=old_commit)
            self.compose("stop", *running)
            backup = self.root / "backups" / ("update-" + self.state["job"]["id"] + ".tar.gz")
            backup.parent.mkdir(exist_ok=True, mode=0o700)
            self.phase("Creazione del backup coerente…", backup=str(backup))
            # Save the pre-update environment, not the modified application configuration.
            new_env = (self.root / ".env").read_bytes()
            (self.root / ".env").write_bytes(original_env)
            try:
                with tarfile.open(backup, "w:gz") as archive:
                    for name in ("data", ".env", "private"):
                        if (self.root / name).exists():
                            archive.add(self.root / name, arcname=name, filter=backup_filter)
                os.chmod(backup, 0o600)
                with tarfile.open(backup) as archive:
                    if ".env" not in archive.getnames():
                        raise ValueError("Backup incompleto.")
            finally:
                (self.root / ".env").write_bytes(new_env)
            self.phase("Avvio e verifica dei servizi aggiornati…")
            desired = set(running) - {"caddy"}
            if target != "core":
                app_services = {target} | (set() if target == "mailcraft" else {target + "-web"})
                desired = desired - app_services if disable else desired | app_services
            self.compose("up", "-d", "--no-build", "--no-deps", *sorted(desired))
            healthy = False
            for _ in range(30):
                try:
                    self.compose("exec", "-T", "backend", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=5)")
                    healthy = True
                    break
                except RuntimeError:
                    time.sleep(2)
            if not healthy:
                raise ValueError("Verifica backend fallita. Servizio fermo: ripristinare il backup dalla VM.")
            self.compose("up", "-d", "--no-build", "--no-deps", "caddy")
            self.compose("exec", "-T", "caddy", "caddy", "validate", "--config", "/etc/caddy/Caddyfile")
            with self.lock:
                self.state["installed"][target] = {"tag": offer["tag"] if offer else self.state["installed"].get(target, {}).get("tag"), "enabled": not disable}
                self.state["offers"] = {}
                self.save()
        except Exception:
            if self.state["job"].get("maintenance"):
                self.compose("stop", *sorted(set(running) | set(build)))
            else:
                (self.root / ".env").write_bytes(original_env)
                if changed_code:
                    self.run("git", "-c", "core.hooksPath=/dev/null", "checkout", "--detach", old_commit)
            raise


def backup_filter(item):
    if item.name == "private/updater-socket" or item.name.startswith("private/updater-socket/"):
        return None
    if not (item.isfile() or item.isdir()):
        raise ValueError("Backup interrotto: dati con link o file speciali richiedono una gestione manuale.")
    return item


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def reply(self, code, value):
        raw = json.dumps(value).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        self.reply(200, self.server.updater.status()) if self.path == "/status" else self.reply(404, {"error": "Non trovato"})

    def do_POST(self):
        if self.path != "/jobs":
            return self.reply(404, {"error": "Non trovato"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 4096:
                raise ValueError("Richiesta non valida")
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError("Richiesta non valida")
            self.reply(202, self.server.updater.submit(body))
        except ValueError as error:
            self.reply(400, {"error": str(error)})


class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(10)
        return connection, address


if __name__ == "__main__":
    os.umask(0o077)
    config = json.loads(Path(sys.argv[1]).read_text())
    updater = Updater(config)
    socket = updater.root / "private/updater-socket/control.sock"
    socket.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    socket.unlink(missing_ok=True)
    with Server(str(socket), Handler) as server:
        os.chmod(socket, 0o600)
        server.updater = updater
        server.serve_forever()
