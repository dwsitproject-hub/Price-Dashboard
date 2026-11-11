"""AI analysis — port of legacy scripts/ai_analysis.py, with Claude in place of Gemini.

The seven quantitative sections are computed at request time from market.price_daily (outrights) or
market.series_points (spreads); the narrative is written by Claude in a background job (ops.jobs, ai_narrative)
from the same "raw statistics" text the legacy sent to Gemini. Fixes vs the legacy: percentages divide by the
absolute base (spreads can be negative), 0.0 is not treated as missing, k-means never asks for more clusters than
points, and only real price/series keys are offered (the legacy listed any CSV and crashed on non-series files).
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
from kneed import KneeLocator
from sklearn.cluster import KMeans
from sqlalchemy import text

from app.catalog.instruments import CASH, FX, PRODUCTS, futures_ric
from app.catalog.registry import FORWARD_CURVE_PRODUCTS, MODULES, SERIES, ric_display
from app.core.db import get_engine
from app.core.errors import ApiError
from app.integrations import claude
from app.intel.jobs import register

TIMEFRAMES = {"1M": 21, "3M": 63, "6M": 126, "1Y": 252, "3Y": 756, "5Y": 1260, "All": None}
MONTHS = {1: "Jan", 2: "Feb", 3: "Mar", 4: "Apr", 5: "May", 6: "Jun", 7: "Jul", 8: "Aug", 9: "Sep", 10: "Oct",
          11: "Nov", 12: "Dec"}
NOISE = 0.1   # legacy oi_analyze.NOISE: |price change %| below this → Neutral
SIGNALS = ["Long Buildup", "Short Buildup", "Short Covering", "Long Unwinding", "Neutral"]

SYSTEM = ("You are a commodity markets analyst. Be concise, specific, and actionable.\n"
          "Use precise numbers from the data provided. Do not invent data not present in the statistics.")
PROMPT = """Analyse the following instrument statistics and produce a structured report with
clearly labelled sections matching each of the 7 sections below.
End with a "Key Takeaways" section containing 3-5 bullet points summarising
the most important signals for a trader.

Instrument: {instrument}
Timeframe: {timeframe}

--- COMPUTED STATISTICS ---
{raw_stats}
--- END ---

Format the output as clean markdown with ## headings for each section."""


# ── Instruments ─────────────────────────────────────────────────────────────

def instruments(allowed_modules: list[str]) -> list[dict]:
    """Legacy build_instrument_options: outright futures c1–c7, FX and palm cash, then the derived series."""
    groups = [{"group": "Outright Prices", "options": []}]
    for p in FORWARD_CURVE_PRODUCTS:
        for c in range(1, 8):
            ric = futures_ric(p, c)
            groups[0]["options"].append({"key": f"outright::{ric}", "label": f"{ric_display(ric)} ({PRODUCTS[p].native_unit})"})
    for ric, f in FX.items():
        groups[0]["options"].append({"key": f"outright::{ric}", "label": f"{f.display_name} ({ric})"})
    for ric, c in CASH.items():
        if c.category in ("malaysia_cash", "indonesia_cash"):
            groups[0]["options"].append({"key": f"outright::{ric}", "label": f"{c.display_name} ({c.native_unit})"})
    allowed = set(allowed_modules)
    for m in MODULES:
        if m.kind != "seasonal" or m.code not in allowed or m.code == "outright_prices":
            continue
        opts = [{"key": f"spread::{s.key}", "label": f"{s.title} ({s.unit})"} for s in SERIES if s.module == m.code]
        if opts:
            groups.append({"group": m.name, "options": opts})
    return groups


def _label(key: str) -> str:
    for g in instruments([m.code for m in MODULES]):
        for o in g["options"]:
            if o["key"] == key:
                return o["label"]
    raise ApiError(404, "unknown_instrument", "Unknown instrument.")


def series_module(key: str) -> str | None:
    kind, ident = key.split("::", 1)
    if kind != "spread":
        return None
    return next((s.module for s in SERIES if s.key == ident), None)


def load_series(key: str) -> pd.DataFrame:
    kind, ident = key.split("::", 1)
    with get_engine().connect() as c:
        if kind == "outright":
            rows = c.execute(text("SELECT trade_date AS date, price::float8 AS value FROM market.price_daily "
                                  "WHERE ric = :r AND price IS NOT NULL ORDER BY trade_date"), {"r": ident}).all()
        elif kind == "spread":
            rows = c.execute(text("SELECT obs_date AS date, value FROM market.series_points WHERE series_key = :k "
                                  "AND value IS NOT NULL ORDER BY obs_date"), {"k": ident}).all()
        else:
            raise ApiError(404, "unknown_instrument", "Unknown instrument.")
    df = pd.DataFrame(rows, columns=["date", "value"])
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
        df["value"] = df["value"].astype(float)
    return df.reset_index(drop=True)


def _pct(cur: float, base: float | None) -> float | None:
    if base is None or pd.isna(base) or base == 0:
        return None
    return (cur - base) / abs(base) * 100


def _r(v, n=2):
    return None if v is None or (isinstance(v, float) and np.isnan(v)) else round(float(v), n)


# ── Sections (legacy compute_*) ─────────────────────────────────────────────

def compute_seasonal(df: pd.DataFrame) -> dict:
    d = df.assign(month=df["date"].dt.month)
    monthly = d.groupby("month")["value"].mean()
    cm = int(df["date"].max().month)
    cur = float(df["value"].iloc[-1])
    avg = monthly.get(cm, np.nan)
    return {"monthly_averages": {MONTHS[int(m)]: round(float(v), 2) for m, v in monthly.items()},
            "current_month": MONTHS[cm], "current_value": round(cur, 2),
            "seasonal_average": _r(avg), "pct_deviation": _r(_pct(cur, None if pd.isna(avg) else float(avg)))}


def compute_extremes(df: pd.DataFrame) -> dict:
    v = df["value"].values
    cur, mean, std = v[-1], np.nanmean(v), np.nanstd(v)
    z = (cur - mean) / std if std > 0 else 0.0
    pct = float(np.nanmean(v <= cur) * 100)
    return {"current_value": _r(cur), "mean": _r(mean), "std": _r(std), "z_score": _r(z),
            "percentile_rank": round(pct, 1), "in_top_10_pct": pct >= 90, "in_bottom_10_pct": pct <= 10}


def compute_highs_lows(df: pd.DataFrame) -> dict:
    cur = float(df["value"].iloc[-1])
    hi, lo = df["value"].idxmax(), df["value"].idxmin()
    return {"current_value": _r(cur), "period_high": _r(df.loc[hi, "value"]),
            "period_high_date": df.loc[hi, "date"].strftime("%Y-%m-%d"), "period_low": _r(df.loc[lo, "value"]),
            "period_low_date": df.loc[lo, "date"].strftime("%Y-%m-%d"),
            "pct_from_high": _r(_pct(cur, float(df.loc[hi, "value"]))),
            "pct_from_low": _r(_pct(cur, float(df.loc[lo, "value"]))),
            "rolling_20d_high": _r(df["value"].rolling(20, min_periods=1).max().iloc[-1]),
            "rolling_20d_low": _r(df["value"].rolling(20, min_periods=1).min().iloc[-1])}


def _classify(price_pct: float, oi_pct: float) -> str:
    """Legacy oi_analyze._classify."""
    if abs(price_pct) < NOISE:
        return "Neutral"
    if price_pct > 0 and oi_pct > 0:
        return "Long Buildup"
    if price_pct < 0 and oi_pct > 0:
        return "Short Buildup"
    if price_pct > 0 and oi_pct <= 0:
        return "Short Covering"
    return "Long Unwinding"


def compute_oi(ric: str) -> dict | None:
    """Legacy compute_oi from oi_latest/oi_summary/oi_analysis, computed from market.price_daily. The 15/30-day
    counts use the last 15/30 trading days (the legacy oi_summary also masked contract-roll days)."""
    with get_engine().connect() as c:
        rows = c.execute(text("SELECT trade_date, price::float8 AS price, open_interest::float8 AS oi, "
                              "volume::float8 AS volume FROM market.price_daily WHERE ric = :r "
                              "AND trade_date >= now() - interval '120 days' ORDER BY trade_date"), {"r": ric}).all()
    df = pd.DataFrame(rows, columns=["date", "price", "oi", "volume"])
    if df.empty or df["oi"].isna().all():
        return None
    df["price_chg_pct"] = df["price"].pct_change(fill_method=None) * 100
    df["oi_chg_pct"] = df["oi"].pct_change(fill_method=None) * 100
    df["signal"] = [_classify(p if pd.notna(p) else 0.0, o if pd.notna(o) else 0.0)
                    for p, o in zip(df["price_chg_pct"], df["oi_chg_pct"])]
    df = df.iloc[1:]
    last = df.iloc[-1]

    def counts(n):
        vc = df.tail(n)["signal"].value_counts()
        return {s: int(vc.get(s, 0)) for s in SIGNALS}
    return {"latest_signal": last["signal"], "latest_date": last["date"].isoformat(),
            "latest_price": _r(last["price"]), "latest_oi": None if pd.isna(last["oi"]) else int(last["oi"]),
            "latest_volume": None if pd.isna(last["volume"]) else int(last["volume"]),
            "signal_15d": counts(15), "signal_30d": counts(30),
            "recent_signals": [{"date": r.date.isoformat(), "signal": r.signal, "price_chg_pct": _r(r.price_chg_pct),
                                "oi_chg_pct": _r(r.oi_chg_pct)} for r in df.tail(5).itertuples()]}


def _optimal_k(data: np.ndarray, max_k: int = 6) -> int:
    n = len(data)
    if n < 2:
        return max(1, n)
    max_k = min(max_k, n)
    wcss = [KMeans(n_clusters=k, random_state=0, n_init=10).fit(data).inertia_ for k in range(1, max_k + 1)]
    kn = KneeLocator(range(1, max_k + 1), wcss, curve="convex", direction="decreasing")
    return min(kn.elbow or 3, n)


def compute_support_resistance(df: pd.DataFrame) -> dict:
    v = df["value"].values
    cur = float(v[-1])
    if len(v) < 30:
        return {"levels": [], "support_levels": [], "resistance_levels": [], "current_value": _r(cur),
                "nearest_support": None, "nearest_resistance": None, "support_distance_pct": None,
                "resistance_distance_pct": None}
    mins, maxs = [], []
    for i in range(0, len(v) - 30 + 1, 5):
        w = v[i:i + 30]
        mins.append(np.min(w))
        maxs.append(np.max(w))
    mins, maxs = np.array(mins).reshape(-1, 1), np.array(maxs).reshape(-1, 1)
    c_min = KMeans(n_clusters=_optimal_k(mins), random_state=0, n_init=10).fit(mins).cluster_centers_.flatten()
    c_max = KMeans(n_clusters=_optimal_k(maxs), random_state=0, n_init=10).fit(maxs).cluster_centers_.flatten()
    levels = sorted(np.concatenate([c_min, c_max]))
    sup = [x for x in levels if x < cur][-5:]
    res = [x for x in levels if x > cur][:5]
    ns = max(sup) if sup else None
    nr = min(res) if res else None
    return {"levels": [_r(x) for x in levels], "support_levels": [_r(x) for x in sup],
            "resistance_levels": [_r(x) for x in res], "current_value": _r(cur),
            "nearest_support": _r(ns) if ns is not None else None, "nearest_resistance": _r(nr) if nr is not None else None,
            "support_distance_pct": _r((cur - ns) / abs(ns) * 100) if ns not in (None, 0) else None,
            "resistance_distance_pct": _r((nr - cur) / abs(cur) * 100) if nr is not None and cur != 0 else None}


def compute_year_resemblance(full: pd.DataFrame) -> tuple[dict, list[dict]]:
    d = full.assign(year=full["date"].dt.year, doy=full["date"].dt.dayofyear)
    cy = int(d["year"].max())
    cur = d[d["year"] == cy].set_index("doy")["value"]
    if len(cur) < 10:
        return {"top_matches": [], "current_year": cy, "ytd_days": len(cur)}, []
    corr = []
    for yr in sorted(d["year"].unique()):
        if yr == cy:
            continue
        ser = d[d["year"] == yr].set_index("doy")["value"]
        common = cur.index.intersection(ser.index)
        if len(common) < 10:
            continue
        c = cur.loc[common].corr(ser.loc[common])
        if pd.notna(c):
            corr.append({"year": int(yr), "correlation": round(float(c), 4)})
    corr.sort(key=lambda x: abs(x["correlation"]), reverse=True)   # legacy: ranked by |correlation|
    top = corr[:3]
    traces = []
    for yr in [cy] + [m["year"] for m in top]:            # chart: % from the first value of each year
        s = d[d["year"] == yr].sort_values("doy")
        base = float(s["value"].iloc[0])
        traces.append({"year": int(yr), "x": [int(x) for x in s["doy"]],
                       "y": [_r((float(v) - base) / abs(base) * 100) if base else None for v in s["value"]]})
    return {"top_matches": top, "current_year": cy, "ytd_days": len(cur)}, traces


def compute_momentum(df: pd.DataFrame) -> dict:
    v = df["value"]
    cur = float(v.iloc[-1])
    roc20 = _pct(cur, float(v.iloc[-21])) if len(v) > 20 else None
    roc60 = _pct(cur, float(v.iloc[-61])) if len(v) > 60 else None
    sma20 = float(v.rolling(20).mean().iloc[-1]) if len(v) >= 20 else None
    sma60 = float(v.rolling(60).mean().iloc[-1]) if len(v) >= 60 else None
    if sma20 is not None and sma60 is not None:
        trend = ("Uptrend" if sma20 > sma60 and cur > sma20 else
                 "Downtrend" if sma20 < sma60 and cur < sma20 else "Sideways")
    else:
        trend = "Insufficient data"
    return {"current_value": _r(cur), "roc_20d_pct": _r(roc20), "roc_60d_pct": _r(roc60), "sma_20": _r(sma20),
            "sma_60": _r(sma60), "vs_sma_20_pct": _r(_pct(cur, sma20)), "vs_sma_60_pct": _r(_pct(cur, sma60)),
            "trend": trend}


def format_raw_stats(label: str, timeframe: str, s: dict) -> str:
    """Legacy _format_raw_stats (the '% vs current' SMA label now says what the number is)."""
    se, e, h, oi, sr, yr, mo = (s["seasonal"], s["extremes"], s["highs_lows"], s["oi"], s["support_resistance"],
                                s["year_resemblance"], s["momentum"])
    lines = [f"Instrument: {label}", f"Timeframe: {timeframe}", "",
             "## 1. SEASONAL PATTERN", f"Monthly averages: {se['monthly_averages']}",
             f"Current month: {se['current_month']} | Value: {se['current_value']} | Seasonal avg: "
             f"{se['seasonal_average']} | Deviation: {se['pct_deviation']}%", "",
             "## 2. EXTREME VALUES", f"Current: {e['current_value']} | Mean: {e['mean']} | Std: {e['std']}",
             f"Z-score: {e['z_score']} | Percentile: {e['percentile_rank']}%",
             f"In top 10%: {e['in_top_10_pct']} | In bottom 10%: {e['in_bottom_10_pct']}", "",
             "## 3. HIGHS AND LOWS",
             f"Period high: {h['period_high']} ({h['period_high_date']}) | Low: {h['period_low']} ({h['period_low_date']})",
             f"Current vs high: {h['pct_from_high']}% | vs low: {h['pct_from_low']}%",
             f"20D rolling high: {h['rolling_20d_high']} | low: {h['rolling_20d_low']}", "", "## 4. OI BUILDUP"]
    if "note" in oi:
        lines.append(oi["note"])
    else:
        lines.append(f"Latest signal: {oi['latest_signal']} ({oi['latest_date']})")
        lines.append(f"Price: {oi['latest_price']} | OI: {oi['latest_oi']:,}" if oi.get("latest_oi") is not None else "")
        lines += [f"15D distribution: {oi['signal_15d']}", f"30D distribution: {oi['signal_30d']}", "Recent 5 signals:"]
        lines += [f"  {r['date']}: {r['signal']} (price {r['price_chg_pct']}%, OI {r['oi_chg_pct']}%)"
                  for r in oi["recent_signals"]]
    lines += ["", "## 5. SUPPORT & RESISTANCE", f"Current: {sr['current_value']}",
              f"Support levels: {sr['support_levels']}", f"Resistance levels: {sr['resistance_levels']}",
              f"Nearest support: {sr['nearest_support']} ({sr['support_distance_pct']}% away)",
              f"Nearest resistance: {sr['nearest_resistance']} ({sr['resistance_distance_pct']}% away)", "",
              "## 6. YEAR RESEMBLANCE", f"Current year: {yr['current_year']} ({yr['ytd_days']} trading days YTD)"]
    lines += [f"  {m['year']}: correlation = {m['correlation']}" for m in yr["top_matches"]]
    lines += ["", "## 7. MOMENTUM & TREND", f"Current: {mo['current_value']} | Trend: {mo['trend']}",
              f"20D ROC: {mo['roc_20d_pct']}% | 60D ROC: {mo['roc_60d_pct']}%",
              f"SMA20: {mo['sma_20']} (current {mo['vs_sma_20_pct']}% vs SMA20) | SMA60: {mo['sma_60']} "
              f"(current {mo['vs_sma_60_pct']}% vs SMA60)"]
    return "\n".join(lines)


def analyse(key: str, timeframe: str) -> dict:
    """Sections + chart data for one instrument (legacy run_analysis without the LLM call)."""
    if timeframe not in TIMEFRAMES:
        raise ApiError(422, "bad_timeframe", f"timeframe must be one of {', '.join(TIMEFRAMES)}")
    label = _label(key)
    full = load_series(key)
    if full.empty or len(full) < 2:
        raise ApiError(404, "no_data", "Data not available for this instrument.")
    n = TIMEFRAMES[timeframe]
    window = full.tail(n).reset_index(drop=True) if n is not None and len(full) > n else full.copy()
    kind, ident = key.split("::", 1)
    oi = compute_oi(ident) if kind == "outright" and ident not in FX and ident not in CASH else None
    resemblance, res_traces = compute_year_resemblance(full)
    sections = {"seasonal": compute_seasonal(full), "extremes": compute_extremes(window),
                "highs_lows": compute_highs_lows(window),
                "oi": oi or {"note": "OI data not available for this instrument."},
                "support_resistance": compute_support_resistance(window), "year_resemblance": resemblance,
                "momentum": compute_momentum(window)}
    v = window["value"]
    chart = {"dates": [d.date().isoformat() for d in window["date"]], "values": [_r(x, 4) for x in v],
             "roll_high": [_r(x, 4) for x in v.rolling(20, min_periods=1).max()],
             "roll_low": [_r(x, 4) for x in v.rolling(20, min_periods=1).min()],
             "sma20": [_r(x, 4) for x in v.rolling(20).mean()], "sma60": [_r(x, 4) for x in v.rolling(60).mean()],
             "resemblance": res_traces}
    sections["chart"] = chart
    raw = format_raw_stats(label, timeframe, sections)
    return {"instrument": key, "label": label, "timeframe": timeframe, "sections": sections, "raw_text": raw}


def create_report(key: str, timeframe: str, user_id) -> dict:
    from app.core.config import get_settings
    from app.intel import jobs
    a = analyse(key, timeframe)
    status = "pending" if get_settings().claude_configured else "skipped"
    with get_engine().begin() as c:
        rid = c.execute(text(
            "INSERT INTO intel.ai_reports (instrument, instrument_label, timeframe, sections, raw_text, status, "
            "error, requested_by) VALUES (:i, :l, :t, CAST(:s AS jsonb), :raw, :st, :err, :u) RETURNING id"),
            {"i": key, "l": a["label"], "t": timeframe, "s": json.dumps(a["sections"]), "raw": a["raw_text"],
             "st": status, "u": user_id,
             "err": None if status == "pending" else "Claude is not configured (ANTHROPIC_API_KEY is not set)."}
        ).scalar_one()
    if status == "pending":
        jobs.enqueue("ai_narrative", params={"report_id": str(rid)}, user_id=user_id)
    return get_report(rid)


def get_report(report_id) -> dict:
    with get_engine().connect() as c:
        r = c.execute(text("SELECT id, instrument, instrument_label, timeframe, sections, raw_text, narrative, status, "
                           "error, model, created_at, finished_at FROM intel.ai_reports WHERE id = :id"),
                      {"id": report_id}).mappings().first()
    if r is None:
        raise ApiError(404, "not_found", "Report not found.")
    return dict(r)


def recent_reports(limit: int = 15) -> list[dict]:
    with get_engine().connect() as c:
        rows = c.execute(text("SELECT r.id, r.instrument, r.instrument_label, r.timeframe, r.status, r.created_at, "
                              "u.email AS requested_by FROM intel.ai_reports r LEFT JOIN iam.users u "
                              "ON u.id = r.requested_by ORDER BY r.created_at DESC LIMIT :n"), {"n": limit}).mappings().all()
    return [dict(x) for x in rows]


@register("ai_narrative")
def write_narrative(params: dict) -> dict:
    rid = params["report_id"]
    with get_engine().connect() as c:
        r = c.execute(text("SELECT instrument_label, timeframe, raw_text FROM intel.ai_reports WHERE id = :id"),
                      {"id": rid}).first()
    if r is None:
        return {"skipped": "report deleted"}
    try:
        res = claude.run(system=SYSTEM, content=PROMPT.format(instrument=r.instrument_label, timeframe=r.timeframe,
                                                              raw_stats=r.raw_text),
                         max_tokens=16000, effort="medium")
    except claude.ClaudeError as exc:
        with get_engine().begin() as c:
            c.execute(text("UPDATE intel.ai_reports SET status = 'failed', error = :e, finished_at = now() WHERE id = :id"),
                      {"e": str(exc), "id": rid})
        raise
    with get_engine().begin() as c:
        c.execute(text("UPDATE intel.ai_reports SET status = 'ready', narrative = :n, model = :m, "
                       "usage = CAST(:u AS jsonb), finished_at = now() WHERE id = :id"),
                  {"n": res.text, "m": res.model, "u": json.dumps(res.usage), "id": rid})
    return {"report_id": rid, "chars": len(res.text), **res.usage}
