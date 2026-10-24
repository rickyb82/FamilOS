#!/usr/bin/env python3
"""One-time Ubuntu host setup, explicitly invoked with sudo by the administrator."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from agent import APPS, repository


def main():
    if sys.platform != "linux" or os.geteuid() != 0 or not Path("/run/systemd/system").exists():
        raise SystemExit("Eseguire sulla VM Ubuntu con sudo e systemd.")
    root = Path(__file__).resolve().parents[2]
    if not (root / ".env").is_file():
        raise SystemExit("Configurare e avviare prima FamilOS in produzione.")
    if (root.parent.parent / 'compose.familos.yaml').exists():
        from orbita_install import install
        install(root)
        return
    remote = subprocess.check_output(["git", "-c", f"safe.directory={root}", "-C", str(root), "remote", "get-url", "origin"], text=True).strip()
    repos = {"core": repository(remote), **{app: "rickyb82/" + app for app in APPS}}
    directory = Path("/etc/familos-updater")
    directory.mkdir(mode=0o700, exist_ok=True)
    config = directory / "config.json"
    if config.exists():
        previous = json.loads(config.read_text())
        if previous["root"] != str(root):
            raise SystemExit("È già configurata un’altra installazione. Verificare /etc/familos-updater/config.json.")
        repos = previous["repositories"]
    config.write_text(json.dumps({"root": str(root), "state": "/var/lib/familos-updater", "repositories": repos}))
    os.chmod(config, 0o600)
    code = Path("/usr/local/lib/familos-updater")
    code.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(Path(__file__).with_name("agent.py"), code / "agent.py")
    os.chmod(code / "agent.py", 0o644)
    socket = root / "private/updater-socket"
    socket.mkdir(parents=True, exist_ok=True, mode=0o700)
    unit = Path("/etc/systemd/system/familos-updater.service")
    unit.write_text("""[Unit]
Description=FamilOS administrator release updater
After=docker.service network-online.target
Requires=docker.service
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 /usr/local/lib/familos-updater/agent.py /etc/familos-updater/config.json
Restart=on-failure
RestartSec=5
UMask=0077
TimeoutStopSec=15

[Install]
WantedBy=multi-user.target
""")
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "enable", "familos-updater"], check=True)
    subprocess.run(["systemctl", "restart", "familos-updater"], check=True)
    print("Agente attivato. Ricrea il backend per montare il socket: docker compose up -d --build backend")
    print("Poi apri Installazione → Aggiornamenti e app. Nessuna release verrà installata automaticamente.")


if __name__ == "__main__":
    main()
