"""Enable the isolated browser in an existing Compose installation."""
from pathlib import Path
import os
import secrets
import subprocess
import sys

root = Path(__file__).resolve().parents[2]
os.chdir(root)
path = root / ".env"
if not path.is_file():
    raise SystemExit("Configura prima FamilOS: manca il file .env.")
compose = ["docker", "compose", "-f", "docker-compose.yml"]
if (root / ".familai-mode").exists() and (root / ".familai-mode").read_text().strip() == "dev":
    compose += ["-f", "docker-compose.dev.yml"]
if sys.argv[1:] == ["down"]:
    subprocess.run([*compose, "stop", "browser"], check=True)
    raise SystemExit(0)
if sys.argv[1:] != ["up"]:
    raise SystemExit("Uso: ./familos.sh browser up|down")
text = path.read_text()
values = dict(line.split("=", 1) for line in text.splitlines() if "=" in line and not line.startswith("#"))
profiles = set(filter(None, values.get("COMPOSE_PROFILES", "").split(","))) | {"browser"}
updates = {"COMPOSE_PROFILES": ",".join(sorted(profiles))}
if len(values.get("BROWSER_WORKER_TOKEN", "").strip('"\'')) < 32:
    updates["BROWSER_WORKER_TOKEN"] = secrets.token_hex(32)
lines = [line for line in text.splitlines() if line.split("=", 1)[0] not in updates]
path.write_text("\n".join(lines + [f"{k}={v}" for k, v in updates.items()]) + "\n")
os.chmod(path, 0o600)
subprocess.run([*compose, "build", "browser", "backend", "frontend"], check=True)
subprocess.run([*compose, "up", "-d", "--no-deps", "browser", "backend", "frontend"], check=True)
print("Browser grafico attivato. Apri Browser in FamilOS; le sessioni sono temporanee e separate per accesso.")
