import hashlib
import hmac
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

_ph = PasswordHasher(time_cost=3, memory_cost=64 * 1024, parallelism=2)


def hash_secret(value: str) -> str:
    return _ph.hash(value)


def verify_secret(hashed: str | None, value: str) -> bool:
    if not hashed:
        return False
    try:
        return _ph.verify(hashed, value)
    except VerifyMismatchError:
        return False
    except Exception:
        return False


def generate_recovery_codes(n: int = 8) -> tuple[list[str], list[str]]:
    """Ritorna (codici in chiaro, hash da salvare)."""
    plain = [f"{secrets.token_hex(2)}-{secrets.token_hex(2)}-{secrets.token_hex(2)}" for _ in range(n)]
    return plain, [hash_recovery_code(c) for c in plain]


def hash_recovery_code(code: str) -> str:
    return hashlib.sha256(code.strip().lower().encode()).hexdigest()


def consume_recovery_code(stored: list[str], code: str) -> list[str] | None:
    h = hash_recovery_code(code)
    for s in stored:
        if hmac.compare_digest(s, h):
            return [x for x in stored if x != s]
    return None
