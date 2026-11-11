"""User administration — the "User Access" page (PRD USR-01..10)."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session as DbSession

from app.core.errors import ApiError
from app.core.security import generate_temporary_password
from app.models import Module, Role, User, UserModuleOverride
from app.services import audit, auth, sessions
from app.services.audit import RequestMeta
from app.services.rbac import effective_modules


def _role(db: DbSession, code: str) -> Role:
    role = db.scalar(select(Role).where(Role.code == code))
    if role is None:
        raise ApiError(422, "unknown_role", f"Unknown role {code!r}.")
    return role


def _active_admin_count(db: DbSession, exclude: uuid.UUID | None = None) -> int:
    q = (select(func.count()).select_from(User).join(Role)
         .where(User.status == "active", Role.permissions.any("admin")))
    if exclude:
        q = q.where(User.id != exclude)
    return db.scalar(q) or 0


def _snapshot(db: DbSession, u: User) -> dict:
    return {"email": u.email, "display_name": u.display_name, "role": u.role.code,
            "auth_provider": u.auth_provider, "status": u.status,
            "overrides": {o.module_code: o.effect for o in u.overrides}}


def to_dict(db: DbSession, u: User) -> dict:
    return {
        "id": str(u.id), "email": u.email, "display_name": u.display_name, "role": u.role.code,
        "role_name": u.role.name, "auth_provider": u.auth_provider, "status": u.status,
        "hub_linked": u.hub_sub is not None,
        "locked": bool(u.locked_until and u.locked_until > datetime.now(UTC)),
        "last_login_at": u.last_login_at, "last_login_method": u.last_login_method,
        "created_at": u.created_at, "updated_at": u.updated_at,
        "overrides": [{"module": o.module_code, "effect": o.effect} for o in u.overrides],
        "effective_modules": effective_modules(db, u),
    }


def list_users(db: DbSession, q: str | None, role: str | None, status: str | None, provider: str | None,
               page: int, page_size: int) -> dict:
    stmt = select(User).join(Role)
    if q:
        like = f"%{q.strip().lower()}%"
        stmt = stmt.where(or_(func.lower(User.email).like(like), func.lower(User.display_name).like(like)))
    if role:
        stmt = stmt.where(Role.code == role)
    if status:
        stmt = stmt.where(User.status == status)
    if provider:
        stmt = stmt.where(User.auth_provider == provider)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(User.created_at.desc()).offset((page - 1) * page_size).limit(page_size))
    return {"items": [to_dict(db, u) for u in rows.unique()], "total": total, "page": page,
            "page_size": page_size}


def _apply_overrides(db: DbSession, user: User, overrides: list[dict] | None, actor: User) -> None:
    if overrides is None:
        return
    valid = set(db.scalars(select(Module.code)))
    wanted: dict[str, str] = {}
    for o in overrides:
        if o["module"] not in valid:
            raise ApiError(422, "unknown_module", f"Unknown module {o['module']!r}.")
        wanted[o["module"]] = o["effect"]
    user.overrides = [UserModuleOverride(user_id=user.id, module_code=m, effect=e, created_by=actor.id)
                      for m, e in wanted.items()]


def create_user(db: DbSession, data: dict, actor: User, meta: RequestMeta) -> tuple[User, str | None]:
    email = data["email"].strip().lower()
    if auth.find_by_email(db, email):
        raise ApiError(409, "email_exists", "A user with this email already exists.")
    role = _role(db, data["role"])
    provider = data["auth_provider"]
    user = User(email=email,
                display_name=data["display_name"].strip(), role_id=role.id, auth_provider=provider,
                created_by=actor.id, updated_by=actor.id)
    user.role = role
    temp: str | None = None
    if provider in ("local", "both"):
        chosen = data.get("password")
        temp = chosen or generate_temporary_password()
        auth.set_password(db, user, temp)
        if chosen:
            temp = None  # admin typed it; never echo it back
    db.add(user)
    db.flush()
    _apply_overrides(db, user, data.get("overrides"), actor)
    audit.record(db, "USER_CREATE", actor_id=actor.id, actor_email=actor.email, target_type="user",
                 target_id=str(user.id), meta=meta, details={"after": _snapshot(db, user)})
    db.commit()
    db.refresh(user)
    return user, temp


def get_user(db: DbSession, user_id: uuid.UUID) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise ApiError(404, "not_found", "User not found.")
    return user


def update_user(db: DbSession, user: User, data: dict, actor: User, meta: RequestMeta) -> User:
    before = _snapshot(db, user)
    is_self = user.id == actor.id
    if "role" in data and data["role"] is not None:
        role = _role(db, data["role"])
        if "admin" in user.role.permissions and "admin" not in role.permissions:
            if is_self:
                raise ApiError(422, "self_demotion", "You cannot remove your own administrator role.")
            if _active_admin_count(db, exclude=user.id) == 0:
                raise ApiError(422, "last_admin", "At least one active administrator must remain.")
        user.role_id, user.role = role.id, role
    if data.get("display_name"):
        user.display_name = data["display_name"].strip()
    if data.get("auth_provider"):
        new = data["auth_provider"]
        if new in ("local", "both") and not user.password_hash:
            raise ApiError(422, "password_required",
                           "Reset the password first to enable password sign-in for this user.")
        user.auth_provider = new
    if data.get("status"):
        if data["status"] == "disabled":
            if is_self:
                raise ApiError(422, "self_disable", "You cannot disable your own account.")
            if "admin" in user.role.permissions and _active_admin_count(db, exclude=user.id) == 0:
                raise ApiError(422, "last_admin", "At least one active administrator must remain.")
            if user.status != "disabled":
                sessions.revoke_all(db, user.id, "user_disabled")
        user.status = data["status"]
    _apply_overrides(db, user, data.get("overrides"), actor)
    user.updated_by = actor.id
    db.flush()
    after = _snapshot(db, user)
    changes = {k: {"from": before[k], "to": after[k]} for k in after if before[k] != after[k]}
    if changes:
        action = ("USER_DISABLE" if changes.get("status", {}).get("to") == "disabled" else
                  "USER_ENABLE" if changes.get("status", {}).get("to") == "active" else "USER_UPDATE")
        audit.record(db, action, actor_id=actor.id, actor_email=actor.email, target_type="user",
                     target_id=str(user.id), meta=meta, details={"changes": changes})
    db.commit()
    db.refresh(user)
    return user


def reset_password(db: DbSession, user: User, actor: User, meta: RequestMeta) -> str:
    if user.auth_provider == "sso":
        raise ApiError(422, "sso_account", "SSO-only users have no password. Change the sign-in method first.")
    temp = generate_temporary_password()
    auth.set_password(db, user, temp)
    revoked = sessions.revoke_all(db, user.id, "password_reset")
    audit.record(db, "PASSWORD_RESET", actor_id=actor.id, actor_email=actor.email, target_type="user",
                 target_id=str(user.id), meta=meta, details={"sessions_revoked": revoked})
    db.commit()
    return temp


def unlock(db: DbSession, user: User, actor: User, meta: RequestMeta) -> None:
    user.locked_until, user.failed_login_count = None, 0
    audit.record(db, "ACCOUNT_UNLOCK", actor_id=actor.id, actor_email=actor.email, target_type="user",
                 target_id=str(user.id), meta=meta)
    db.commit()


def revoke_sessions(db: DbSession, user: User, actor: User, meta: RequestMeta) -> int:
    n = sessions.revoke_all(db, user.id, "admin_revoked")
    audit.record(db, "SESSIONS_REVOKE", actor_id=actor.id, actor_email=actor.email, target_type="user",
                 target_id=str(user.id), meta=meta, details={"count": n})
    db.commit()
    return n
