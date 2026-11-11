"""Google Sheet–backed views (/api/sheets) and their administration (/api/admin/sheets).

Read routes check the module of the view (waste_bio, parity); admin routes need the `admin` permission (the
refresh also accepts `pipeline.run`) and every change is audited. The service-account key is never returned.
"""
from __future__ import annotations

import re
from datetime import date

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session as DbSession

from app.api.deps import Principal, request_meta, require_admin, require_module
from app.core.db import get_db
from app.core.errors import ApiError
from app.integrations import gsheets
from app.intel import jobs
from app.services import audit, market
from app.sheets import argus, job, service, sources

router = APIRouter(prefix="/sheets", tags=["sheets"])
admin_router = APIRouter(prefix="/admin/sheets", tags=["admin"])

_MD = re.compile(r"^(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$")
UPLOAD_MAX_MB = 31


def _years(raw: str | None, db: DbSession) -> list[int]:
    available = market.available_years(db)
    if not raw:
        return available[-3:]
    try:
        ys = sorted({int(y) for y in raw.split(",") if y.strip()})
    except ValueError as exc:
        raise ApiError(422, "bad_years", "years must be a comma-separated list of integers.") from exc
    if len(ys) > 15 or any(y < 2000 or y > 2100 for y in ys):
        raise ApiError(422, "bad_years", "Choose up to 15 years between 2000 and 2100.")
    return ys


def _audit(db: DbSession, request: Request, p: Principal, action: str, target_id: str | None, **details) -> None:
    audit.record(db, action, actor_id=p.user.id, actor_email=p.user.email, target_type="sheet_source",
                 target_id=target_id, meta=request_meta(request), details=details)
    db.commit()


# ── Waste/Bio ───────────────────────────────────────────────────────────────

@router.get("/waste-bio/curves/{set_name}")
def waste_bio_curves(set_name: str, years: str | None = None, snapshot: str | None = None,
                     p: Principal = Depends(require_module("waste_bio")), db: DbSession = Depends(get_db)) -> dict:
    if set_name not in argus.CURVE_SETS:
        raise ApiError(404, "not_found", "Unknown curve set.")
    md = snapshot or date.today().strftime("%m-%d")
    if not _MD.match(md):
        raise ApiError(422, "bad_snapshot", "snapshot must be MM-DD.")
    ys = _years(years, db)
    return {"set": set_name, "snapshot": md, "years": ys, "curves": service.curves(db, set_name, ys, md)}


@router.get("/waste-bio/ytd")
def waste_bio_ytd(year: int | None = Query(None, ge=2000, le=2100), p: Principal = Depends(require_module("waste_bio")),
                  db: DbSession = Depends(get_db)) -> dict:
    y = year or date.today().year
    return {"year": y, "products": service.ytd(db, y)}


@router.get("/waste-bio/arb")
def waste_bio_arb(p: Principal = Depends(require_module("waste_bio")), db: DbSession = Depends(get_db)) -> dict:
    return service.arb(db)


@router.get("/waste-bio/tickets-calc")
def waste_bio_tickets(p: Principal = Depends(require_module("waste_bio")), db: DbSession = Depends(get_db)) -> dict:
    return service.tickets_calc(db)


# ── Parity · Ind/China ──────────────────────────────────────────────────────

@router.get("/parity-snapshots")
def parity_snapshots(p: Principal = Depends(require_module("parity")), db: DbSession = Depends(get_db)) -> dict:
    return service.parity_snapshots(db)


# ── Administration ──────────────────────────────────────────────────────────

class ServiceAccountIn(BaseModel):
    key_json: str = Field(min_length=50, max_length=20_000)


class RefreshIn(BaseModel):
    sources: list[str] | None = None


@admin_router.get("")
def sheet_sources(p: Principal = Depends(require_admin)) -> dict:
    info, origin = sources.service_account()
    return {"sources": job.status(), "job": jobs.latest("sheet_refresh"),
            "service_account": {"configured": info is not None, "email": info["client_email"] if info else None,
                                "origin": origin or None}}


@admin_router.post("/refresh", status_code=202)
def refresh(body: RefreshIn, request: Request, p: Principal = Depends(require_admin),
            db: DbSession = Depends(get_db)) -> dict:
    codes = body.sources or None
    if codes and any(c not in sources.SOURCES for c in codes):
        raise ApiError(422, "bad_source", f"sources must be among {', '.join(sources.SOURCES)}")
    jid = jobs.enqueue("sheet_refresh", trigger="manual", params={"sources": codes} if codes else {}, user_id=p.user.id)
    _audit(db, request, p, "SHEETS_REFRESH", None, sources=codes or "all", job_id=str(jid))
    return {"job_id": str(jid)}


@admin_router.put("/service-account")
def put_service_account(body: ServiceAccountIn, request: Request, p: Principal = Depends(require_admin),
                        db: DbSession = Depends(get_db)) -> dict:
    try:
        email = sources.save_service_account(body.key_json, p.user.id)
    except gsheets.SheetError as exc:
        raise ApiError(422, "bad_key", str(exc)) from exc
    _audit(db, request, p, "SHEETS_SERVICE_ACCOUNT_SET", None, service_account=email)
    return {"configured": True, "email": email}


@admin_router.delete("/service-account")
def delete_service_account(request: Request, p: Principal = Depends(require_admin),
                           db: DbSession = Depends(get_db)) -> dict:
    removed = sources.delete_service_account()
    _audit(db, request, p, "SHEETS_SERVICE_ACCOUNT_REMOVED", None, removed=removed)
    return {"removed": removed}


@admin_router.post("/upload", status_code=201)
async def upload(request: Request, file: UploadFile = File(...), code: str = Form(...),
                 p: Principal = Depends(require_admin), db: DbSession = Depends(get_db)) -> dict:
    src = sources.SOURCES.get(code)
    if src is None:
        raise ApiError(422, "bad_source", f"code must be one of {', '.join(sources.SOURCES)}")
    name = (file.filename or "").lower()
    if not name.endswith((".xlsx", ".xlsm")):
        raise ApiError(422, "bad_file_type", "Upload the workbook as .xlsx (Google Sheets: File → Download → .xlsx).")
    data = await file.read(UPLOAD_MAX_MB * 1024 * 1024 + 1)
    if len(data) > UPLOAD_MAX_MB * 1024 * 1024:
        raise ApiError(413, "too_large", f"Files are limited to {UPLOAD_MAX_MB} MB.")
    if not data:
        raise ApiError(422, "empty_file", "The file is empty.")
    try:
        grids = sources.grids_from_xlsx(src, data)
        job.parse_source(code, grids)                 # reject a workbook the parsers cannot read
    except ValueError as exc:
        raise ApiError(422, "bad_workbook", str(exc)) from exc
    sources.save_upload(code, file.filename or "upload.xlsx", grids, p.user.id)
    jid = jobs.enqueue("sheet_refresh", trigger="upload", params={"sources": [code]}, user_id=p.user.id)
    _audit(db, request, p, "SHEETS_UPLOAD", code, filename=(file.filename or "")[:255],
           tabs={k: len(v) for k, v in grids.items()}, job_id=str(jid))
    return {"code": code, "tabs": {k: len(v) for k, v in grids.items()}, "job_id": str(jid)}


@admin_router.delete("/upload/{code}")
def delete_upload(code: str, request: Request, p: Principal = Depends(require_admin),
                  db: DbSession = Depends(get_db)) -> dict:
    if code not in sources.SOURCES:
        raise ApiError(404, "not_found", "Unknown source.")
    removed = sources.delete_upload(code)
    _audit(db, request, p, "SHEETS_UPLOAD_DELETE", code, removed=removed)
    return {"removed": removed}
