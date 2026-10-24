"""Backup completo (DB + file + segreti) in un unico archivio cifrato con password, e ripristino su un'installazione nuova.

Formato .fab: 16 byte di magic+salt, poi un token Fernet (chiave derivata dalla password con PBKDF2) che cifra un tar.gz con:
  manifest.json   versione, data, conteggi, impostazioni d'ambiente informative
  familai.db      copia consistente del DB SQLite (sqlite backup API)
  secrets.json    segreti in chiaro (chiavi API, password SMTP, token Gmail, segreti TOTP): nel DB sono cifrati con una
                  chiave derivata da SECRET_KEY, che su una nuova installazione e' diversa → al ripristino vengono ricifrati
  uploads/, generated/   file caricati e generati
Il ripristino viene preparato in data/pending_restore e applicato al riavvio del backend (prima di aprire il DB)."""
from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import os
import shutil
import sqlite3
import tarfile
import time
from datetime import datetime, timezone
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from ..config import get_settings

log = logging.getLogger(__name__)
settings = get_settings()
MAGIC = b"FAB1"
FORMAT_VERSION = 1


def db_path() -> Path:
    url = settings.database_url
    if not url.startswith("sqlite"):
        raise RuntimeError("Il backup dal pannello e' disponibile solo con database SQLite")
    return Path(url.split("///", 1)[1])


def _key(password: str, salt: bytes) -> bytes:
    raw = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 600_000, dklen=32)
    return base64.urlsafe_b64encode(raw)


def _pending_dir() -> Path:
    return settings.data_dir / "pending_restore"


# ------------------------------------------------------------------ export
def _collect_secrets() -> dict:
    """Legge dal DB i valori cifrati e li decifra con la chiave corrente."""
    from .settings_store import decrypt

    out: dict = {"app_settings": {}, "users_totp": {}}
    con = sqlite3.connect(db_path())
    try:
        for key, value, secret in con.execute("SELECT key, value, secret FROM app_settings"):
            if secret and value:
                try:
                    out["app_settings"][key] = decrypt(value)
                except Exception:
                    log.warning("Impossibile decifrare %s: escluso dal backup", key)
        for uid, tsec in con.execute("SELECT id, totp_secret FROM users WHERE totp_secret IS NOT NULL AND totp_secret != ''"):
            try:
                out["users_totp"][uid] = decrypt(tsec)
            except Exception:
                pass
    finally:
        con.close()
    return out


def _counts() -> dict:
    con = sqlite3.connect(db_path())
    try:
        c = {}
        for t in ("users", "conversations", "messages", "projects", "files", "artifacts", "mcp_servers", "app_settings"):
            try:
                c[t] = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            except sqlite3.Error:
                c[t] = 0
        return c
    finally:
        con.close()


def info() -> dict:
    d = settings.data_dir
    size = 0
    for sub in ("uploads", "generated", "versions", "branding", "installation", "education", "browser"):
        p = d / sub
        if p.exists():
            size += sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
    dbp = db_path()
    return {"counts": _counts(), "db_bytes": dbp.stat().st_size if dbp.exists() else 0, "files_bytes": size,
            "pending_restore": (_pending_dir() / "manifest.json").exists()}


def create(password: str, version: str) -> Path:
    """Crea l'archivio cifrato in data/backups e ne restituisce il percorso."""
    if len(password) < 8:
        raise ValueError("La password del backup deve avere almeno 8 caratteri")
    out_dir = settings.data_dir / "backups"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    tmp_db = out_dir / f".db-{stamp}.sqlite"

    # copia consistente del DB (anche con WAL attivo)
    src = sqlite3.connect(db_path())
    dst = sqlite3.connect(tmp_db)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()

    manifest = {
        "format": FORMAT_VERSION, "app_version": version, "created_at": datetime.now(timezone.utc).isoformat(),
        "counts": _counts(),
        "env_hint": {"public_host": settings.public_host, "webauthn_rp_id": settings.webauthn_rp_id,
                     "webauthn_origin": settings.webauthn_origin, "family_name": settings.family_name},
    }
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        def add_bytes(name: str, data: bytes):
            ti = tarfile.TarInfo(name)
            ti.size = len(data)
            ti.mtime = int(time.time())
            tar.addfile(ti, io.BytesIO(data))
        add_bytes("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=1).encode())
        add_bytes("secrets.json", json.dumps(_collect_secrets()).encode())
        tar.add(tmp_db, arcname="familai.db")
        for sub in ("uploads", "generated", "versions", "branding", "installation", "education", "browser"):
            p = settings.data_dir / sub
            if p.exists():
                tar.add(p, arcname=sub)
    tmp_db.unlink(missing_ok=True)

    salt = os.urandom(12)
    token = Fernet(_key(password, salt)).encrypt(buf.getvalue())
    out = out_dir / f"familai-backup-{stamp}.fab"
    out.write_bytes(MAGIC + salt + token)
    return out


# ------------------------------------------------------------------ import
def _open(data: bytes, password: str) -> bytes:
    if data[:4] != MAGIC:
        raise ValueError("Non e' un file di backup FamilAI (.fab)")
    salt, token = data[4:16], data[16:]
    try:
        return Fernet(_key(password, salt)).decrypt(token)
    except InvalidToken:
        raise ValueError("Password del backup errata o file danneggiato")


def stage(data: bytes, password: str) -> dict:
    """Decifra e scompatta in data/pending_restore; il ripristino vero avviene al riavvio. Restituisce il manifest."""
    plain = _open(data, password)
    pend = _pending_dir()
    if pend.exists():
        shutil.rmtree(pend)
    pend.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(plain), mode="r:gz") as tar:
        names = tar.getnames()
        if "manifest.json" not in names or "familai.db" not in names:
            raise ValueError("Archivio incompleto (manca manifest o database)")
        for m in tar.getmembers():
            # sicurezza: niente percorsi assoluti o con '..'
            if m.name.startswith("/") or ".." in Path(m.name).parts:
                raise ValueError(f"Percorso non valido nell'archivio: {m.name}")
        tar.extractall(pend)
    manifest = json.loads((pend / "manifest.json").read_text())
    if int(manifest.get("format", 0)) > FORMAT_VERSION:
        shutil.rmtree(pend)
        raise ValueError("Backup creato da una versione piu' recente di FamilAI: aggiorna prima l'installazione")
    # verifica che il DB sia leggibile
    con = sqlite3.connect(pend / "familai.db")
    try:
        con.execute("SELECT COUNT(*) FROM users").fetchone()
    finally:
        con.close()
    return manifest


def cancel_pending() -> None:
    pend = _pending_dir()
    if pend.exists():
        shutil.rmtree(pend)


def apply_pending() -> bool:
    """Chiamato all'avvio PRIMA di aprire il DB: sostituisce DB e file, ricifra i segreti con la SECRET_KEY corrente."""
    pend = _pending_dir()
    if not (pend / "manifest.json").exists():
        return False
    from .settings_store import encrypt

    log.warning("Ripristino backup in corso da %s", pend)
    dbp = db_path()
    dbp.parent.mkdir(parents=True, exist_ok=True)
    # metti da parte lo stato attuale (per sicurezza) e sostituisci
    keep = settings.data_dir / f"pre-restore-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    keep.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(dbp) + suffix)
        if p.exists():
            shutil.move(str(p), keep / p.name)
    for sub in ("uploads", "generated", "versions", "branding", "installation", "education", "browser"):
        p = settings.data_dir / sub
        if p.exists():
            shutil.move(str(p), keep / sub)
    shutil.copy2(pend / "familai.db", dbp)
    for sub in ("uploads", "generated", "versions", "branding", "installation", "education", "browser"):
        src = pend / sub
        if src.exists():
            shutil.copytree(src, settings.data_dir / sub)

    secrets = json.loads((pend / "secrets.json").read_text()) if (pend / "secrets.json").exists() else {}
    con = sqlite3.connect(dbp)
    try:
        for key, val in secrets.get("app_settings", {}).items():
            con.execute("UPDATE app_settings SET value=? WHERE key=?", (encrypt(val), key))
        for uid, sec in secrets.get("users_totp", {}).items():
            con.execute("UPDATE users SET totp_secret=? WHERE id=?", (encrypt(sec), uid))
        for t in ("sessions", "email_tokens"):
            try:
                con.execute(f"DELETE FROM {t}")
            except sqlite3.Error:
                pass
        con.commit()
    finally:
        con.close()
    shutil.rmtree(pend)
    log.warning("Ripristino completato. Copia dello stato precedente in %s (puoi cancellarla)", keep)
    return True
