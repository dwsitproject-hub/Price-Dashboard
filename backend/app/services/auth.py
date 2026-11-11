"""Local authentication: login with lockout, password change with policy (PRD AUTH-*)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session as DbSession

from app.core.config import get_settings
from app.core.errors import ApiError
from app.core.security import hash_password, needs_rehash, password_policy_errors, verify_password
from app.models import User
from app.services import audit, sessions
from app.services.audit import RequestMeta

INVALID = ApiError(401, "invalid_credentials", "Invalid email or password.")


def find_by_email(db: DbSession, email: str) -> User | None:
    return db.scalar(select(User).where(func.lower(User.email) == email.strip().lower()))


def login(db: DbSession, email: str, password: str, meta: RequestMeta) -> tuple[User, sessions.IssuedSession]:
    s = get_settings()
    if not s.auth_local_enabled:
        raise ApiError(403, "local_login_disabled", "Password sign-in is disabled. Use Downstream Hub SSO.")
    user = find_by_email(db, email)
    now = datetime.now(UTC)

    if user is None or user.auth_provider == "sso":
        verify_password(None, password)  # equalise timing
        audit.record(db, "LOGIN_FAILED", actor_email=email[:254], outcome="failure", meta=meta,
                     details={"reason": "unknown_user" if user is None else "sso_only"})
        db.commit()
        raise INVALID

    if user.status != "active":
        verify_password(None, password)
        audit.record(db, "LOGIN_FAILED", actor_id=user.id, actor_email=user.email, outcome="failure",
                     meta=meta, details={"reason": "disabled"})
        db.commit()
        raise INVALID

    if user.locked_until and user.locked_until > now:
        audit.record(db, "LOGIN_FAILED", actor_id=user.id, actor_email=user.email, outcome="failure",
                     meta=meta, details={"reason": "locked"})
        db.commit()
        raise ApiError(423, "account_locked",
                       "This account is temporarily locked after too many failed attempts. "
                       "Try again later or contact an administrator.")

    if not verify_password(user.password_hash, password):
        user.failed_login_count += 1
        details = {"reason": "bad_password", "failures": user.failed_login_count}
        if user.failed_login_count >= s.login_max_failures:
            user.locked_until = now + timedelta(minutes=s.login_lockout_minutes)
            user.failed_login_count = 0
            audit.record(db, "ACCOUNT_LOCKED", actor_id=user.id, actor_email=user.email, target_type="user",
                         target_id=str(user.id), meta=meta,
                         details={"minutes": s.login_lockout_minutes})
        audit.record(db, "LOGIN_FAILED", actor_id=user.id, actor_email=user.email, outcome="failure",
                     meta=meta, details=details)
        db.commit()
        raise INVALID

    if user.password_hash and needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    user.failed_login_count, user.locked_until = 0, None
    user.last_login_at, user.last_login_method = now, "local"
    issued = sessions.create(db, user, "local", meta)
    audit.record(db, "LOGIN_SUCCESS", actor_id=user.id, actor_email=user.email, meta=meta,
                 details={"method": "local"})
    db.commit()
    return user, issued


def set_password(db: DbSession, user: User, new_password: str) -> None:
    """Validate the policy and store. Caller audits and commits."""
    errors = password_policy_errors(new_password, user.email)
    if errors:
        raise ApiError(422, "weak_password", " ".join(errors), {"rules": errors})
    user.password_hash = hash_password(new_password)
    user.password_changed_at = datetime.now(UTC)
    user.failed_login_count, user.locked_until = 0, None


def change_password(db: DbSession, user: User, current: str, new: str, current_session_id, meta: RequestMeta):
    if user.auth_provider == "sso" or not user.password_hash:
        raise ApiError(400, "sso_account", "This account signs in with Downstream Hub; it has no password.")
    if not verify_password(user.password_hash, current):
        audit.record(db, "PASSWORD_CHANGE", actor_id=user.id, actor_email=user.email, outcome="failure",
                     meta=meta, details={"reason": "bad_current_password"})
        db.commit()
        raise ApiError(400, "bad_current_password", "Your current password is incorrect.")
    if current == new:
        raise ApiError(422, "password_reused", "The new password must differ from the current one.")
    set_password(db, user, new)
    revoked = sessions.revoke_all(db, user.id, "password_changed", except_id=current_session_id)
    audit.record(db, "PASSWORD_CHANGE", actor_id=user.id, actor_email=user.email, target_type="user",
                 target_id=str(user.id), meta=meta, details={"other_sessions_revoked": revoked})
    db.commit()
