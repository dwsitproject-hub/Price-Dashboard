"""Symmetric encryption for secrets at rest (LSEG token, PKCE verifiers) and token hashing."""
from __future__ import annotations

import hashlib
import secrets

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings


def _fernet() -> Fernet:
    return Fernet(get_settings().app_encryption_key.encode())


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str | None:
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken:
        return None


def random_token(nbytes: int = 32) -> str:
    """URL-safe random token (32 bytes = 256 bits → 43 chars)."""
    return secrets.token_urlsafe(nbytes)


def sha256(value: str) -> bytes:
    return hashlib.sha256(value.encode()).digest()
