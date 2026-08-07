"""Password hashing, JWTs, API keys, secret encryption, identity verification."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from datetime import timedelta
from typing import Any, Literal

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings
from app.core.db import utcnow, uuid7
from app.core.errors import UnauthorizedError

_hasher = PasswordHasher()

TokenType = Literal[
    "access", "refresh", "widget", "password_reset", "recorder", "extension", "tour_preview"
]

API_KEY_PREFIX = "sk_stept_"

# Single source of truth for tour-recorder/extension token lifetimes (imported by routers).
RECORDER_TOKEN_TTL_DAYS = 7
EXTENSION_TOKEN_TTL_DAYS = 30


# ---------------------------------------------------------------------------
# passwords
# ---------------------------------------------------------------------------


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    if not password_hash:
        return False  # social-login-only accounts have no password to verify
    try:
        return _hasher.verify(password_hash, password)
    except VerificationError:
        return False


# ---------------------------------------------------------------------------
# JWTs
# ---------------------------------------------------------------------------


def _encode(claims: dict[str, Any], ttl: timedelta, token_type: TokenType) -> str:
    now = utcnow()
    payload = {
        **claims,
        "iss": "stept",
        "typ": token_type,
        "iat": int(now.timestamp()),
        "exp": int((now + ttl).timestamp()),
    }
    return jwt.encode(payload, get_settings().secret_key, algorithm="HS256")


def decode_token(token: str, expected_type: TokenType) -> dict[str, Any]:
    try:
        payload = jwt.decode(token, get_settings().secret_key, algorithms=["HS256"], issuer="stept")
    except jwt.PyJWTError as exc:
        raise UnauthorizedError("Invalid or expired token") from exc
    if payload.get("typ") != expected_type:
        raise UnauthorizedError("Invalid token type")
    return payload


def create_access_token(user_id: str) -> str:
    settings = get_settings()
    return _encode({"sub": user_id}, timedelta(minutes=settings.access_token_ttl_minutes), "access")


def create_refresh_token(user_id: str, jti: str | None = None) -> tuple[str, str]:
    """Returns (token, jti). The jti is persisted for rotation/reuse detection."""
    settings = get_settings()
    jti = jti or uuid7()
    token = _encode(
        {"sub": user_id, "jti": jti}, timedelta(days=settings.refresh_token_ttl_days), "refresh"
    )
    return token, jti


def create_password_reset_token(user_id: str) -> str:
    return _encode({"sub": user_id}, timedelta(hours=1), "password_reset")


def create_widget_token(workspace_id: str, contact_id: str) -> str:
    return _encode({"ws": workspace_id, "sub": contact_id}, timedelta(days=30), "widget")


def create_recorder_token(workspace_id: str, user_id: str) -> str:
    """Short-lived token pasted into the tour-recorder browser extension."""
    return _encode(
        {"ws": workspace_id, "sub": user_id}, timedelta(days=RECORDER_TOKEN_TTL_DAYS), "recorder"
    )


def create_extension_token(workspace_id: str, user_id: str) -> str:
    """Long-lived workspace-scoped token minted for the logged-in Chrome extension."""
    return _encode(
        {"ws": workspace_id, "sub": user_id}, timedelta(days=EXTENSION_TOKEN_TTL_DAYS), "extension"
    )


def create_tour_preview_token(workspace_id: str, tour_id: str) -> str:
    """One-hour token that lets the widget fetch a single tour regardless of status."""
    return _encode({"ws": workspace_id, "tour": tour_id}, timedelta(hours=1), "tour_preview")


# ---------------------------------------------------------------------------
# API keys — stored as sha256, shown once at creation
# ---------------------------------------------------------------------------


def generate_api_key() -> tuple[str, str, str]:
    """Returns (full_key, display_prefix, sha256_hex)."""
    full = API_KEY_PREFIX + secrets.token_urlsafe(32)
    return full, full[: len(API_KEY_PREFIX) + 6], hash_api_key(full)


def hash_api_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


# ---------------------------------------------------------------------------
# secret encryption at rest (provider keys, channel tokens)
# ---------------------------------------------------------------------------


def _fernet() -> Fernet:
    digest = hashlib.sha256(get_settings().secret_key.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt_secret(value: str) -> str:
    try:
        return _fernet().decrypt(value.encode()).decode()
    except InvalidToken as exc:
        raise UnauthorizedError("Cannot decrypt stored secret (secret key changed?)") from exc


# ---------------------------------------------------------------------------
# identity verification (Intercom-style HMAC of the external user id)
# ---------------------------------------------------------------------------


def compute_identity_hash(identity_secret: str, external_id: str) -> str:
    return hmac.new(identity_secret.encode(), external_id.encode(), hashlib.sha256).hexdigest()


def verify_identity_hash(identity_secret: str, external_id: str, candidate: str) -> bool:
    expected = compute_identity_hash(identity_secret, external_id)
    return hmac.compare_digest(expected, candidate)


def new_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)
