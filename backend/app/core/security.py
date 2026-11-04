"""Password hashing (Argon2id), password policy and temporary-password generation."""
from __future__ import annotations

import re
import secrets
import string

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2, hash_len=32, salt_len=16)

PASSWORD_MIN = 12
PASSWORD_MAX = 128
PASSWORD_HISTORY = 5

# A constant dummy hash so that a login for an unknown email still pays the Argon2 cost
# (prevents user enumeration by timing).
_DUMMY_HASH = _hasher.hash("timing-equaliser-not-a-real-password")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def password_policy_errors(password: str, email: str | None = None) -> list[str]:
    """Return human-readable policy violations (empty list = acceptable). PRD AUTH-05."""
    errors: list[str] = []
    if len(password) < PASSWORD_MIN:
        errors.append(f"Use at least {PASSWORD_MIN} characters.")
    if len(password) > PASSWORD_MAX:
        errors.append(f"Use at most {PASSWORD_MAX} characters.")
    classes = sum(
        bool(re.search(p, password)) for p in (r"[a-z]", r"[A-Z]", r"[0-9]", r"[^A-Za-z0-9]")
    )
    if classes < 3:
        errors.append("Use at least 3 of: lowercase, uppercase, digits, symbols.")
    if email:
        local = email.split("@", 1)[0].lower()
        if len(local) >= 3 and local in password.lower():
            errors.append("Do not include your email name in the password.")
    return errors


def generate_temporary_password(length: int = 16) -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*-_=+"
    while True:
        pwd = "".join(secrets.choice(alphabet) for _ in range(length))
        if not password_policy_errors(pwd):
            return pwd
