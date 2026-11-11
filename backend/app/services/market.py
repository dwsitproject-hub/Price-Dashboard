"""Market data queries for the API. Results are cached per data_version (TD §6.5)."""
from __future__ import annotations

import threading
from collections import OrderedDict
from datetime import date, timedelta

import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session as DbSession

from app.catalog.instruments import PRODUCTS, futures_ric, instrument_conversion
from app.catalog.registry import (FORWARD_CURVE_PRODUCTS, MODULES, OVERVIEW_TILES, PS_CONTRACTS, PS_FAMILIES,
                                  SERIES, SERIES_BY_KEY, TA_PRICE_INSTRUMENTS, TA_SPREAD_INSTRUMENTS,
                                  ric_display)
from app.engine import oi as oi_engine
from app.engine import ta as ta_engine
from app.engine.conversions import to_usd
from app.engine.seasonal import seasonal_traces

_CACHE_MAX = 512
_cache: OrderedDict[tuple, object] = OrderedDict()
_cache_lock = threading.Lock()


def data_version(db: DbSession) -> int:
    v = db.execute(text("SELECT coalesce(max(data_version), 0) FROM ops.pipeline_runs "
                        "WHERE data_version IS NOT NULL")).scalar_one()
    return int(v)


def _cached(key: tuple, fn):
    with _cache_lock:
        if key in _cache:
            _cache.move_to_end(key)
            return _cache[key]
    val = fn()
    with _cache_lock:
        _cache[key] = val
        while len(_cache) > _CACHE_MAX:
            _cache.popitem(last=False)
    return val


# ── Catalog ─────────────────────────────────────────────────────────────────

def catalog(allowed_modules: list[str]) -> list[dict]:
    allowed = set(allowed_modules)
    out = []
    for m in MODULES:
        if m.code not in allowed:
            continue
        groups: OrderedDict[str, list] = OrderedDict()
        for s in SERIES:
            if s.module != m.code:
                continue
            if s.family and s.module == "product_spreads" and s.tenor != "c3":
                continue  # tenor picker resolves the other contracts
            groups.setdefault(s.group, []).append(
                {"key": s.key, "title": s.title, "unit": s.unit, "local_unit": s.local_unit,
                 "family": s.family})
        out.append({"code": m.code, "name": m.name, "description": m.description, "nav_group": m.nav_group,
                    "icon": m.icon, "kind": m.kind, "has_local_units": any(
                        s.local_unit for s in SERIES if s.module == m.code) or m.kind in ("forward", "ta"),
                    "has_tenor": m.code == "product_spreads",
                    "groups": [{"name": g, "series": v} for g, v in groups.items()]})
    return out


def latest_trade_date(db: DbSession) -> str | None:
    def q():
        d = db.execute(text("SELECT max(obs_date) FROM market.series_points")).scalar()
        return d.isoformat() if d else None
    return _cached(("asof", data_version(db)), q)


def available_years(db: DbSession) -> list[int]:
    def q():
        rows = db.execute(text("SELECT DISTINCT extract(year FROM obs_date)::int AS y FROM market.series_points "
                               "ORDER BY 1")).scalars().all()
        return [int(r) for r in rows]
    return _cached(("years", data_version(db)), q)


# ── Seasonal series ─────────────────────────────────────────────────────────

def _points(db: DbSession, key: str, start: date | None) -> pd.DataFrame:
    sql = "SELECT obs_date, value, value_local FROM market.series_points WHERE series_key = :k"
    params: dict = {"k": key}
    if start:
        sql += " AND obs_date >= :s"
        params["s"] = start
    rows = db.execute(text(sql + " ORDER BY obs_date"), params).all()
    df = pd.DataFrame(rows, columns=["obs_date", "value", "value_local"])
    return df.set_index("obs_date")


def series_payload(db: DbSession, key: str, years: list[int], unit: str) -> dict:
    sd = SERIES_BY_KEY[key]
    use_local = unit == "local" and sd.local_unit is not None
    years = sorted(set(years))

    def q():
        start = date(min(years), 1, 1) if years else None
        pts = _points(db, key, start)
        traces = seasonal_traces(pts, years, "value_local" if use_local else "value")
        last = None
        if not pts.empty:
            col = "value_local" if use_local else "value"
            nn = pts[col].dropna()
            if not nn.empty:
                last = {"date": nn.index[-1].isoformat(), "value": float(nn.iloc[-1])}
        return {"key": key, "title": sd.title, "group": sd.group,
                "unit": sd.local_unit if use_local else sd.unit, "traces": traces, "last": last}
    return _cached(("series", key, tuple(years), use_local, data_version(db)), q)


def section_payload(db: DbSession, module: str, group: str | None, years: list[int], unit: str,
                    tenor: str | None) -> list[dict]:
    keys = []
    for s in SERIES:
        if s.module != module or (group and s.group != group):
            continue
        if s.module == "product_spreads" and s.family:
            if s.tenor != (tenor or "c3"):
                continue
        keys.append(s.key)
    return [series_payload(db, k, years, unit) for k in keys]


def export_rows(db: DbSession, key: str, unit: str) -> tuple[list[str], list[tuple]]:
    sd = SERIES_BY_KEY[key]
    use_local = unit == "local" and sd.local_unit is not None
    pts = _points(db, key, None)
    col = "value_local" if use_local else "value"
    rows = [(d.isoformat(), None if pd.isna(v) else round(float(v), 6)) for d, v in pts[col].items()]
    return ["date", f"{key} ({sd.local_unit if use_local else sd.unit})"], rows


# ── Prices (raw) ────────────────────────────────────────────────────────────

def _prices(db: DbSession, rics: list[str], start: date | None = None) -> pd.DataFrame:
    sql = ("SELECT trade_date AS date, ric, price::float8 AS price FROM market.price_daily "
           "WHERE ric = ANY(:rics) AND price IS NOT NULL")
    params: dict = {"rics": rics}
    if start:
        sql += " AND trade_date >= :s"
        params["s"] = start
    df = pd.DataFrame(db.execute(text(sql), params).all(), columns=["date", "ric", "price"])
    if df.empty:
        return pd.DataFrame()
    w = df.pivot(index="date", columns="ric", values="price").sort_index()
    w.columns.name = None
    return w


def _fx(db: DbSession, start: date | None = None) -> pd.DataFrame:
    w = _prices(db, ["MYR=", "CNY="], start)
    return w.ffill() if not w.empty else w


# ── Overview ────────────────────────────────────────────────────────────────

def overview(db: DbSession) -> dict:
    def q():
        start = date.today() - timedelta(days=40)
        w = _prices(db, OVERVIEW_TILES, start)
        fx = _fx(db, start - timedelta(days=10))
        tiles = []
        for ric in OVERVIEW_TILES:
            ctype, _, _, native = instrument_conversion(ric)
            if w.empty or ric not in w.columns:
                tiles.append({"ric": ric, "title": ric_display(ric), "unit": native, "value": None})
                continue
            s = w[ric].dropna()
            if s.empty:
                tiles.append({"ric": ric, "title": ric_display(ric), "unit": native, "value": None})
                continue
            last, prev = float(s.iloc[-1]), (float(s.iloc[-2]) if len(s) > 1 else None)
            usd = to_usd(s.iloc[-1:], ric, fx) if not ric.endswith("=") else None
            spark = [round(float(v), 4) for v in s.iloc[-22:].tolist()]
            tiles.append({
                "ric": ric, "title": ric_display(ric), "unit": native, "value": last,
                "as_of": s.index[-1].isoformat(), "change": None if prev is None else last - prev,
                "change_pct": None if not prev else (last - prev) / prev * 100,
                "usd_mt": None if usd is None or usd.empty else float(usd.iloc[-1]), "spark": spark,
            })
        meta = db.execute(text(
            "SELECT max(trade_date) FROM market.price_daily")).scalar_one()
        run = db.execute(text(
            "SELECT id, kind, status, finished_at FROM ops.pipeline_runs WHERE status IN ('succeeded','partial') "
            "ORDER BY finished_at DESC NULLS LAST LIMIT 1")).first()
        counts = db.execute(text("SELECT (SELECT count(*) FROM market.series WHERE is_active), "
                                 "(SELECT count(DISTINCT series_key) FROM market.series_points), "
                                 "(SELECT count(*) FROM market.instruments WHERE is_active)")).first()
        return {"tiles": tiles, "latest_trade_date": meta.isoformat() if meta else None,
                "last_run": None if run is None else {"id": str(run.id), "kind": run.kind, "status": run.status,
                                                      "finished_at": run.finished_at},
                "series_defined": counts[0], "series_with_data": counts[1], "instruments": counts[2]}
    return _cached(("overview", data_version(db)), q)


# ── Forward curves ──────────────────────────────────────────────────────────

def _snapshot_rows(curve: pd.DataFrame, years: list[int], snapshot_md: str) -> list[dict]:
    traces = []
    for year in sorted(set(years)):
        try:
            target = pd.Timestamp(f"{year}-{snapshot_md}")
        except ValueError:
            target = pd.Timestamp(f"{year}-02-28")
        idx = pd.to_datetime(curve.index)
        yr = curve.loc[(idx.year == year) & (idx <= target)]
        if yr.empty:
            continue
        row = yr.iloc[-1]
        if row.isna().all():
            continue
        traces.append({"year": year, "as_of": pd.Timestamp(yr.index[-1]).date().isoformat(),
                       "x": list(row.index), "y": [None if pd.isna(v) else round(float(v), 6) for v in row]})
    return traces


def _outright_curves(db: DbSession, start: date | None, unit: str) -> list[tuple[str, str, pd.DataFrame]]:
    """Per futures product: (code, unit, c1..c7 frame) — legacy dashboard.load_forward_curves.

    Prices are pivoted across ALL products' contracts and forward-filled, so each frame carries the union of
    trading dates (as in the legacy). Missing tenors are omitted columns; an empty frame means no data.
    """
    rics = [futures_ric(p, c) for p in FORWARD_CURVE_PRODUCTS for c in range(1, 8)]
    w = _prices(db, rics, start)
    fx = _fx(db, start)
    out = []
    for p in FORWARD_CURVE_PRODUCTS:
        prod = PRODUCTS[p]
        cols = {f"c{c}": futures_ric(p, c) for c in range(1, 8) if futures_ric(p, c) in w.columns}
        if not cols:
            out.append((p, "USD/MT" if unit != "local" else prod.native_unit, pd.DataFrame()))
            continue
        local = w[list(cols.values())].rename(columns={v: k for k, v in cols.items()}).ffill()
        local = local.dropna(how="all")
        if unit == "local":
            out.append((p, prod.native_unit, local))
        else:
            out.append((p, "USD/MT", pd.DataFrame({c: to_usd(local[c], cols[c], fx) for c in local.columns})))
    return out


def forward_curves(db: DbSession, kind: str, years: list[int], snapshot_md: str, unit: str) -> list[dict]:
    years = sorted(set(years))

    def q():
        start = date(min(years), 1, 1) if years else None
        out = []
        if kind == "outright":
            for p, u, curve in _outright_curves(db, start, unit):
                title = f"{PRODUCTS[p].display_name} Forward Curve"
                out.append({"key": p, "title": title, "unit": u,
                            "traces": _snapshot_rows(curve, years, snapshot_md) if not curve.empty else []})
        else:
            for fam in PS_FAMILIES:
                cols = {}
                for c in PS_CONTRACTS:
                    pts = _points(db, f"{fam}_{c}", start)
                    if not pts.empty:
                        cols[c] = pts["value"]
                title = SERIES_BY_KEY[f"{fam}_c3"].title.replace(" C3", "").replace(" c3 (Dalian)", " (Dalian)")
                curve = pd.DataFrame(cols).sort_index() if cols else pd.DataFrame()
                out.append({"key": fam, "title": f"{title} Forward Curve", "unit": "USD/MT",
                            "traces": _snapshot_rows(curve, years, snapshot_md) if cols else []})
        return out
    return _cached(("fwd", kind, tuple(years), snapshot_md, unit, data_version(db)), q)


def _series_xy(s: pd.Series) -> dict:
    s = s.dropna()
    return {"x": [pd.Timestamp(d).date().isoformat() for d in s.index], "y": [round(float(v), 6) for v in s]}


def ytd_spreads(db: DbSession, year: int, unit: str) -> list[dict]:
    """Calendar Spreads → YTD (legacy dashboard.make_ytd_spread_chart): for one year, on actual trading dates,
    the C1−C2 … C1−C7 legs of every futures product plus the C1 price (shown on a secondary axis)."""
    def q():
        # Start a month early so the forward-fill carries over into the first trading days of January.
        frames = _outright_curves(db, date(year - 1, 12, 1), unit)
        out = []
        for p, u, curve in frames:
            item = {"key": p, "title": PRODUCTS[p].display_name, "unit": u, "year": year, "legs": [], "front": None}
            if curve.empty or "c1" not in curve.columns:
                out.append(item)
                continue
            yr = curve.loc[pd.to_datetime(curve.index).year == year]
            for leg in ["c2", "c3", "c4", "c5", "c6", "c7"]:
                if leg not in yr.columns:
                    continue
                spread = (yr["c1"] - yr[leg]).dropna()
                if not spread.empty:
                    item["legs"].append({"label": f"C1-{leg.upper()}", **_series_xy(spread)})
            front = yr["c1"].dropna()
            if not front.empty:
                item["front"] = {"label": "C1 Price", **_series_xy(front)}
            out.append(item)
        return out
    return _cached(("ytd", year, unit, data_version(db)), q)


# ── OI / Volume ─────────────────────────────────────────────────────────────

def oi_tables(db: DbSession, days: int) -> list[dict]:
    def q():
        thresholds = {r.product_code: (r.median_pct, r.p75_pct) for r in db.execute(text(
            "SELECT product_code, median_pct, p75_pct FROM market.oi_arrow_thresholds"))}
        out = []
        for p in sorted(PRODUCTS.values(), key=lambda x: x.oi_display_order):
            rows = db.execute(text(
                "SELECT obs_date, total_oi, oi_chg, total_volume, volume_chg, oi_vol_pct, avg_price, "
                "price_chg_pct, contracts_reporting FROM market.oi_cumulative WHERE product_code = :p "
                "ORDER BY obs_date DESC LIMIT :n"), {"p": p.code, "n": days}).all()
            med, p75 = thresholds.get(p.code, (1.0, 2.0))
            items = []
            for r in rows:
                direction, mag = oi_engine.arrow(r.price_chg_pct, med, p75)
                items.append({"date": r.obs_date.isoformat(), "total_oi": r.total_oi, "oi_chg": r.oi_chg,
                              "total_volume": r.total_volume, "volume_chg": r.volume_chg,
                              "oi_vol_pct": r.oi_vol_pct, "oi_vol_flag": oi_engine.oi_vol_flag(r.oi_vol_pct),
                              "avg_price": r.avg_price, "price_chg_pct": r.price_chg_pct,
                              "arrow": direction, "arrow_n": mag, "contracts": r.contracts_reporting})
            out.append({"product": p.code, "title": p.oi_display_name, "unit": p.native_unit,
                        "contracts_scanned": p.oi_contracts, "threshold_median": med, "threshold_p75": p75,
                        "rows": items})
        return out
    return _cached(("oi", days, data_version(db)), q)


# ── Technical analysis ──────────────────────────────────────────────────────

def ta_instruments() -> list[dict]:
    items = [{"key": r, "title": ric_display(r), "kind": "price",
              "native_unit": instrument_conversion(r)[3]} for r in TA_PRICE_INSTRUMENTS]
    items += [{"key": k, "title": SERIES_BY_KEY[k].title, "kind": "spread", "native_unit": SERIES_BY_KEY[k].unit}
              for k in TA_SPREAD_INSTRUMENTS if k in SERIES_BY_KEY]
    return items


def ta_payload(db: DbSession, key: str, start_year: int, unit: str) -> dict:
    def q():
        start = date(start_year, 1, 1)
        if key in TA_PRICE_INSTRUMENTS:
            w = _prices(db, [key], start)
            if w.empty:
                return {"key": key, "title": ric_display(key), "unit": None, "dates": []}
            s = w[key].ffill().dropna()
            native = instrument_conversion(key)[3]
            if unit != "local" and not key.endswith("="):
                s = to_usd(s, key, _fx(db, start - timedelta(days=10))).dropna()
                u = "USD/MT"
            else:
                u = native
            title = ric_display(key)
        else:
            sd = SERIES_BY_KEY[key]
            s = _points(db, key, start)["value"].dropna()
            u, title = sd.unit, sd.title
        s.index = pd.to_datetime(s.index)
        levels = ta_engine.ranked_levels(s) if len(s) >= 30 else []
        sm = ta_engine.smas(s)

        def ser(x: pd.Series):
            return [None if pd.isna(v) else round(float(v), 6) for v in x]
        return {"key": key, "title": title, "unit": u, "dates": [d.date().isoformat() for d in s.index],
                "price": ser(s), "sma50": ser(sm["sma50"]), "sma100": ser(sm["sma100"]),
                "sma200": ser(sm["sma200"]), "levels": [round(v, 6) for v in levels]}
    return _cached(("ta", key, start_year, unit, data_version(db)), q)
