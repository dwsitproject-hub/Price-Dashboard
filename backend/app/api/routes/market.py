"""/api/market — analytics data, guarded per module (PRD RBAC-05)."""
from __future__ import annotations

import csv
import io
import re

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session as DbSession

from app.api.deps import Principal, request_meta, require_module, require_permission, require_ready_user
from app.catalog.registry import MODULE_CODES, SERIES_BY_KEY, TA_PRICE_INSTRUMENTS, TA_SPREAD_INSTRUMENTS
from app.core.db import get_db
from app.core.errors import ApiError
from app.services import audit, market

router = APIRouter(prefix="/market", tags=["market"])

_MD = re.compile(r"^(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$")


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


def _unit(unit: str) -> str:
    if unit not in ("usd", "local"):
        raise ApiError(422, "bad_unit", "unit must be 'usd' or 'local'.")
    return unit


def _check_series_access(p: Principal, key: str) -> None:
    sd = SERIES_BY_KEY.get(key)
    if sd is None:
        raise ApiError(404, "not_found", "Unknown series.")
    if sd.module not in p.modules:
        raise ApiError(403, "module_forbidden", "You do not have access to this series.")


@router.get("/catalog")
def get_catalog(p: Principal = Depends(require_ready_user)) -> dict:
    return {"modules": market.catalog(p.modules)}


@router.get("/years")
def get_years(p: Principal = Depends(require_ready_user), db: DbSession = Depends(get_db)) -> dict:
    ys = market.available_years(db)
    return {"years": ys, "default": ys[-3:], "data_version": market.data_version(db),
            "as_of": market.latest_trade_date(db)}


@router.get("/overview")
def get_overview(p: Principal = Depends(require_module("overview")), db: DbSession = Depends(get_db)) -> dict:
    return market.overview(db)


@router.get("/sections/{module}")
def get_section(module: str, request: Request, group: str | None = None, years: str | None = None,
                unit: str = "usd", tenor: str | None = None, section: str | None = Query(None, max_length=32),
                p: Principal = Depends(require_ready_user),
                db: DbSession = Depends(get_db)) -> dict:
    if module not in MODULE_CODES:
        raise ApiError(404, "not_found", "Unknown module.")
    if module not in p.modules:
        raise ApiError(403, "module_forbidden", "You do not have access to this module.")
    if tenor and tenor not in ("c1", "c2", "c3", "c5", "c7"):
        raise ApiError(422, "bad_tenor", "tenor must be one of c1, c2, c3, c5, c7.")
    ys = _years(years, db)
    return {"module": module, "group": group, "years": ys, "unit": _unit(unit),
            "series": market.section_payload(db, module, group, ys, unit, tenor, section)}


@router.get("/series/{key}")
def get_series(key: str, years: str | None = None, unit: str = "usd", p: Principal = Depends(require_ready_user),
               db: DbSession = Depends(get_db)) -> dict:
    _check_series_access(p, key)
    return market.series_payload(db, key, _years(years, db), _unit(unit))


@router.get("/series/{key}/export.csv")
def export_series(key: str, request: Request, unit: str = "usd",
                  p: Principal = Depends(require_permission("data.export")), db: DbSession = Depends(get_db)):
    _check_series_access(p, key)
    header, rows = market.export_rows(db, key, _unit(unit))
    audit.record(db, "DATA_EXPORT", actor_id=p.user.id, actor_email=p.user.email, target_type="series",
                 target_id=key, meta=request_meta(request), details={"rows": len(rows), "unit": unit})
    db.commit()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    w.writerows(rows)
    filename = re.sub(r"[^A-Za-z0-9_.-]", "_", key) + ".csv"
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/forward-curves")
def get_forward_curves(kind: str = "outright", years: str | None = None, snapshot: str | None = None,
                       unit: str = "usd", p: Principal = Depends(require_module("forward_curves")),
                       db: DbSession = Depends(get_db)) -> dict:
    if kind not in ("outright", "product_spread"):
        raise ApiError(422, "bad_kind", "kind must be 'outright' or 'product_spread'.")
    from datetime import date
    md = snapshot or date.today().strftime("%m-%d")
    if not _MD.match(md):
        raise ApiError(422, "bad_snapshot", "snapshot must be MM-DD.")
    ys = _years(years, db)
    return {"kind": kind, "snapshot": md, "years": ys, "unit": _unit(unit),
            "curves": market.forward_curves(db, kind, ys, md, unit)}


@router.get("/ytd-spreads")
def get_ytd_spreads(year: int | None = Query(None, ge=2000, le=2100), unit: str = "usd",
                    p: Principal = Depends(require_module("calendar_spreads")),
                    db: DbSession = Depends(get_db)) -> dict:
    """Calendar Spreads → YTD: C1−CN legs and the C1 price per futures product for one year."""
    available = market.available_years(db)
    y = year if year is not None else (available[-1] if available else None)
    if y is None:
        return {"year": None, "unit": _unit(unit), "products": []}
    return {"year": y, "unit": _unit(unit), "products": market.ytd_spreads(db, y, unit)}


@router.get("/oi-cumulative")
def get_oi(days: int = Query(30, ge=5, le=120), p: Principal = Depends(require_module("oi_volume")),
           db: DbSession = Depends(get_db)) -> dict:
    return {"days": days, "tables": market.oi_tables(db, days)}


@router.get("/ta/instruments")
def get_ta_instruments(p: Principal = Depends(require_module("technical_analysis"))) -> dict:
    return {"instruments": market.ta_instruments()}


@router.get("/ta/{key}")
def get_ta(key: str, start_year: int = Query(2020, ge=2010, le=2100), unit: str = "usd",
           p: Principal = Depends(require_module("technical_analysis")), db: DbSession = Depends(get_db)) -> dict:
    if key not in TA_PRICE_INSTRUMENTS and key not in TA_SPREAD_INSTRUMENTS:
        raise ApiError(404, "not_found", "Unknown instrument.")
    return market.ta_payload(db, key, start_year, _unit(unit))
