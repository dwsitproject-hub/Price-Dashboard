"""/api/admin — users, roles & module access, audit log, settings, data pipeline."""
from __future__ import annotations

import csv
import io
import uuid
from datetime import UTC, date, datetime, time

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session as DbSession

from app.api.deps import Principal, request_meta, require_admin, require_permission
from app.core.config import APP_VERSION, get_settings
from app.core.db import get_db
from app.core.errors import ApiError
from app.models import AuditLog, DqFinding, Module, PipelineRun, Role, RoleModule
from app.schemas import PipelineRunIn, RoleModulesIn, SettingsIn, UserCreateIn, UserUpdateIn
from app.services import audit, users
from app.services import settings as runtime_settings

router = APIRouter(prefix="/admin", tags=["admin"])


def _uid(raw: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except ValueError as exc:
        raise ApiError(404, "not_found", "User not found.") from exc


# ── Users ───────────────────────────────────────────────────────────────────

@router.get("/users")
def list_users(q: str | None = Query(None, max_length=100), role: str | None = None, status: str | None = None,
               provider: str | None = None, page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100),
               p: Principal = Depends(require_admin), db: DbSession = Depends(get_db)) -> dict:
    return users.list_users(db, q, role, status, provider, page, page_size)


@router.post("/users", status_code=201)
def create_user(body: UserCreateIn, request: Request, p: Principal = Depends(require_admin),
                db: DbSession = Depends(get_db)) -> dict:
    data = body.model_dump()
    data["overrides"] = [o for o in data["overrides"]] if data.get("overrides") is not None else None
    user, temp = users.create_user(db, data, p.user, request_meta(request))
    return {"user": users.to_dict(db, user), "temporary_password": temp}


@router.get("/users/{user_id}")
def get_user(user_id: str, p: Principal = Depends(require_admin), db: DbSession = Depends(get_db)) -> dict:
    return users.to_dict(db, users.get_user(db, _uid(user_id)))


@router.patch("/users/{user_id}")
def update_user(user_id: str, body: UserUpdateIn, request: Request, p: Principal = Depends(require_admin),
                db: DbSession = Depends(get_db)) -> dict:
    user = users.get_user(db, _uid(user_id))
    data = body.model_dump(exclude_unset=True)
    return users.to_dict(db, users.update_user(db, user, data, p.user, request_meta(request)))


@router.post("/users/{user_id}/reset-password")
def reset_password(user_id: str, request: Request, p: Principal = Depends(require_admin),
                   db: DbSession = Depends(get_db)) -> dict:
    user = users.get_user(db, _uid(user_id))
    return {"temporary_password": users.reset_password(db, user, p.user, request_meta(request))}


@router.post("/users/{user_id}/unlock")
def unlock(user_id: str, request: Request, p: Principal = Depends(require_admin),
           db: DbSession = Depends(get_db)) -> dict:
    users.unlock(db, users.get_user(db, _uid(user_id)), p.user, request_meta(request))
    return {"ok": True}


@router.post("/users/{user_id}/revoke-sessions")
def revoke(user_id: str, request: Request, p: Principal = Depends(require_admin),
           db: DbSession = Depends(get_db)) -> dict:
    return {"revoked": users.revoke_sessions(db, users.get_user(db, _uid(user_id)), p.user,
                                             request_meta(request))}


# ── Roles & modules ─────────────────────────────────────────────────────────

@router.get("/modules")
def list_modules(p: Principal = Depends(require_admin), db: DbSession = Depends(get_db)) -> dict:
    rows = db.scalars(select(Module).order_by(Module.sort_order))
    return {"modules": [{"code": m.code, "name": m.name, "description": m.description, "release": m.release,
                         "is_enabled": m.is_enabled} for m in rows]}


@router.get("/roles")
def list_roles(p: Principal = Depends(require_admin), db: DbSession = Depends(get_db)) -> dict:
    counts = dict(db.execute(text("SELECT role_id, count(*) FROM iam.users GROUP BY role_id")).all())
    roles = db.scalars(select(Role).order_by(Role.id))
    return {"roles": [{"code": r.code, "name": r.name, "description": r.description,
                       "permissions": list(r.permissions or []), "is_system": r.is_system,
                       "modules": sorted(m.module_code for m in r.modules), "user_count": counts.get(r.id, 0)}
                      for r in roles]}


@router.put("/roles/{code}/modules")
def set_role_modules(code: str, body: RoleModulesIn, request: Request, p: Principal = Depends(require_admin),
                     db: DbSession = Depends(get_db)) -> dict:
    role = db.scalar(select(Role).where(Role.code == code))
    if role is None:
        raise ApiError(404, "not_found", "Role not found.")
    if "admin" in (role.permissions or []):
        raise ApiError(422, "admin_role_fixed", "The Administrator role always has every module.")
    valid = set(db.scalars(select(Module.code)))
    wanted = set(body.modules)
    if wanted - valid:
        raise ApiError(422, "unknown_module", f"Unknown modules: {', '.join(sorted(wanted - valid))}")
    before = sorted(m.module_code for m in role.modules)
    role.modules = [RoleModule(role_id=role.id, module_code=m) for m in sorted(wanted)]
    audit.record(db, "ROLE_MODULES_UPDATE", actor_id=p.user.id, actor_email=p.user.email, target_type="role",
                 target_id=code, meta=request_meta(request), details={"from": before, "to": sorted(wanted)})
    db.commit()
    return {"code": code, "modules": sorted(wanted)}


# ── Audit ───────────────────────────────────────────────────────────────────

def _audit_query(action: str | None, actor: str | None, date_from: date | None, date_to: date | None):
    stmt = select(AuditLog)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if actor:
        stmt = stmt.where(func.lower(AuditLog.actor_email).like(f"%{actor.lower()}%"))
    if date_from:
        stmt = stmt.where(AuditLog.occurred_at >= datetime.combine(date_from, time.min, UTC))
    if date_to:
        stmt = stmt.where(AuditLog.occurred_at <= datetime.combine(date_to, time.max, UTC))
    return stmt


def _audit_dict(a: AuditLog) -> dict:
    return {"id": a.id, "occurred_at": a.occurred_at, "actor_email": a.actor_email, "action": a.action,
            "target_type": a.target_type, "target_id": a.target_id, "outcome": a.outcome,
            "ip": str(a.ip) if a.ip else None, "request_id": a.request_id, "details": a.details}


@router.get("/audit")
def list_audit(action: str | None = Query(None, max_length=64), actor: str | None = Query(None, max_length=254),
               date_from: date | None = Query(None, alias="from"), date_to: date | None = Query(None, alias="to"),
               page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
               p: Principal = Depends(require_admin), db: DbSession = Depends(get_db)) -> dict:
    stmt = _audit_query(action, actor, date_from, date_to)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(AuditLog.id.desc()).offset((page - 1) * page_size).limit(page_size))
    actions = list(db.scalars(select(AuditLog.action).distinct().order_by(AuditLog.action)))
    return {"items": [_audit_dict(a) for a in rows], "total": total, "page": page, "page_size": page_size,
            "actions": actions}


@router.get("/audit/export.csv")
def export_audit(request: Request, action: str | None = None, actor: str | None = None,
                 date_from: date | None = Query(None, alias="from"), date_to: date | None = Query(None, alias="to"),
                 p: Principal = Depends(require_admin), db: DbSession = Depends(get_db)):
    rows = list(db.scalars(_audit_query(action, actor, date_from, date_to).order_by(AuditLog.id.desc())
                           .limit(50_000)))
    audit.record(db, "DATA_EXPORT", actor_id=p.user.id, actor_email=p.user.email, target_type="audit_log",
                 meta=request_meta(request), details={"rows": len(rows)})
    db.commit()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "occurred_at", "actor_email", "action", "target_type", "target_id", "outcome", "ip",
                "request_id", "details"])
    for a in rows:
        w.writerow([a.id, a.occurred_at.isoformat(), a.actor_email, a.action, a.target_type, a.target_id,
                    a.outcome, a.ip, a.request_id, a.details])
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": 'attachment; filename="audit_log.csv"'})


# ── Settings & integrations ─────────────────────────────────────────────────

@router.get("/settings")
def get_settings_view(p: Principal = Depends(require_admin), db: DbSession = Depends(get_db)) -> dict:
    s = get_settings()
    vals = runtime_settings.all_values(db)
    token = db.execute(text("SELECT obtained_at, expires_at FROM ops.integration_tokens WHERE provider='lseg'")
                       ).first()
    return {
        "editable": [{"key": k, "value": vals.get(k), "type": t.__name__, "description": d}
                     for k, (t, d) in runtime_settings.EDITABLE.items()],
        "integrations": {
            "environment": s.app_env, "version": APP_VERSION, "release": s.app_release,
            "data_mode": vals.get("data.mode"),
            "lseg": {"configured": s.lseg_configured, "base_url": s.lseg_base_url,
                     "token_obtained_at": token.obtained_at if token else None,
                     "token_expires_at": token.expires_at if token else None},
            "sso": {"enabled": s.sso_enabled, "configured": s.sso_configured, "issuer": s.hub_issuer,
                    "client_id": s.hub_client_id or None, "redirect_uri": s.hub_redirect_uri,
                    "launch_url": s.app_base_url.rstrip("/") + "/api/auth/sso/login"},
            "local_login_enabled": s.auth_local_enabled,
            "pipeline": {"cron": s.pipeline_cron, "timezone": s.pipeline_tz},
        },
    }


@router.put("/settings")
def put_settings(body: SettingsIn, request: Request, p: Principal = Depends(require_admin),
                 db: DbSession = Depends(get_db)) -> dict:
    before = runtime_settings.all_values(db)
    changes = {}
    for key, value in body.values.items():
        try:
            runtime_settings.validate(key, value)
        except ValueError as exc:
            raise ApiError(422, "bad_setting", str(exc)) from exc
        if key == "sso.default_role":
            role = db.scalar(select(Role).where(Role.code == value))
            if role is None or "admin" in (role.permissions or []):
                raise ApiError(422, "bad_setting", "Default SSO role must be an existing non-admin role.")
        if before.get(key) != value:
            runtime_settings.put(db, key, value, p.user.id)
            changes[key] = {"from": before.get(key), "to": value}
    if changes:
        audit.record(db, "SETTINGS_UPDATE", actor_id=p.user.id, actor_email=p.user.email, target_type="setting",
                     meta=request_meta(request), details={"changes": changes})
    db.commit()
    runtime_settings.invalidate()
    return {"changed": list(changes)}


# ── Data pipeline ───────────────────────────────────────────────────────────

def _run_dict(r: PipelineRun, with_steps: bool = False) -> dict:
    d = {"id": str(r.id), "kind": r.kind, "trigger": r.trigger, "status": r.status,
         "queued_at": r.queued_at, "started_at": r.started_at, "finished_at": r.finished_at,
         "rows_fetched": r.rows_fetched, "rows_upserted": r.rows_upserted, "series_computed": r.series_computed,
         "data_version": r.data_version, "warnings_count": r.warnings_count, "error": r.error,
         "duration_s": (r.finished_at - r.started_at).total_seconds() if r.finished_at and r.started_at else None}
    if with_steps:
        d["steps"] = [{"seq": s.seq, "step": s.step, "status": s.status, "started_at": s.started_at,
                       "finished_at": s.finished_at, "message": s.message, "metrics": s.metrics} for s in r.steps]
    return d


@router.get("/pipeline/status")
def pipeline_status(p: Principal = Depends(require_permission("pipeline.run")),
                    db: DbSession = Depends(get_db)) -> dict:
    s = get_settings()
    last_ok = db.scalar(select(PipelineRun).where(PipelineRun.status.in_(["succeeded", "partial"]))
                        .order_by(PipelineRun.finished_at.desc()).limit(1))
    active = db.scalar(select(PipelineRun).where(PipelineRun.status.in_(["queued", "running"]))
                       .order_by(PipelineRun.queued_at.desc()).limit(1))
    stats = db.execute(text(
        "SELECT (SELECT count(*) FROM market.price_daily), (SELECT max(trade_date) FROM market.price_daily), "
        "(SELECT count(*) FROM market.series_points), (SELECT count(DISTINCT ric) FROM market.price_daily)")).first()
    return {"data_mode": runtime_settings.get(db, "data.mode"), "lseg_configured": s.lseg_configured,
            "schedule": {"cron": s.pipeline_cron, "timezone": s.pipeline_tz},
            "last_success": _run_dict(last_ok) if last_ok else None,
            "active": _run_dict(active) if active else None,
            "stats": {"price_rows": stats[0], "latest_trade_date": stats[1], "series_points": stats[2],
                      "rics_with_data": stats[3]}}


@router.get("/pipeline/runs")
def pipeline_runs(limit: int = Query(30, ge=1, le=200), p: Principal = Depends(require_permission("pipeline.run")),
                  db: DbSession = Depends(get_db)) -> dict:
    rows = db.scalars(select(PipelineRun).order_by(PipelineRun.queued_at.desc()).limit(limit))
    return {"items": [_run_dict(r) for r in rows]}


@router.get("/pipeline/runs/{run_id}")
def pipeline_run(run_id: str, p: Principal = Depends(require_permission("pipeline.run")),
                 db: DbSession = Depends(get_db)) -> dict:
    try:
        r = db.get(PipelineRun, uuid.UUID(run_id))
    except ValueError:
        r = None
    if r is None:
        raise ApiError(404, "not_found", "Run not found.")
    findings = db.scalars(select(DqFinding).where(DqFinding.run_id == r.id).order_by(DqFinding.id).limit(500))
    return {**_run_dict(r, with_steps=True),
            "findings": [{"check": f.check_code, "severity": f.severity, "ric": f.ric,
                          "date": f.obs_date, "detail": f.detail} for f in findings]}


@router.post("/pipeline/run", status_code=202)
def trigger_run(body: PipelineRunIn, request: Request, p: Principal = Depends(require_permission("pipeline.run")),
                db: DbSession = Depends(get_db)) -> dict:
    from app.core.ratelimit import limiter
    if body.kind == "full" and not p.is_admin:
        raise ApiError(403, "forbidden", "Only administrators can start a full backfill.")
    if not limiter.allow(f"pipeline:{p.user.id}", 6, 3600):
        raise ApiError(429, "rate_limited", "Too many pipeline runs requested this hour.")
    busy = db.scalar(select(func.count()).select_from(PipelineRun)
                     .where(PipelineRun.status.in_(["queued", "running"])))
    if busy:
        raise ApiError(409, "pipeline_busy", "A pipeline run is already queued or running.")
    run = PipelineRun(kind=body.kind, trigger="manual", triggered_by=p.user.id)
    db.add(run)
    db.flush()
    audit.record(db, "PIPELINE_RUN_QUEUED", actor_id=p.user.id, actor_email=p.user.email,
                 target_type="pipeline_run", target_id=str(run.id), meta=request_meta(request),
                 details={"kind": body.kind})
    db.commit()
    return _run_dict(run)
