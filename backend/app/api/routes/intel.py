"""Intelligence sections (legacy parity): SND, CFTC, AI analysis, News, event Calendar, HVO/SAF Atlas.

Every route checks the section's module; changes additionally need a permission (admin, pipeline.run or
documents.upload) and are audited.
"""
from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session as DbSession

from app.api.deps import Principal, request_meta, require_module
from app.core.config import get_settings
from app.core.db import get_db
from app.core.errors import ApiError
from app.core.ratelimit import limiter
from app.intel import ai, atlas, calendar, cftc, jobs, news, snd
from app.services import audit

router = APIRouter(prefix="/intel", tags=["intel"])


def _need(p: Principal, perm: str) -> None:
    if not p.can(perm):
        raise ApiError(403, "forbidden", "You do not have permission for this action.")


def _audit(db: DbSession, request: Request, p: Principal, action: str, target_type: str, target_id, **details):
    audit.record(db, action, actor_id=p.user.id, actor_email=p.user.email, target_type=target_type,
                 target_id=str(target_id) if target_id is not None else None, meta=request_meta(request),
                 details=details)
    db.commit()


async def _read(upload: UploadFile, max_mb: int) -> bytes:
    data = await upload.read(max_mb * 1024 * 1024 + 1)
    if len(data) > max_mb * 1024 * 1024:
        raise ApiError(413, "too_large", f"Files are limited to {max_mb} MB.")
    if not data:
        raise ApiError(422, "empty_file", "The file is empty.")
    return data


def _years(raw: str | None) -> list[int]:
    try:
        return sorted({int(y) for y in raw.split(",") if y.strip()}) if raw else []
    except ValueError as exc:
        raise ApiError(422, "bad_years", "years must be a comma-separated list of integers.") from exc


# ── SND ─────────────────────────────────────────────────────────────────────

@router.get("/snd/graphs")
def snd_graphs(years: str | None = None, p: Principal = Depends(require_module("snd"))) -> dict:
    return snd.graphs(_years(years))


@router.get("/snd/tables")
def snd_tables(p: Principal = Depends(require_module("snd"))) -> dict:
    return snd.tables()


@router.get("/snd/exports")
def snd_exports(p: Principal = Depends(require_module("snd"))) -> dict:
    return snd.exports_table()


@router.get("/snd/sources")
def snd_sources(p: Principal = Depends(require_module("snd"))) -> dict:
    return {"sources": snd.list_sources(), "can_upload": p.is_admin}


@router.post("/snd/sources", status_code=201)
async def snd_upload(request: Request, file: UploadFile = File(...), role: str = Form(...), year: int = Form(0),
                     sheet: str | None = Form(None), p: Principal = Depends(require_module("snd")),
                     db: DbSession = Depends(get_db)) -> dict:
    _need(p, "admin")
    data = await _read(file, 20)
    grid, used_sheet = snd.grid_from_upload(file.filename or "upload", data, (sheet or "").strip() or None, role)
    res = snd.save_source(role, year, file.filename or "upload", used_sheet, grid, p.user.id)
    _audit(db, request, p, "SND_UPLOAD", "snd_source", f"{role}:{res['year']}", filename=file.filename,
           rows=res["rows"])
    return res


@router.delete("/snd/sources/{role}/{year}")
def snd_delete(role: str, year: int, request: Request, p: Principal = Depends(require_module("snd")),
               db: DbSession = Depends(get_db)) -> dict:
    _need(p, "admin")
    if not snd.delete_source(role, year):
        raise ApiError(404, "not_found", "No such SND source.")
    _audit(db, request, p, "SND_DELETE", "snd_source", f"{role}:{year}")
    return {"deleted": True}


# ── CFTC ────────────────────────────────────────────────────────────────────

@router.get("/cftc")
def cftc_view(p: Principal = Depends(require_module("cftc"))) -> dict:
    return {**cftc.payload(), "job": jobs.latest("cftc_refresh"), "can_refresh": p.can("pipeline.run")}


@router.post("/cftc/refresh", status_code=202)
def cftc_refresh(request: Request, p: Principal = Depends(require_module("cftc")), db: DbSession = Depends(get_db)):
    _need(p, "pipeline.run")
    jid = jobs.enqueue("cftc_refresh", user_id=p.user.id)
    _audit(db, request, p, "CFTC_REFRESH", "job", jid)
    return {"job_id": str(jid)}


# ── Calendar ────────────────────────────────────────────────────────────────

@router.get("/calendar")
def calendar_view(p: Principal = Depends(require_module("calendar"))) -> dict:
    return {**calendar.payload(), "job": jobs.latest("calendar_refresh"), "can_refresh": p.can("pipeline.run"),
            "claude_configured": get_settings().claude_configured}


@router.post("/calendar/refresh", status_code=202)
def calendar_refresh(request: Request, p: Principal = Depends(require_module("calendar")),
                     db: DbSession = Depends(get_db)):
    _need(p, "pipeline.run")
    if not get_settings().claude_configured:
        raise ApiError(409, "claude_not_configured", "Claude is not configured (ANTHROPIC_API_KEY is not set).")
    jid = jobs.enqueue("calendar_refresh", user_id=p.user.id)
    _audit(db, request, p, "CALENDAR_REFRESH", "job", jid)
    return {"job_id": str(jid)}


# ── HVO / SAF Atlas ─────────────────────────────────────────────────────────

@router.get("/atlas")
def atlas_view(p: Principal = Depends(require_module("hvo_atlas"))) -> dict:
    return {**atlas.payload(), "can_edit": p.is_admin}


class AtlasIn(BaseModel):
    snapshot: str | None = Field(None, max_length=60)
    plants: list[dict] = Field(min_length=1, max_length=5000)


@router.put("/atlas")
def atlas_replace(body: AtlasIn, request: Request, p: Principal = Depends(require_module("hvo_atlas")),
                  db: DbSession = Depends(get_db)) -> dict:
    _need(p, "admin")
    n = atlas.replace(body.model_dump())
    _audit(db, request, p, "ATLAS_REPLACE", "atlas", None, plants=n, snapshot=body.snapshot)
    return {"plants": n}


# ── AI analysis ─────────────────────────────────────────────────────────────

@router.get("/ai/instruments")
def ai_instruments(p: Principal = Depends(require_module("ai_analysis"))) -> dict:
    return {"groups": ai.instruments(p.modules), "timeframes": list(ai.TIMEFRAMES),
            "claude_configured": get_settings().claude_configured}


class AiReportIn(BaseModel):
    instrument: str = Field(max_length=96)
    timeframe: str = Field("1Y", max_length=4)


def _check_instrument(p: Principal, key: str) -> None:
    if "::" not in key:
        raise ApiError(422, "bad_instrument", "Unknown instrument.")
    mod = ai.series_module(key)
    if mod is not None and mod not in p.modules:      # a derived series needs its own module
        raise ApiError(403, "module_forbidden", "You do not have access to this series.")


@router.post("/ai/reports", status_code=201)
def ai_create(body: AiReportIn, request: Request, p: Principal = Depends(require_module("ai_analysis")),
              db: DbSession = Depends(get_db)) -> dict:
    _check_instrument(p, body.instrument)
    if not limiter.allow(f"ai:{p.user.id}", 20, 3600):
        raise ApiError(429, "rate_limited", "At most 20 AI analyses per hour. Try again later.")
    rep = ai.create_report(body.instrument, body.timeframe, p.user.id)
    _audit(db, request, p, "AI_REPORT", "ai_report", rep["id"], instrument=body.instrument, timeframe=body.timeframe)
    return rep


@router.get("/ai/reports")
def ai_recent(p: Principal = Depends(require_module("ai_analysis"))) -> dict:
    return {"reports": ai.recent_reports()}


@router.get("/ai/reports/{report_id}")
def ai_get(report_id: uuid.UUID, p: Principal = Depends(require_module("ai_analysis"))) -> dict:
    rep = ai.get_report(report_id)
    _check_instrument(p, rep["instrument"])
    return rep


# ── News ────────────────────────────────────────────────────────────────────

@router.get("/news/panel")
def news_panel(geography: str = Query("Asia"), product: str = Query("Palm"),
               p: Principal = Depends(require_module("news"))) -> dict:
    return news.panel(geography, product)


@router.get("/news/documents")
def news_documents(p: Principal = Depends(require_module("news"))) -> dict:
    return {"documents": news.list_documents(), "can_upload": p.can("documents.upload"), "can_delete": p.is_admin,
            "claude_configured": get_settings().claude_configured, "doc_types": news.DOC_TYPES,
            "geographies": news.GEOGRAPHIES, "products": news.PRODUCTS,
            "max_mb": get_settings().document_max_mb}


@router.post("/news/documents", status_code=201)
async def news_upload(request: Request, file: UploadFile = File(...), doc_type: str = Form("auto"),
                      doc_date: date | None = Form(None), p: Principal = Depends(require_module("news")),
                      db: DbSession = Depends(get_db)) -> dict:
    _need(p, "documents.upload")
    if not get_settings().claude_configured:
        raise ApiError(409, "claude_not_configured", "Claude is not configured (ANTHROPIC_API_KEY is not set).")
    data = await _read(file, get_settings().document_max_mb)
    doc = news.upload(file.filename or "document", data, doc_type, doc_date, p.user.id)
    _audit(db, request, p, "DOCUMENT_UPLOAD", "document", doc["id"], filename=doc["filename"], size=len(data))
    return doc


@router.post("/news/documents/{document_id}/reprocess", status_code=202)
def news_reprocess(document_id: uuid.UUID, request: Request, p: Principal = Depends(require_module("news")),
                   db: DbSession = Depends(get_db)) -> dict:
    _need(p, "documents.upload")
    doc = news.reprocess(document_id, p.user.id)
    _audit(db, request, p, "DOCUMENT_REPROCESS", "document", document_id)
    return doc


@router.delete("/news/documents/{document_id}")
def news_delete(document_id: uuid.UUID, request: Request, p: Principal = Depends(require_module("news")),
                db: DbSession = Depends(get_db)) -> dict:
    _need(p, "admin")
    if not news.delete_document(document_id):
        raise ApiError(404, "not_found", "Document not found.")
    _audit(db, request, p, "DOCUMENT_DELETE", "document", document_id)
    return {"deleted": True}
