from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import jwt
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(PROJECT_ROOT / ".env")

_SCRYPT_N = 32768
_SCRYPT_R = 8
_SCRYPT_P = 1
_DUMMY_HASH = (
    "scrypt$32768$8$1$AAAAAAAAAAAAAAAAAAAAAA==$"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
)


def validate_auth_config() -> None:
    secret = os.getenv("AUTH_JWT_SECRET", "")
    if len(secret) < 32:
        raise RuntimeError("AUTH_JWT_SECRET 至少需要 32 个字符。")
    if os.getenv("APP_ENV") == "production" and "change" in secret.lower():
        raise RuntimeError("生产环境不能使用示例 JWT 密钥。")
    minutes = int(os.getenv("ACCESS_TOKEN_MINUTES", "120"))
    if minutes < 5 or minutes > 1440:
        raise RuntimeError("ACCESS_TOKEN_MINUTES 必须在 5 到 1440 之间。")


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=32,
        maxmem=64 * 1024 * 1024,
    )
    return "$".join(
        [
            "scrypt",
            str(_SCRYPT_N),
            str(_SCRYPT_R),
            str(_SCRYPT_P),
            base64.b64encode(salt).decode("ascii"),
            base64.b64encode(digest).decode("ascii"),
        ],
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt_text, digest_text = encoded.split("$")
        if algorithm != "scrypt":
            return False
        digest = hashlib.scrypt(
            password.encode("utf-8"),
            salt=base64.b64decode(salt_text),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=32,
            maxmem=64 * 1024 * 1024,
        )
        return hmac.compare_digest(digest, base64.b64decode(digest_text))
    except (ValueError, TypeError, binascii.Error):
        return False


def dummy_password_check(password: str) -> None:
    verify_password(password, _DUMMY_HASH)


def create_access_token(user: dict[str, Any]) -> tuple[str, int]:
    validate_auth_config()
    minutes = int(os.getenv("ACCESS_TOKEN_MINUTES", "120"))
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user["id"],
        "username": user["username"],
        "role": user["role"],
        "tenant_id": user["tenant_id"],
        "iat": now,
        "exp": now + timedelta(minutes=minutes),
        "jti": secrets.token_urlsafe(16),
    }
    token = jwt.encode(payload, os.environ["AUTH_JWT_SECRET"], algorithm="HS256")
    return token, minutes * 60


def decode_access_token(token: str) -> dict[str, Any]:
    validate_auth_config()
    payload = jwt.decode(
        token,
        os.environ["AUTH_JWT_SECRET"],
        algorithms=["HS256"],
        options={"require": ["exp", "iat", "sub", "role", "tenant_id"]},
    )
    if not payload.get("tenant_id"):
        raise jwt.InvalidTokenError("Missing tenant")
    if payload.get("role") not in {"user", "admin"}:
        raise jwt.InvalidTokenError("Invalid role")
    return payload
