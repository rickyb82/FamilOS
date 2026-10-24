"""Comandi di emergenza (eseguiti dentro il container backend, vedi familai.sh):

  python -m app.cli reset-passkey EMAIL        rimuove le passkey e i codici di recupero: al prossimo login si registra di nuovo
  python -m app.cli set-password EMAIL PASS    imposta una nuova password (e sblocca l'account)
  python -m app.cli unlock EMAIL               azzera blocco e tentativi falliti
  python -m app.cli list-users                 elenca gli utenti
"""
from __future__ import annotations

import asyncio
import sys

from sqlalchemy import delete, select

from .db import SessionLocal, init_db
from .models import Session, User, WebAuthnCredential
from .security.passwords import hash_secret


async def _user(db, email: str) -> User:
    u = (await db.execute(select(User).where(User.email == email.lower()))).scalar_one_or_none()
    if not u:
        print(f"Utente {email} non trovato"); sys.exit(1)
    return u


async def main(argv: list[str]) -> None:
    await init_db()
    cmd = argv[0] if argv else "help"
    async with SessionLocal() as db:
        if cmd == "list-users":
            for u in (await db.execute(select(User))).scalars():
                n = len((await db.execute(select(WebAuthnCredential.id).where(WebAuthnCredential.user_id == u.id))).all())
                print(f"{u.role.value:6} {u.display_name:20} {u.email or u.username:30} passkey={n} authenticator={bool(u.totp_secret)} attivo={u.is_active}")
        elif cmd == "reset-passkey" and len(argv) == 2:
            u = await _user(db, argv[1])
            await db.execute(delete(WebAuthnCredential).where(WebAuthnCredential.user_id == u.id))
            await db.execute(delete(Session).where(Session.user_id == u.id))
            u.recovery_codes, u.failed_logins, u.locked_until = [], 0, None
            u.totp_secret, u.totp_pending = None, None
            await db.commit()
            print(f"Secondo fattore azzerato per {u.email} (passkey e app authenticator): accedi con la password e attivane uno nuovo.")
        elif cmd == "set-password" and len(argv) == 3:
            u = await _user(db, argv[1])
            u.password_hash = hash_secret(argv[2])
            u.failed_logins, u.locked_until = 0, None
            await db.execute(delete(Session).where(Session.user_id == u.id))
            await db.commit()
            print(f"Password aggiornata per {u.email}.")
        elif cmd == "unlock" and len(argv) == 2:
            u = await _user(db, argv[1])
            u.failed_logins, u.locked_until = 0, None
            await db.commit()
            print(f"Account {u.email} sbloccato.")
        else:
            print(__doc__)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
