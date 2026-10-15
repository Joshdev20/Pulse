"""Password hashing and JWT helpers."""

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

import jwt

from .config import settings

_ITERATIONS = 260_000
_DIGEST = "sha256"
# Stored in the hash prefix so the algorithm can be changed without breaking old hashes.
_SCHEME = f"pbkdf2_{_DIGEST}"


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(_DIGEST, password.encode(), salt.encode(), _ITERATIONS)
    return f"{_SCHEME}${_ITERATIONS}${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, iterations, salt, expected = stored.split("$")
        if not scheme.startswith("pbkdf2_"):
            return False
        digest_name = scheme.removeprefix("pbkdf2_")
        digest = hashlib.pbkdf2_hmac(digest_name, password.encode(), salt.encode(), int(iterations))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest.hex(), expected)


def create_access_token(subject: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.access_token_expire_minutes)
    payload = {"sub": subject, "exp": expire, "iat": datetime.now(timezone.utc)}
    return jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)


def decode_access_token(token: str) -> str | None:
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
    except jwt.PyJWTError:
        return None
    subject = payload.get("sub")
    return subject if isinstance(subject, str) else None
