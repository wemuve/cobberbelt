"""Password hashing (Argon2id) and signed JWT access/refresh tokens."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from copperbelt.config import get_settings

_hasher = PasswordHasher()
MIN_PASSWORD_LENGTH = 12


class TokenError(Exception):
    pass


def validate_password_strength(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters")
    if password.lower() == password or password.upper() == password or not any(c.isdigit() for c in password):
        raise ValueError("Password must mix upper- and lower-case letters and include a digit")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


# A constant hash used to equalise timing when the user does not exist.
_DUMMY_HASH = _hasher.hash("timing-equaliser-not-a-password")


def verify_dummy() -> None:
    verify_password(_DUMMY_HASH, "x")


def create_token(user_id: uuid.UUID, token_version: int, kind: Literal["access", "refresh"]) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    ttl = timedelta(minutes=settings.access_token_minutes) if kind == "access" else timedelta(days=settings.refresh_token_days)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "tv": token_version,
        "typ": kind,
        "iss": settings.jwt_issuer,
        "iat": int(now.timestamp()),
        "exp": int((now + ttl).timestamp()),
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, settings.jwt_secret.get_secret_value(), algorithm="HS256")


def decode_token(token: str, expected: Literal["access", "refresh"]) -> dict[str, Any]:
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            algorithms=["HS256"],
            issuer=settings.jwt_issuer,
            options={"require": ["sub", "exp", "iat", "typ", "tv"]},
        )
    except jwt.PyJWTError as exc:
        raise TokenError("Invalid or expired token") from exc
    if payload.get("typ") != expected:
        raise TokenError("Wrong token type")
    return payload
