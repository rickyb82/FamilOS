"""Invio email via SMTP (configurato dal pannello: smtp_*) o Gmail OAuth. Usa la libreria standard in un thread.

Allegati facoltativi (`attachments`: lista di (nome file, content type, byte)), per esempio i PDF dei report di NetCraft, e
immagini dentro l'HTML (`inline`: lista di (nome, content type, byte, content-id), richiamate con <img src="cid:…">): il messaggio
diventa multipart/mixed[ multipart/alternative[ testo, multipart/related[ html, immagini ] ], allegati ]. Con Gmail OAuth un
messaggio con allegati o immagini passa dall'endpoint di upload (fino a 35 MB) invece che dal JSON."""
from __future__ import annotations

import asyncio
import logging
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr

from ..config import get_settings

log = logging.getLogger(__name__)
settings = get_settings()

Attachment = tuple[str, str, bytes]          # (nome del file, content type, contenuto)
InlineImage = tuple[str, str, bytes, str]    # (nome del file, content type, contenuto, content-id senza <>)


def _one_line(text: str) -> str:
    """Un header su una riga sola: niente CR/LF (che aggiungerebbero header) ne' altri caratteri di controllo."""
    return " ".join("".join(c if c >= " " and c != "\x7f" else " " for c in str(text or "")).split())


def _add_attachments(msg: EmailMessage, attachments: list[Attachment] | None) -> None:
    for name, ctype, data in attachments or ():
        maintype, _, subtype = (ctype or "").lower().partition("/")
        if not maintype or not subtype or maintype in ("multipart", "message"):
            maintype, subtype = "application", "octet-stream"
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=_one_line(name) or "allegato")


def _add_inline(msg: EmailMessage, inline: list[InlineImage] | None) -> None:
    """Le immagini richiamate dall'HTML (cid:…) accanto alla parte HTML, in un multipart/related. Serve una parte HTML."""
    if not inline:
        return
    html_part = next((p for p in msg.iter_parts() if p.get_content_type() == "text/html"), None) if msg.is_multipart() else None
    if html_part is None:
        return
    for name, ctype, data, cid in inline:
        maintype, _, subtype = (ctype or "").lower().partition("/")
        if maintype != "image" or not subtype:
            continue
        html_part.add_related(data, maintype=maintype, subtype=subtype, cid=f"<{_one_line(cid)}>", disposition="inline",
                              filename=_one_line(name) or "immagine")


def gmail_oauth_ready() -> bool:
    return bool(settings.gmail_oauth_refresh_token and settings.google_client_id and settings.google_client_secret)


def transport() -> str:
    t = (settings.mail_transport or "auto").lower()
    if t == "gmail_oauth":
        return "gmail_oauth" if gmail_oauth_ready() else "none"
    if t == "smtp":
        return "smtp" if settings.smtp_host and settings.smtp_from else "none"
    if gmail_oauth_ready():
        return "gmail_oauth"
    return "smtp" if settings.smtp_host and settings.smtp_from else "none"


def email_enabled() -> bool:
    return transport() != "none"


def sender_address() -> str:
    return settings.gmail_oauth_email if transport() == "gmail_oauth" else settings.smtp_from


async def _gmail_access_token() -> str:
    import httpx

    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post("https://oauth2.googleapis.com/token", data={
            "client_id": settings.google_client_id, "client_secret": settings.google_client_secret,
            "refresh_token": settings.gmail_oauth_refresh_token, "grant_type": "refresh_token"})
        if r.status_code != 200:
            raise RuntimeError(f"Google ha rifiutato il token ({r.status_code}): {r.text[:200]}. Ricollega l'account Gmail dal pannello.")
        return r.json()["access_token"]


async def _send_gmail_api(to: str, subject: str, text: str, html: str | None, attachments: list[Attachment] | None = None,
                          inline: list[InlineImage] | None = None) -> None:
    import base64

    import httpx

    msg = EmailMessage()
    msg["From"] = formataddr((settings.smtp_from_name or settings.app_name, settings.gmail_oauth_email))
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype="html")
    if attachments or inline:
        _add_inline(msg, inline)
        _add_attachments(msg, attachments)
        token = await _gmail_access_token()
        async with httpx.AsyncClient(timeout=180) as c:   # con allegati: upload del messaggio intero (fino a 35 MB)
            r = await c.post("https://gmail.googleapis.com/upload/gmail/v1/users/me/messages/send?uploadType=media",
                             headers={"Authorization": f"Bearer {token}", "Content-Type": "message/rfc822"}, content=msg.as_bytes())
            if r.status_code >= 300:
                raise RuntimeError(f"Gmail API {r.status_code}: {r.text[:300]}")
        return
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    token = await _gmail_access_token()
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post("https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
                         headers={"Authorization": f"Bearer {token}"}, json={"raw": raw})
        if r.status_code >= 300:
            raise RuntimeError(f"Gmail API {r.status_code}: {r.text[:300]}")


def _send_sync(to: str, subject: str, text: str, html: str | None, attachments: list[Attachment] | None = None,
               inline: list[InlineImage] | None = None) -> None:
    msg = EmailMessage()
    msg["From"] = formataddr((settings.smtp_from_name or settings.app_name, settings.smtp_from))
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype="html")
    _add_inline(msg, inline)
    _add_attachments(msg, attachments)
    security = (settings.smtp_security or "starttls").lower()
    port = int(settings.smtp_port or (465 if security == "ssl" else 587))
    # coerenza porta/sicurezza: 465 = SSL implicito, 587/25 = STARTTLS (evita "Connection unexpectedly closed")
    if port == 465 and security != "ssl":
        security = "ssl"
    elif port in (587, 25) and security == "ssl":
        security = "starttls"
    ctx = ssl.create_default_context()
    if security == "ssl":
        server = smtplib.SMTP_SSL(settings.smtp_host, port, timeout=20, context=ctx)
    else:
        server = smtplib.SMTP(settings.smtp_host, port, timeout=20)
    with server:
        server.ehlo()
        if security == "starttls":
            server.starttls(context=ctx)
            server.ehlo()
        if settings.smtp_user:
            server.login(settings.smtp_user, settings.smtp_password)
        server.send_message(msg)


async def send_email(to: str, subject: str, text: str, html: str | None = None, attachments: list[Attachment] | None = None,
                     inline: list[InlineImage] | None = None) -> None:
    """attachments: [(nome, content type, byte)] e inline: [(nome, content type, byte, content-id)] facoltativi; il chiamante ne
    limita numero e dimensione."""
    t = transport()
    if t == "none":
        raise RuntimeError("Email non configurata: collega un account Google o imposta l'SMTP in Famiglia → Provider AI → Email")
    subject = _one_line(subject)
    if t == "gmail_oauth":
        await _send_gmail_api(to, subject, text, html, attachments, inline)
    else:
        await asyncio.to_thread(_send_sync, to, subject, text, html, attachments, inline)
    extra = len(attachments or ()) + len(inline or ())
    log.info("Email inviata a %s via %s: %s%s", to, t, subject, f" ({extra} allegati)" if extra else "")


def _wrap(title: str, body_html: str) -> str:
    return f"""<div style="font-family:system-ui,sans-serif;max-width:560px;margin:auto;padding:24px;color:#1c1f26">
<h2 style="margin:0 0 16px">🏠 {settings.webauthn_rp_name}</h2>
<h3 style="margin:0 0 12px">{title}</h3>
{body_html}
<p style="color:#6b7280;font-size:12px;margin-top:24px">Se non ti aspettavi questa email puoi ignorarla.</p></div>"""


def base_url() -> str:
    return settings.webauthn_origin.rstrip("/")


async def send_invite(to: str, display_name: str, code: str, invited_by: str) -> None:
    link = f"{base_url()}/reset?email={to}&code={code}"
    text = (f"Ciao {display_name},\n{invited_by} ti ha aggiunto a {settings.webauthn_rp_name}.\n"
            f"Imposta la tua password aprendo questo link (valido 48 ore):\n{link}\n\n"
            f"Dopo il primo accesso ti verra' chiesto di registrare una passkey (Face ID / Touch ID / Windows Hello).")
    html = _wrap(f"Benvenuto/a, {display_name}!",
                 f"<p>{invited_by} ti ha aggiunto all'assistente di famiglia.</p>"
                 f"<p><a href=\"{link}\" style=\"background:#1f6feb;color:#fff;padding:10px 16px;border-radius:8px;text-decoration:none\">Imposta la password</a></p>"
                 f"<p style=\"color:#6b7280\">Il link vale 48 ore. Dopo il primo accesso ti verrà chiesto di registrare una passkey.</p>")
    await send_email(to, f"Il tuo accesso a {settings.webauthn_rp_name}", text, html)


async def send_code(to: str, display_name: str, code: str, purpose: str) -> None:
    what = {"reset": "reimpostare la password", "mfa": "completare l'accesso"}.get(purpose, "continuare")
    text = f"Ciao {display_name},\nil tuo codice per {what} e': {code}\nVale 10 minuti. Non condividerlo con nessuno."
    html = _wrap(f"Codice per {what}",
                 f"<p style=\"font-size:32px;letter-spacing:6px;font-weight:700\">{code}</p><p>Vale 10 minuti. Non condividerlo con nessuno.</p>")
    await send_email(to, f"{code} è il tuo codice {settings.webauthn_rp_name}", text, html)
