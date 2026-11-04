"""Append-only audit trail (PRD AUD-01..03)."""
from __future__ import annotations

import ipaddress
import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.logging import request_id_var
from app.models import AuditLog

_SECRET_HINTS = ("password", "token", "secret", "hash", "verifier", "cookie")


@dataclass
class RequestMeta:
    ip: str | None = None
    user_agent: str | None = None


def _scrub(obj):
    if isinstance(obj, dict):
        return {k: ("[redacted]" if any(h in k.lower() for h in _SECRET_HINTS) else _scrub(v))
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [_scrub(v) for v in obj]
    if isinstance(obj, uuid.UUID):
        return str(obj)
    return obj


def _valid_ip(ip: str | None) -> str | None:
    if not ip:
        return None
    try:
        return str(ipaddress.ip_address(ip))
    except ValueError:
        return None


def record(db: Session, action: str, *, actor_id: uuid.UUID | None = None, actor_email: str | None = None,
           target_type: str | None = None, target_id: str | None = None, outcome: str = "success",
           meta: RequestMeta | None = None, details: dict | None = None) -> None:
    """Add an audit row to the current transaction (the caller commits)."""
    db.add(AuditLog(
        actor_user_id=actor_id, actor_email=actor_email, action=action, target_type=target_type,
        target_id=str(target_id) if target_id is not None else None, outcome=outcome,
        ip=_valid_ip(meta.ip if meta else None),
        user_agent=(meta.user_agent or "")[:512] if meta else None,
        request_id=request_id_var.get(), details=_scrub(details or {}),
    ))
