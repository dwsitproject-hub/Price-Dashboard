"""Server-side sessions: opaque cookie token, SHA-256 at rest, idle + absolute expiry (PRD AUTH-10/11/13)."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session as DbSession

from app.core.crypto import random_token, sha256
from app.models import Session, User
from app.services import settings as runtime_settings
from app.services.audit import RequestMeta

SESSION_COOKIE = "aastar_session"
CSRF_COOKIE = "aastar_csrf"
_SLIDE_EVERY = timedelta(seconds=60)


@dataclass
class IssuedSession:
    session: Session
    token: str
    csrf: str


def _now() -> datetime:
    return datetime.now(UTC)


def create(db: DbSession, user: User, method: str, meta: RequestMeta) -> IssuedSession:
    vals = runtime_settings.all_values(db)
    now = _now()
    token, csrf = random_token(), random_token()
    s = Session(
        user_id=user.id, token_hash=sha256(token), csrf_hash=sha256(csrf), auth_method=method,
        created_at=now, last_seen_at=now,
        idle_expires_at=now + timedelta(minutes=int(vals["security.session_idle_minutes"])),
        expires_at=now + timedelta(hours=int(vals["security.session_absolute_hours"])),
        ip=meta.ip, user_agent=(meta.user_agent or "")[:512],
    )
    db.add(s)
    return IssuedSession(s, token, csrf)


def resolve(db: DbSession, token: str | None) -> Session | None:
    if not token or len(token) > 128:
        return None
    s = db.scalar(select(Session).where(Session.token_hash == sha256(token)))
    if s is None or s.revoked_at is not None:
        return None
    now = _now()
    if now >= s.expires_at or now >= s.idle_expires_at:
        return None
    if s.user.status != "active":
        return None
    if now - s.last_seen_at >= _SLIDE_EVERY:
        idle = int(runtime_settings.get(db, "security.session_idle_minutes"))
        s.last_seen_at = now
        s.idle_expires_at = min(s.expires_at, now + timedelta(minutes=idle))
        db.commit()
    return s


def revoke(db: DbSession, session: Session, reason: str) -> None:
    session.revoked_at, session.revoked_reason = _now(), reason


def revoke_all(db: DbSession, user_id: uuid.UUID, reason: str, except_id: uuid.UUID | None = None) -> int:
    stmt = (update(Session).where(Session.user_id == user_id, Session.revoked_at.is_(None))
            .values(revoked_at=_now(), revoked_reason=reason))
    if except_id is not None:
        stmt = stmt.where(Session.id != except_id)
    return db.execute(stmt).rowcount or 0


def csrf_matches(session: Session, header_value: str | None, cookie_value: str | None) -> bool:
    if not header_value or not cookie_value or header_value != cookie_value:
        return False
    return sha256(header_value) == session.csrf_hash
