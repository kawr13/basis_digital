"""Криптография доступа: пароли, JWT, refresh-токены.

Зависимостей на C нет намеренно (на сервере может не быть компилятора):
пароли — hashlib.scrypt (stdlib), токены — PyJWT.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import jwt

from .config import get_settings
from .models import RefreshToken, utcnow

# scrypt: 16 МБ на хэш — комфортно и для API-логина, и для SQLite-разработки.
_SCRYPT_N = 2 ** 14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_MAXMEM = 64 * 1024 * 1024


# ---------------------------------------------------------------- пароли ----

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(
        password.encode("utf-8"), salt=salt,
        n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32, maxmem=_SCRYPT_MAXMEM,
    )
    # формат: scrypt$N$r$salt$hash — без ведущего разделителя, иначе split() даёт пустой элемент
    return "scrypt${}${}${}${}".format(
        _SCRYPT_N, _SCRYPT_R, base64.b64encode(salt).decode(), base64.b64encode(dk).decode()
    )


def verify_password(password: str, stored: str) -> bool:
    if not stored:
        return False
    try:
        parts = stored.split("$")
        if parts and parts[0] == "":  # совместимость со старым форматом с ведущим $
            parts = parts[1:]
        if len(parts) != 5:
            return False
        tag, n, r, salt_b64, hash_b64 = parts
        if tag != "scrypt":
            return False
        dk = hashlib.scrypt(
            password.encode("utf-8"), salt=base64.b64decode(salt_b64),
            n=int(n), r=int(r), p=_SCRYPT_P, dklen=len(base64.b64decode(hash_b64)),
            maxmem=_SCRYPT_MAXMEM,
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(dk, base64.b64decode(hash_b64))


def password_is_strong(password: str) -> tuple[bool, str]:
    if len(password) < 8:
        return False, "Минимум 8 символов"
    if password.isdigit() or password.isalpha():
        return False, "Нужны буквы и цифры"
    return True, ""


def random_password(length: int = 14) -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
    return "".join(secrets.choice(alphabet) for _ in range(length))


# ------------------------------------------------------------------ JWT ----

def create_access_token(user_id: int, role: str, extra: dict | None = None) -> tuple[str, int]:
    s = get_settings()
    ttl = s.access_token_ttl_minutes * 60
    now = int(datetime.now(timezone.utc).timestamp())
    payload = {
        "sub": str(user_id),
        "role": role,
        "typ": "access",
        "iat": now,
        "exp": now + ttl,
        "jti": uuid.uuid4().hex,
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, s.secret_key, algorithm=s.jwt_algorithm), ttl


def decode_access_token(token: str) -> dict | None:
    s = get_settings()
    try:
        payload = jwt.decode(token, s.secret_key, algorithms=[s.jwt_algorithm])
    except jwt.PyJWTError:
        return None
    if payload.get("typ") != "access":
        return None
    return payload


# ------------------------------------------------------- refresh-токены ----

def new_refresh_token(user_id: int, family_id: str | None = None,
                      ip: str | None = None, user_agent: str | None = None) -> tuple[RefreshToken, str]:
    """Возвращает (строка_для_клиента, запись_в_БД). В БД — только SHA-256 от строки."""
    s = get_settings()
    raw = secrets.token_urlsafe(48)
    rec = RefreshToken(
        user_id=user_id,
        token_hash=hashlib.sha256(raw.encode()).hexdigest(),
        family_id=family_id or uuid.uuid4().hex,
        expires_at=utcnow() + timedelta(days=s.refresh_token_ttl_days),
        ip=ip,
        user_agent=(user_agent or "")[:256] or None,
    )
    return raw, rec


def hash_refresh(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
