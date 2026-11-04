"""Runtime settings stored in ops.app_settings, with environment defaults (PRD ADM-05)."""
from __future__ import annotations

import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import AppSetting

# key → (type, description, env default getter, validator)
EDITABLE: dict[str, tuple[type, str]] = {
    "security.session_idle_minutes": (int, "Session idle timeout (minutes, 5–240)"),
    "security.session_absolute_hours": (int, "Session absolute lifetime (hours, 1–24)"),
    "sso.auto_provision": (bool, "Create unknown Hub users automatically on first SSO sign-in"),
    "sso.default_role": (str, "Role given to auto-provisioned SSO users"),
}
_LIMITS = {"security.session_idle_minutes": (5, 240), "security.session_absolute_hours": (1, 24)}

_cache: dict[str, Any] = {}
_cache_at = 0.0
_TTL = 30.0


def _defaults() -> dict[str, Any]:
    s = get_settings()
    return {
        "security.session_idle_minutes": s.session_idle_minutes,
        "security.session_absolute_hours": s.session_absolute_hours,
        "sso.auto_provision": s.sso_auto_provision,
        "sso.default_role": s.sso_default_role,
        "data.mode": "live" if s.lseg_configured else "demo",
    }


def all_values(db: Session) -> dict[str, Any]:
    global _cache, _cache_at
    if time.monotonic() - _cache_at < _TTL and _cache:
        return dict(_cache)
    values = _defaults()
    for row in db.scalars(select(AppSetting)):
        values[row.key] = row.value
    _cache, _cache_at = values, time.monotonic()
    return dict(values)


def get(db: Session, key: str) -> Any:
    return all_values(db).get(key)


def validate(key: str, value: Any) -> Any:
    if key not in EDITABLE:
        raise ValueError(f"Setting {key!r} is not editable.")
    typ, _ = EDITABLE[key]
    if typ is bool and not isinstance(value, bool):
        raise ValueError(f"{key} must be true or false.")
    if typ is int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{key} must be an integer.")
        lo, hi = _LIMITS.get(key, (None, None))
        if lo is not None and not (lo <= value <= hi):
            raise ValueError(f"{key} must be between {lo} and {hi}.")
    if typ is str and not isinstance(value, str):
        raise ValueError(f"{key} must be text.")
    return value


def put(db: Session, key: str, value: Any, user_id=None) -> None:
    global _cache_at
    row = db.get(AppSetting, key)
    if row is None:
        db.add(AppSetting(key=key, value=value, description=EDITABLE.get(key, (None, None))[1],
                          updated_by=user_id))
    else:
        row.value, row.updated_by = value, user_id
    _cache_at = 0.0


def invalidate() -> None:
    global _cache_at
    _cache_at = 0.0
