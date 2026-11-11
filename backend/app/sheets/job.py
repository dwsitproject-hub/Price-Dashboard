"""`sheet_refresh` job: read every Google Sheet source, compute its series and snapshots, publish a data version.

Each source is independent — one failing (private sheet not shared yet, layout change) never blocks the others.
Series points are written scoped to the source's own keys (store.write_series), so neither the LSEG pipeline nor
another source can delete them.
"""
from __future__ import annotations

import json
import logging

import pandas as pd
from sqlalchemy import text

from app.catalog.instruments import FX_RICS
from app.catalog.sheet_series import keys_for, sheet_specs
from app.core.db import get_engine
from app.engine.conversions import convert_frame
from app.integrations import gsheets
from app.intel.jobs import register
from app.pipeline import store
from app.sheets import argus, china, india, sources, tradable

logger = logging.getLogger("app.sheets")

ARGUS_LEG_RICS = ["FCPOc1", "FCPOc2", "FCPOc3", "LGOc1", "LGOc2", "LGOc3", "MYR=", "GBP=", "EUR=", "BO-ARGUPR-C1"]


def lseg_frames() -> dict[str, pd.DataFrame]:
    """build_frames restricted to the Argus legs but on the full union of LSEG trading dates, so forward fills and
    exact-date joins match the legacy (which pivoted the whole raw pull)."""
    long_df = store.load_prices_long(ARGUS_LEG_RICS)
    with get_engine().connect() as c:
        dates = [r[0] for r in c.execute(text("SELECT DISTINCT trade_date FROM market.price_daily"))]
    if long_df.empty:
        return {"usd": pd.DataFrame(), "raw_ffill": pd.DataFrame()}
    wide = (long_df.drop_duplicates(subset=["date", "ric"])
            .pivot(index="date", columns="ric", values="price").sort_index())
    wide.columns.name = None
    wide = wide.reindex(wide.index.union(pd.to_datetime(dates))).sort_index()
    for ric in FX_RICS:
        if ric in wide.columns:
            wide[ric] = wide[ric].ffill()
    return {"usd": convert_frame(wide).sort_index().ffill(), "raw_ffill": wide.sort_index().ffill()}


def _frame(value: pd.Series, local: pd.Series | None = None) -> pd.DataFrame:
    value = value.dropna()
    value = value[value.index.notna()]
    df = pd.DataFrame({"value": value})
    df["value_local"] = local.reindex(df.index) if local is not None else None
    df.index = pd.to_datetime(df.index)
    df.index.name = "obs_date"
    return df


def _need(grids: dict, key: str) -> list[list[str]]:
    g = grids.get(key)
    if not g:
        raise ValueError(f"tab '{key}' is empty")
    return g


def parse_source(code: str, grids: dict) -> tuple[dict[str, pd.DataFrame], dict[str, list], dict]:
    """(series frames, snapshots, detail) for one source's grids."""
    series: dict[str, pd.DataFrame] = {}
    snaps: dict[str, list] = {}
    detail: dict = {}
    if code == "argus":
        wide = argus.parse_wide(_need(grids, "prices"))
        if wide is None or wide.empty:
            raise ValueError("Argus: no dated rows / header not found")
        legs = argus.lseg_legs(lseg_frames())
        for k, s in argus.compute(wide, legs).items():
            series[k] = _frame(s)
        detail = {"columns": int(wide.shape[1]), "rows": int(wide.shape[0]),
                  "first": wide.index.min().date().isoformat(), "last": wide.index.max().date().isoformat()}
    elif code == "market_price":
        for k, s in tradable.compute(_need(grids, "premiums")).items():
            series[k] = _frame(s)
        if not series:
            raise ValueError("Premiums: no Tradable/Reuters columns matched")
    elif code == "china_parity":
        snaps["parity_china"] = china.parity_snapshot(_need(grids, "dashboard"))
        for k, s in china.cross_oil(_need(grids, "cross_oil")).items():
            series[k] = _frame(s)
        for k, (usd, cny) in china.palm_import(_need(grids, "palm_import")).items():
            series[k] = _frame(usd, cny)
        for k, s in china.soyoil_import(_need(grids, "soyoil_import")).items():
            series[k] = _frame(s)
    elif code == "india_parity":
        for k, (usd, inr) in india.local_prices(india.grid_to_csv(_need(grids, "local"))).items():
            series[k] = _frame(usd, inr)
        snaps["parity_india"] = india.parity_snapshot(india.grid_to_csv(_need(grids, "dashboard")))
        for k, s in india.import_parity(_need(grids, "calculation")).items():
            series[k] = _frame(s)
    else:
        raise ValueError(code)
    known = set(keys_for(code))
    series = {k: v for k, v in series.items() if k in known and not v.empty}
    if not series:
        raise ValueError("no series could be parsed — existing data kept (check the sheet layout)")
    if series:
        last = max(v.index.max() for v in series.values())
        detail.setdefault("last", last.date().isoformat())
    detail["series"] = len(series)
    return series, snaps, detail


def _status(code: str, status: str, method: str | None, error: str | None, detail: dict) -> None:
    with get_engine().begin() as c:
        c.execute(text(
            "INSERT INTO intel.sheet_sources (code, status, method, pulled_at, error, detail, updated_at) "
            "VALUES (:c, :s, :m, now(), :e, CAST(:d AS jsonb), now()) ON CONFLICT (code) DO UPDATE SET "
            "status = EXCLUDED.status, method = EXCLUDED.method, pulled_at = EXCLUDED.pulled_at, "
            "error = EXCLUDED.error, detail = EXCLUDED.detail, updated_at = now()"),
            {"c": code, "s": status, "m": method, "e": error, "d": json.dumps(detail)})


def _save_snapshots(code: str, snaps: dict[str, list]) -> None:
    with get_engine().begin() as c:
        for key, payload in snaps.items():
            c.execute(text("INSERT INTO intel.sheet_snapshots (key, source_code, payload, updated_at) "
                           "VALUES (:k, :c, CAST(:p AS jsonb), now()) ON CONFLICT (key) DO UPDATE SET "
                           "payload = EXCLUDED.payload, source_code = EXCLUDED.source_code, updated_at = now()"),
                      {"k": key, "c": code, "p": json.dumps(payload)})


def _publish(codes: list[str]) -> int:
    with get_engine().begin() as c:
        v = int(c.execute(text("SELECT nextval('ops.data_version_seq')")).scalar_one())
        c.execute(text("UPDATE intel.sheet_sources SET data_version = :v WHERE code = ANY(:c)"), {"v": v, "c": codes})
    return v


def refresh_sources(codes: list[str] | None = None) -> dict:
    codes = [c for c in (codes or list(sources.SOURCES)) if c in sources.SOURCES]
    sa_info, _ = sources.service_account()
    client = gsheets.ServiceAccountClient(sa_info) if sa_info else None
    results, ok = {}, []
    try:
        for code in codes:
            src = sources.SOURCES[code]
            try:
                grids, method = sources.fetch(src, client)
                series, snaps, detail = parse_source(code, grids)
                upserted, deleted = store.write_series(series, keys_for(code))
                _save_snapshots(code, snaps)
                detail.update({"upserted": upserted, "deleted": deleted})
                _status(code, "ok", method, None, detail)
                ok.append(code)
                results[code] = {"status": "ok", "method": method, **detail}
            except sources.NotAvailable as exc:
                _status(code, "skipped", None, str(exc), {})
                results[code] = {"status": "skipped", "error": str(exc)}
            except Exception as exc:  # noqa: BLE001 - recorded per source; the others still run
                msg = f"{type(exc).__name__}: {exc}"[:1000] if not isinstance(exc, gsheets.SheetError) else str(exc)
                logger.warning("sheet source %s failed: %s", code, msg)
                _status(code, "failed", None, msg, {})
                results[code] = {"status": "failed", "error": msg}
    finally:
        if client is not None:
            client.close()
    if ok:
        results["data_version"] = _publish(ok)
    return results


@register("sheet_refresh")
def _job(params: dict) -> dict:
    res = refresh_sources(params.get("sources"))
    failed = [c for c, r in res.items() if isinstance(r, dict) and r.get("status") == "failed"]
    if failed and len(failed) == len([c for c in res if c in sources.SOURCES]):
        raise RuntimeError("; ".join(f"{c}: {res[c]['error']}" for c in failed))
    return res


def status() -> list[dict]:
    with get_engine().connect() as c:
        rows = {r.code: r for r in c.execute(text(
            "SELECT code, status, method, pulled_at, error, detail, data_version FROM intel.sheet_sources"))}
        ups = {r.code: r for r in c.execute(text(
            "SELECT u.code, u.filename, u.uploaded_at, us.email AS uploaded_by FROM intel.sheet_uploads u "
            "LEFT JOIN iam.users us ON us.id = u.uploaded_by"))}
    counts: dict[str, int] = {}
    for s in sheet_specs():
        counts[s["source"]] = counts.get(s["source"], 0) + 1
    out = []
    for code, src in sources.SOURCES.items():
        r, u = rows.get(code), ups.get(code)
        out.append({
            "code": code, "title": src.title, "url": src.url, "access": src.access, "feeds": src.feeds,
            "tabs": [{"title": t.title, "gid": t.gid} for t in src.tabs], "series_defined": counts.get(code, 0),
            "status": r.status if r else "never", "method": r.method if r else None,
            "pulled_at": r.pulled_at if r else None, "error": r.error if r else None,
            "detail": (r.detail or {}) if r else {},
            "upload": None if not u else {"filename": u.filename, "uploaded_at": u.uploaded_at,
                                          "uploaded_by": u.uploaded_by},
        })
    return out

