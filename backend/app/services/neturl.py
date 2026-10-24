"""Normalizzazione degli indirizzi dei servizi di casa quando il backend gira in Docker:
'localhost' dentro il container e' il container stesso, non il Mac/il server. Lo riscriviamo in host.docker.internal."""
from __future__ import annotations

import os
from urllib.parse import urlsplit, urlunsplit


def in_docker() -> bool:
    return os.path.exists("/.dockerenv") or os.environ.get("FAMILAI_IN_DOCKER") == "1"


def host_url(url: str) -> str:
    """Es. http://localhost:8188 → http://host.docker.internal:8188 (solo dentro un container)."""
    if not url:
        return url
    u = url.strip()
    if not u.startswith(("http://", "https://")):
        u = "http://" + u
    if not in_docker():
        return u
    parts = urlsplit(u)
    host = (parts.hostname or "").lower()
    if host in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
        netloc = "host.docker.internal" + (f":{parts.port}" if parts.port else "")
        return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
    return u
