"""Effective module access = (role modules ∪ user grants) − user denies. Admin sees everything."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Module, User

PERMISSIONS = ("admin", "data.export", "pipeline.run")


def enabled_modules(db: Session) -> list[str]:
    return list(db.scalars(select(Module.code).where(Module.is_enabled).order_by(Module.sort_order)))


def effective_modules(db: Session, user: User) -> list[str]:
    enabled = enabled_modules(db)
    if "admin" in (user.role.permissions or []):
        return enabled
    allowed = {rm.module_code for rm in user.role.modules}
    for o in user.overrides:
        if o.effect == "grant":
            allowed.add(o.module_code)
    for o in user.overrides:
        if o.effect == "deny":
            allowed.discard(o.module_code)
    return [m for m in enabled if m in allowed]


def has_permission(user: User, perm: str) -> bool:
    perms = user.role.permissions or []
    return "admin" in perms or perm in perms
