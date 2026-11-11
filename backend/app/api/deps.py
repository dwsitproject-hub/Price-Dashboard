"""Request dependencies: session resolution, CSRF, RBAC, request metadata."""
from __future__ import annotations

import ipaddress
from dataclasses import dataclass

from fastapi import Depends, Request
from sqlalchemy.orm import Session as DbSession

from app.core.config import get_settings
from app.core.db import get_db
from app.core.errors import ApiError
from app.core.logging import user_id_var
from app.core.ratelimit import limiter
from app.models import Session, User
from app.services import rbac, sessions
from app.services.audit import RequestMeta

_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def client_ip(request: Request) -> str | None:
    # Nginx sets X-Forwarded-For; uvicorn's --forwarded-allow-ips makes request.client trustworthy.
    host = request.client.host if request.client else None
    try:
        return str(ipaddress.ip_address(host)) if host else None
    except ValueError:
        return None


def request_meta(request: Request) -> RequestMeta:
    return RequestMeta(ip=client_ip(request), user_agent=request.headers.get("user-agent"))


def check_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if origin and origin.rstrip("/") not in get_settings().allowed_origins:
        raise ApiError(403, "bad_origin", "Cross-origin request rejected.")


@dataclass
class Principal:
    user: User
    session: Session
    modules: list[str]

    @property
    def is_admin(self) -> bool:
        return "admin" in (self.user.role.permissions or [])

    def can(self, perm: str) -> bool:
        return rbac.has_permission(self.user, perm)


def current_principal(request: Request, db: DbSession = Depends(get_db)) -> Principal:
    token = request.cookies.get(sessions.SESSION_COOKIE)
    s = sessions.resolve(db, token)
    if s is None:
        raise ApiError(401, "unauthorized", "Please sign in.")
    if request.method not in _SAFE_METHODS:
        check_origin(request)
        if not sessions.csrf_matches(s, request.headers.get("x-csrf-token"),
                                     request.cookies.get(sessions.CSRF_COOKIE)):
            raise ApiError(403, "csrf_failed", "Security token missing or invalid. Reload the page.")
    if not limiter.allow(f"session:{s.id}", 600, 60):
        raise ApiError(429, "rate_limited", "Too many requests. Slow down.")
    user_id_var.set(str(s.user_id))
    return Principal(user=s.user, session=s, modules=rbac.effective_modules(db, s.user))


def require_ready_user(p: Principal = Depends(current_principal)) -> Principal:
    """Signed in. (The forced change of temporary passwords, PRD AUTH-09, was removed: sign-in is via the Hub.)"""
    return p


def require_module(module: str):
    def _dep(p: Principal = Depends(require_ready_user)) -> Principal:
        if module not in p.modules:
            raise ApiError(403, "module_forbidden", "You do not have access to this module.")
        return p
    return _dep


def require_permission(perm: str):
    def _dep(p: Principal = Depends(require_ready_user)) -> Principal:
        if not p.can(perm):
            raise ApiError(403, "forbidden", "You do not have permission for this action.")
        return p
    return _dep


require_admin = require_permission("admin")
