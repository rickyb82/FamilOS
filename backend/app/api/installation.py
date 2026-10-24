"""Public URL, OAuth/passkey identity and TLS deployment settings (administrators only)."""
from __future__ import annotations
import asyncio
import ipaddress
import json
import os
import re
import ssl
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from ..config import get_settings
from ..security.sessions import require_admin

router = APIRouter(prefix="/admin/installation", tags=["installation"])
_lock = asyncio.Lock()


def directory() -> Path:
    path = get_settings().data_dir / "installation"
    path.mkdir(parents=True, exist_ok=True)
    return path


def normalize_url(value: str) -> tuple[str, str, str]:
    value = value.strip()
    if any(ord(c) < 33 for c in value):
        raise HTTPException(400, "Il dominio non può contenere spazi o caratteri di controllo.")
    if "://" not in value:
        value = "https://" + value
    try:
        parts = urlsplit(value)
        host = (parts.hostname or "").encode("idna").decode().lower()
        port = parts.port
        if not host or parts.username or parts.password or parts.query or parts.fragment or parts.path not in ("", "/"):
            raise ValueError()
        if not re.fullmatch(r"(?=.{1,253}$)[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", host):
            raise ValueError()
        if any(not label or len(label) > 63 or label.startswith("-") or label.endswith("-") for label in host.split(".")):
            raise ValueError()
        local = host in ("localhost", "127.0.0.1")
        if parts.scheme not in ("http", "https") or parts.scheme == "http" and not local:
            raise ValueError()
        if port is not None and not 1 <= port <= 65535:
            raise ValueError()
        authority = host + (f":{port}" if port and (parts.scheme, port) not in (("https",443), ("http",80)) else "")
        return f"{parts.scheme}://{authority}", authority, host
    except (ValueError, UnicodeError):
        raise HTTPException(400, "Indica un dominio HTTPS senza percorsi, credenziali o parametri. HTTP è ammesso solo su localhost.")


def read_config() -> dict:
    try:
        return json.loads((directory() / "config.json").read_text())
    except (OSError, ValueError):
        s = get_settings()
        return {"public_url": s.webauthn_origin, "tls_mode": os.getenv("TLS_MODE", "acme"),
                "acme_email": os.getenv("ACME_EMAIL", ""), "acme_staging": False}


def apply_runtime(config: dict | None = None) -> None:
    if config is None and not (directory() / "config.json").exists():
        return
    config = config or read_config()
    origin, authority, host = normalize_url(config["public_url"])
    s = get_settings()
    s.public_host, s.webauthn_origin, s.webauthn_rp_id = authority, origin, host
    s.cookie_secure = origin.startswith("https://")
    # Challenges/OAuth attempts issued for the previous origin must be restarted.
    from . import google_auth
    from ..security import webauthn_svc
    google_auth._pending.clear()
    webauthn_svc._challenges.clear()


def certificate_info(data: bytes) -> dict:
    cert = x509.load_pem_x509_certificate(data)
    try:
        names = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.DNSName)
    except x509.ExtensionNotFound:
        names = []
    return {"domains": names, "issuer": cert.issuer.rfc4514_string(),
            "expires_at": cert.not_valid_after_utc.isoformat(),
            "days_remaining": (cert.not_valid_after_utc - datetime.now(timezone.utc)).days}


def cert_covers(cert: x509.Certificate, host: str) -> bool:
    try:
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        try:
            return ipaddress.ip_address(host) in san.get_values_for_type(x509.IPAddress)
        except ValueError:
            return any(host == name.lower() or (name.startswith("*.") and host.split(".",1)[-1] == name[2:].lower())
                       for name in san.get_values_for_type(x509.DNSName))
    except x509.ExtensionNotFound:
        return False


@router.get("")
async def installation(ctx=Depends(require_admin)):
    config = read_config()
    origin, _, host = normalize_url(config["public_url"])
    manual = None
    version = config.get("manual_version", "")
    if re.fullmatch(r"[a-f0-9]{32}", version):
        try:
            manual = certificate_info((directory() / "tls" / version / "certificate.pem").read_bytes())
        except (OSError, ValueError):
            pass
    return {**{k:v for k,v in config.items() if k != "manual_version"},
            "rp_id": host, "google_origin": origin,
            "google_redirect_uri": origin + "/api/auth/google/callback",
            "gmail_redirect_uri": origin + "/api/admin/email/google/callback",
            "google_client_id": get_settings().google_client_id,
            "google_secret_configured": bool(get_settings().google_client_secret),
            "manual_certificate": manual,
            "proxy_pending": bool((directory() / "config.json").exists() and (
                os.getenv("INSTALLATION_REVISION", "") != config.get("revision", "") or
                os.getenv("PUBLIC_HOST", "localhost") != urlsplit(origin).netloc or
                os.getenv("TLS_MODE", "acme") != config["tls_mode"] or
                os.getenv("ACME_EMAIL", "") != config.get("acme_email", "") or
                os.getenv("ACME_STAGING", "false") != str(config.get("acme_staging", False)).lower())),
            "apply_command": "./familos.sh domain apply"}


class InstallationUpdate(BaseModel):
    public_url: str = Field(min_length=1, max_length=300)
    tls_mode: str = "acme"
    acme_email: str = Field(default="", max_length=254)
    acme_staging: bool = False


@router.put("")
async def update_installation(body: InstallationUpdate, ctx=Depends(require_admin)):
    origin, authority, host = normalize_url(body.public_url)
    if body.tls_mode not in ("acme", "internal", "manual"):
        raise HTTPException(400, "Scegli Let’s Encrypt, CA interna o certificato personale.")
    if body.tls_mode == "acme":
        try:
            ipaddress.ip_address(host)
            is_ip = True
        except ValueError:
            is_ip = False
        if origin.startswith("http:") or host == "localhost" or "." not in host or is_ip:
            raise HTTPException(400, "Let’s Encrypt richiede un dominio pubblico HTTPS.")
        from pydantic import TypeAdapter, EmailStr
        try:
            email = str(TypeAdapter(EmailStr).validate_python(body.acme_email.strip()))
        except ValueError:
            raise HTTPException(400, "Indica un’email valida per Let’s Encrypt.")
    else:
        email = body.acme_email.strip()
    if email and not re.fullmatch(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", email):
        raise HTTPException(400, "Indica un indirizzo email semplice, senza nome visualizzato.")
    async with _lock:
        config = read_config()
        if body.tls_mode == "manual":
            version = config.get("manual_version", "")
            if not re.fullmatch(r"[a-f0-9]{32}", version):
                raise HTTPException(400, "Carica prima certificato e chiave privata.")
            cert = x509.load_pem_x509_certificate((directory() / "tls" / version / "certificate.pem").read_bytes())
            if not cert_covers(cert, host) or cert.not_valid_after_utc <= datetime.now(timezone.utc):
                raise HTTPException(400, "Il certificato deve essere valido e coprire il dominio scelto.")
        config.update(public_url=origin, tls_mode=body.tls_mode, acme_email=email, acme_staging=body.acme_staging)
        write_config(config)
        apply_runtime(config)
    return await installation(ctx)


def write_config(config: dict) -> None:
    origin, authority, host = normalize_url(config["public_url"])
    config["revision"] = uuid.uuid4().hex
    path = directory() / "config.json"
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(config))
    os.replace(temp, path)
    # Only validated, allowlisted values; the host script never evaluates this file.
    values = {"PUBLIC_HOST": authority, "WEBAUTHN_RP_ID": host, "WEBAUTHN_ORIGIN": origin,
              "COOKIE_SECURE": str(origin.startswith("https:")).lower(), "TLS_MODE": config["tls_mode"],
              "ACME_EMAIL": config.get("acme_email", ""), "ACME_STAGING": str(config.get("acme_staging",False)).lower(),
              "ACME_CA": "https://acme-staging-v02.api.letsencrypt.org/directory" if config.get("acme_staging") else "https://acme-v02.api.letsencrypt.org/directory",
              "HTTPS_PORT": str(urlsplit(origin).port or 443), "INSTALLATION_REVISION": config["revision"]}
    env = directory() / "domain.env"
    temp = env.with_suffix(".tmp")
    temp.write_text("".join(f"{k}={v}\n" for k,v in values.items()))
    os.replace(temp, env)


@router.post("/certificate")
async def upload_certificate(public_url: str = Form(...), certificate: UploadFile = File(...),
                             private_key: UploadFile = File(...), ctx=Depends(require_admin)):
    _, _, host = normalize_url(public_url)
    cert_data, key_data = await certificate.read(256*1024+1), await private_key.read(32*1024+1)
    if len(cert_data) > 256*1024 or len(key_data) > 32*1024:
        raise HTTPException(413, "File del certificato troppo grandi.")
    try:
        cert = x509.load_pem_x509_certificate(cert_data)
        key = serialization.load_pem_private_key(key_data, password=None)
        encode = lambda public: public.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        if encode(cert.public_key()) != encode(key.public_key()):
            raise ValueError("La chiave non corrisponde al certificato.")
        now = datetime.now(timezone.utc)
        if not cert.not_valid_before_utc <= now < cert.not_valid_after_utc:
            raise ValueError("Il certificato non è valido alla data corrente.")
        if not cert_covers(cert, host):
            raise ValueError("Il certificato non copre il dominio scelto.")
    except (ValueError, TypeError) as error:
        raise HTTPException(400, str(error) or "Certificato o chiave PEM non validi.")
    async with _lock:
        version = uuid.uuid4().hex
        target = directory() / "tls" / version
        target.mkdir(parents=True, mode=0o700)
        (target / "certificate.pem").write_bytes(cert_data)
        with os.fdopen(os.open(target / "private-key.pem", os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600), "wb") as output:
            output.write(key_data)
        current = directory() / "tls" / "current.caddy"
        temp = current.with_suffix(".tmp")
        temp.write_text(f"tls /etc/caddy/installation/tls/{version}/certificate.pem /etc/caddy/installation/tls/{version}/private-key.pem\n")
        os.replace(temp,current)
        config = read_config()
        config["manual_version"] = version
        # Upload stages the certificate; changing the active TLS mode is a separate Save.
        write_config(config)
    return certificate_info(cert_data)


@router.get("/certificate/status")
async def certificate_status(ctx=Depends(require_admin)):
    origin, _, host = normalize_url(read_config()["public_url"])
    if not origin.startswith("https:"):
        return {"ok": False, "error": "L’indirizzo configurato usa HTTP."}
    writer = None
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, urlsplit(origin).port or 443,
                ssl=ssl.create_default_context(), server_hostname=host), timeout=8)
        der = writer.get_extra_info("ssl_object").getpeercert(binary_form=True)
        pem = ssl.DER_cert_to_PEM_cert(der).encode()
        return {"ok": True, **certificate_info(pem)}
    except (OSError, ValueError, asyncio.TimeoutError) as error:
        return {"ok": False, "error": str(error)[:250]}
    finally:
        if writer:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass
