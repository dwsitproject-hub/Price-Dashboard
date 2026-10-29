"""
AI Analysis module for the LSEGDashboard.
Computes 7 analysis sections for any instrument + timeframe,
then calls Gemini LLM for narrative report.
Loaded lazily — nothing runs at import time.
"""

import configparser
import logging
from pathlib import Path
from functools import lru_cache

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from kneed import KneeLocator

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent
RAW_FILE = ROOT / "data" / "raw" / "all_instruments_raw.csv"
PROC_DIR = ROOT / "data" / "processed"
CRED_FILE = ROOT / "credentials.ini"

# ── Timeframe definitions ────────────────────────────────────────────────────

TIMEFRAMES = {
    "1M":  21,
    "3M":  63,
    "6M":  126,
    "1Y":  252,
    "3Y":  756,
    "5Y":  1260,
    "All": None,
}

# ── Instrument universe builders ─────────────────────────────────────────────

def build_instrument_options() -> list[dict]:
    """Build grouped dropdown options for all instruments. Called once at tab render."""
    import sys
    sys.path.insert(0, str(ROOT))
    import config
    from gsheet_data import INDIA_TITLES, INDIA_OUTRIGHT_KEYS, INDIA_SPREAD_DEFS

    options = []

    # Group 1: Outright prices (all RICs from config)
    outright_opts = []
    for group_name, rics in config.RICS["futures"].items():
        for ric in rics:
            outright_opts.append({"label": ric, "value": f"outright::{ric}"})
    for ric in config.RICS["fx"]:
        outright_opts.append({"label": ric, "value": f"outright::{ric}"})
    for ric in config.RICS["malaysia_cash"]:
        outright_opts.append({"label": ric, "value": f"outright::{ric}"})
    for ric in config.RICS["indonesia_cash"]:
        outright_opts.append({"label": ric, "value": f"outright::{ric}"})
    options.append({"label": "Outright Prices", "value": "", "disabled": True})
    options.extend(outright_opts)

    # Group 2: Spreads (processed CSVs)
    spread_opts = []
    for csv in sorted(PROC_DIR.glob("*.csv")):
        stem = csv.stem
        if stem in ("oi_analysis", "oi_summary", "oi_latest"):
            continue
        label = stem.replace("_", " ")
        spread_opts.append({"label": label, "value": f"spread::{stem}"})
    options.append({"label": "Spreads", "value": "", "disabled": True})
    options.extend(spread_opts)

    # Group 3: India Local outrights
    options.append({"label": "India Local Outrights", "value": "", "disabled": True})
    for key in INDIA_OUTRIGHT_KEYS:
        options.append({"label": INDIA_TITLES[key], "value": f"india_out::{key}"})

    # Group 4: India Local spreads
    options.append({"label": "India Local Spreads", "value": "", "disabled": True})
    for _ka, _kb, sk in INDIA_SPREAD_DEFS:
        options.append({"label": INDIA_TITLES[sk], "value": f"india_spr::{sk}"})

    return options


# ── Lazy data loaders with caching ───────────────────────────────────────────

_cache: dict[str, pd.DataFrame] = {}


def _load_outright(ric: str) -> pd.DataFrame | None:
    """Load outright price series from raw CSV. Returns df with [date, value]."""
    key = f"outright::{ric}"
    if key in _cache:
        return _cache[key]
    if not RAW_FILE.exists():
        return None
    raw = pd.read_csv(RAW_FILE, parse_dates=["Trade Date"])
    raw = raw.rename(columns={"Trade Date": "date"})
    raw["date"] = pd.to_datetime(raw["date"]).dt.normalize()
    sub = raw[raw["RIC"] == ric].copy()
    if sub.empty:
        return None
    sub["Last"] = pd.to_numeric(sub.get("Last"), errors="coerce")
    sub["Settlement Price"] = pd.to_numeric(sub.get("Settlement Price"), errors="coerce")
    sub["Bid"] = pd.to_numeric(sub.get("Bid"), errors="coerce")
    sub["Ask"] = pd.to_numeric(sub.get("Ask"), errors="coerce")
    # FX uses mid, others use Last then Settlement
    import sys; sys.path.insert(0, str(ROOT)); import config
    if ric in config.FX_RICS:
        sub["value"] = (sub["Bid"] + sub["Ask"]) / 2
    else:
        sub["value"] = np.where(sub["Last"].notna(), sub["Last"], sub["Settlement Price"])
    result = sub[["date", "value"]].dropna().drop_duplicates("date").sort_values("date").reset_index(drop=True)
    _cache[key] = result
    return result


def _load_spread(stem: str) -> pd.DataFrame | None:
    """Load processed spread CSV. Returns df with [date, value]."""
    key = f"spread::{stem}"
    if key in _cache:
        return _cache[key]
    path = PROC_DIR / f"{stem}.csv"
    if not path.exists():
        return None
    df = pd.read_csv(path, parse_dates=["date"])
    df = df.rename(columns={"spread_value": "value"})
    result = df[["date", "value"]].dropna().sort_values("date").reset_index(drop=True)
    _cache[key] = result
    return result


def _load_india_outright(key: str) -> pd.DataFrame | None:
    cache_key = f"india_out::{key}"
    if cache_key in _cache:
        return _cache[cache_key]
    from gsheet_data import fetch_india_data
    result = fetch_india_data()
    if result is None:
        return None
    usd_out, inr_out, _, _ = result
    # Cache all at once
    for k, df in usd_out.items():
        ck = f"india_out::{k}"
        if ck not in _cache:
            _cache[ck] = df[["date", "spread_value"]].rename(columns={"spread_value": "value"}).dropna().reset_index(drop=True)
    return _cache.get(cache_key)


def _load_india_spread(key: str) -> pd.DataFrame | None:
    cache_key = f"india_spr::{key}"
    if cache_key in _cache:
        return _cache[cache_key]
    from gsheet_data import fetch_india_data
    result = fetch_india_data()
    if result is None:
        return None
    _, _, usd_spr, _ = result
    for k, df in usd_spr.items():
        ck = f"india_spr::{k}"
        if ck not in _cache:
            _cache[ck] = df[["date", "spread_value"]].rename(columns={"spread_value": "value"}).dropna().reset_index(drop=True)
    return _cache.get(cache_key)


def load_series(instrument_key: str) -> pd.DataFrame | None:
    """
    Universal loader. instrument_key format: "type::id"
    Returns DataFrame with columns [date, value].
    """
    kind, ident = instrument_key.split("::", 1)
    if kind == "outright":
        return _load_outright(ident)
    elif kind == "spread":
        return _load_spread(ident)
    elif kind == "india_out":
        return _load_india_outright(ident)
    elif kind == "india_spr":
        return _load_india_spread(ident)
    return None


# ── Section 1: Seasonal Pattern vs Current ───────────────────────────────────

def compute_seasonal(df: pd.DataFrame) -> dict:
    df = df.copy()
    df["month"] = df["date"].dt.month
    monthly_avg = df.groupby("month")["value"].mean()
    current_month = df["date"].max().month
    current_val = df.iloc[-1]["value"]
    seasonal_avg = monthly_avg.get(current_month, np.nan)
    pct_dev = ((current_val - seasonal_avg) / seasonal_avg * 100) if seasonal_avg and seasonal_avg != 0 else np.nan

    month_names = {1: "Jan", 2: "Feb", 3: "Mar", 4: "Apr", 5: "May", 6: "Jun",
                   7: "Jul", 8: "Aug", 9: "Sep", 10: "Oct", 11: "Nov", 12: "Dec"}
    monthly_table = {month_names.get(m, str(m)): round(v, 2) for m, v in monthly_avg.items()}

    return {
        "monthly_averages": monthly_table,
        "current_month": month_names.get(current_month, str(current_month)),
        "current_value": round(current_val, 2),
        "seasonal_average": round(seasonal_avg, 2) if pd.notna(seasonal_avg) else None,
        "pct_deviation": round(pct_dev, 2) if pd.notna(pct_dev) else None,
    }


# ── Section 2: Extreme Values ────────────────────────────────────────────────

def compute_extremes(df: pd.DataFrame) -> dict:
    values = df["value"].values
    current = values[-1]
    mean = np.nanmean(values)
    std = np.nanstd(values)
    z_score = (current - mean) / std if std > 0 else 0.0
    percentile = float(np.nanmean(values <= current) * 100)
    in_top_10 = percentile >= 90
    in_bottom_10 = percentile <= 10

    return {
        "current_value": round(float(current), 2),
        "mean": round(float(mean), 2),
        "std": round(float(std), 2),
        "z_score": round(z_score, 2),
        "percentile_rank": round(percentile, 1),
        "in_top_10_pct": in_top_10,
        "in_bottom_10_pct": in_bottom_10,
    }


# ── Section 3: Highs and Lows ────────────────────────────────────────────────

def compute_highs_lows(df: pd.DataFrame) -> dict:
    current = df.iloc[-1]["value"]
    high_idx = df["value"].idxmax()
    low_idx = df["value"].idxmin()
    high_val = df.loc[high_idx, "value"]
    low_val = df.loc[low_idx, "value"]
    high_date = df.loc[high_idx, "date"]
    low_date = df.loc[low_idx, "date"]

    pct_from_high = (current - high_val) / high_val * 100 if high_val != 0 else 0
    pct_from_low = (current - low_val) / low_val * 100 if low_val != 0 else 0

    rolling_high = df["value"].rolling(20, min_periods=1).max().iloc[-1]
    rolling_low = df["value"].rolling(20, min_periods=1).min().iloc[-1]

    return {
        "current_value": round(float(current), 2),
        "period_high": round(float(high_val), 2),
        "period_high_date": high_date.strftime("%Y-%m-%d"),
        "period_low": round(float(low_val), 2),
        "period_low_date": low_date.strftime("%Y-%m-%d"),
        "pct_from_high": round(float(pct_from_high), 2),
        "pct_from_low": round(float(pct_from_low), 2),
        "rolling_20d_high": round(float(rolling_high), 2),
        "rolling_20d_low": round(float(rolling_low), 2),
    }


# ── Section 4: OI Buildup ────────────────────────────────────────────────────

def compute_oi(ric: str) -> dict | None:
    """OI analysis for futures RICs only. Returns None if not available."""
    oi_analysis_path = PROC_DIR / "oi_analysis.csv"
    oi_latest_path = PROC_DIR / "oi_latest.csv"
    oi_summary_path = PROC_DIR / "oi_summary.csv"

    if not oi_analysis_path.exists() or not oi_latest_path.exists():
        return None

    latest_df = pd.read_csv(oi_latest_path)
    latest_row = latest_df[latest_df["RIC"] == ric]
    if latest_row.empty:
        return None

    r = latest_row.iloc[0]
    result = {
        "latest_signal": r.get("signal", "N/A"),
        "latest_date": r.get("date", "N/A"),
        "latest_price": round(float(r["price"]), 2) if pd.notna(r.get("price")) else None,
        "latest_oi": int(r["oi"]) if pd.notna(r.get("oi")) else None,
        "latest_volume": int(r["volume"]) if pd.notna(r.get("volume")) else None,
    }

    # 15D/30D distribution
    if oi_summary_path.exists():
        summary_df = pd.read_csv(oi_summary_path)
        summary_row = summary_df[summary_df["RIC"] == ric]
        if not summary_row.empty:
            s = summary_row.iloc[0]
            result["signal_15d"] = {
                "Long Buildup": int(s.get("LB_15d", 0)),
                "Short Buildup": int(s.get("SB_15d", 0)),
                "Short Covering": int(s.get("SC_15d", 0)),
                "Long Unwinding": int(s.get("LU_15d", 0)),
                "Neutral": int(s.get("N_15d", 0)),
            }
            result["signal_30d"] = {
                "Long Buildup": int(s.get("LB_30d", 0)),
                "Short Buildup": int(s.get("SB_30d", 0)),
                "Short Covering": int(s.get("SC_30d", 0)),
                "Long Unwinding": int(s.get("LU_30d", 0)),
                "Neutral": int(s.get("N_30d", 0)),
            }

    # Last 5 signal days
    analysis_df = pd.read_csv(oi_analysis_path, parse_dates=["date"])
    ric_data = analysis_df[analysis_df["RIC"] == ric].sort_values("date").tail(5)
    result["recent_signals"] = [
        {"date": row["date"].strftime("%Y-%m-%d"), "signal": row["signal"],
         "price_chg_pct": round(row["price_chg_pct"], 2) if pd.notna(row["price_chg_pct"]) else None,
         "oi_chg_pct": round(row["oi_chg_pct"], 2) if pd.notna(row["oi_chg_pct"]) else None}
        for _, row in ric_data.iterrows()
    ]

    return result


# ── Section 5: Technical Support & Resistance ────────────────────────────────

def compute_support_resistance(df: pd.DataFrame) -> dict:
    """KMeans clustering approach matching dashboard.py TA tab."""
    values = df["value"].values
    current = values[-1]

    def _local_extrema(data, window=30, step=5):
        mins, maxs = [], []
        for i in range(0, len(data) - window + 1, step):
            w = data[i:i + window]
            mins.append(np.min(w))
            maxs.append(np.max(w))
        return np.array(mins).reshape(-1, 1), np.array(maxs).reshape(-1, 1)

    def _optimal_k(data, max_k=6):
        if len(data) < 2:
            return min(3, len(data))
        wcss = []
        max_k = min(max_k, len(data))
        for k in range(1, max_k + 1):
            km = KMeans(n_clusters=k, random_state=0, n_init=10)
            km.fit(data)
            wcss.append(km.inertia_)
        kn = KneeLocator(range(1, max_k + 1), wcss, curve="convex", direction="decreasing")
        return kn.elbow if kn.elbow else 3

    if len(values) < 30:
        return {"levels": [], "current_value": round(float(current), 2),
                "nearest_support": None, "nearest_resistance": None,
                "support_distance_pct": None, "resistance_distance_pct": None}

    local_mins, local_maxs = _local_extrema(values)

    k_min = _optimal_k(local_mins)
    k_max = _optimal_k(local_maxs)

    centers_min = KMeans(n_clusters=k_min, random_state=0, n_init=10).fit(local_mins).cluster_centers_.flatten()
    centers_max = KMeans(n_clusters=k_max, random_state=0, n_init=10).fit(local_maxs).cluster_centers_.flatten()

    all_levels = sorted(np.concatenate([centers_min, centers_max]))

    support_levels = [l for l in all_levels if l < current][-5:]
    resistance_levels = [l for l in all_levels if l > current][:5]

    nearest_support = max(support_levels) if support_levels else None
    nearest_resistance = min(resistance_levels) if resistance_levels else None

    sup_dist = ((current - nearest_support) / nearest_support * 100) if nearest_support else None
    res_dist = ((nearest_resistance - current) / current * 100) if nearest_resistance else None

    return {
        "levels": [round(float(l), 2) for l in all_levels],
        "support_levels": [round(float(l), 2) for l in support_levels],
        "resistance_levels": [round(float(l), 2) for l in resistance_levels],
        "current_value": round(float(current), 2),
        "nearest_support": round(float(nearest_support), 2) if nearest_support else None,
        "nearest_resistance": round(float(nearest_resistance), 2) if nearest_resistance else None,
        "support_distance_pct": round(float(sup_dist), 2) if sup_dist is not None else None,
        "resistance_distance_pct": round(float(res_dist), 2) if res_dist is not None else None,
    }


# ── Section 6: Year Resemblance ──────────────────────────────────────────────

def compute_year_resemblance(full_df: pd.DataFrame) -> dict:
    """Correlate current YTD path with each prior year's path."""
    df = full_df.copy()
    df["year"] = df["date"].dt.year
    df["doy"] = df["date"].dt.dayofyear

    current_year = df["year"].max()
    current_ytd = df[df["year"] == current_year].set_index("doy")["value"]

    if len(current_ytd) < 10:
        return {"top_matches": [], "current_year": current_year}

    years = sorted(df["year"].unique())
    correlations = []
    for yr in years:
        if yr == current_year:
            continue
        yr_data = df[df["year"] == yr].set_index("doy")["value"]
        # Align on common day-of-year
        common = current_ytd.index.intersection(yr_data.index)
        if len(common) < 10:
            continue
        corr = current_ytd.loc[common].corr(yr_data.loc[common])
        if pd.notna(corr):
            correlations.append({"year": yr, "correlation": round(float(corr), 4)})

    correlations.sort(key=lambda x: abs(x["correlation"]), reverse=True)
    return {
        "top_matches": correlations[:3],
        "current_year": current_year,
        "ytd_days": len(current_ytd),
    }


# ── Section 7: Momentum & Trend ─────────────────────────────────────────────

def compute_momentum(df: pd.DataFrame) -> dict:
    values = df["value"]
    current = values.iloc[-1]

    roc_20 = ((current - values.iloc[-21]) / values.iloc[-21] * 100) if len(values) > 20 else None
    roc_60 = ((current - values.iloc[-61]) / values.iloc[-61] * 100) if len(values) > 60 else None

    sma_20 = values.rolling(20).mean().iloc[-1] if len(values) >= 20 else None
    sma_60 = values.rolling(60).mean().iloc[-1] if len(values) >= 60 else None

    vs_sma_20 = ((current - sma_20) / sma_20 * 100) if sma_20 else None
    vs_sma_60 = ((current - sma_60) / sma_60 * 100) if sma_60 else None

    if sma_20 is not None and sma_60 is not None:
        if sma_20 > sma_60 and current > sma_20:
            trend = "Uptrend"
        elif sma_20 < sma_60 and current < sma_20:
            trend = "Downtrend"
        else:
            trend = "Sideways"
    else:
        trend = "Insufficient data"

    return {
        "current_value": round(float(current), 2),
        "roc_20d_pct": round(float(roc_20), 2) if roc_20 is not None else None,
        "roc_60d_pct": round(float(roc_60), 2) if roc_60 is not None else None,
        "sma_20": round(float(sma_20), 2) if sma_20 is not None else None,
        "sma_60": round(float(sma_60), 2) if sma_60 is not None else None,
        "vs_sma_20_pct": round(float(vs_sma_20), 2) if vs_sma_20 is not None else None,
        "vs_sma_60_pct": round(float(vs_sma_60), 2) if vs_sma_60 is not None else None,
        "trend": trend,
    }


# ── Master runner ────────────────────────────────────────────────────────────

def run_analysis(instrument_key: str, timeframe: str) -> dict:
    """
    Run all 7 sections. Returns dict with keys:
    instrument, timeframe, sections (dict of 7), raw_text, narrative, error
    """
    full_df = load_series(instrument_key)
    if full_df is None or full_df.empty:
        return {"error": "Data not available for this instrument."}

    # Apply timeframe window
    n_days = TIMEFRAMES.get(timeframe)
    if n_days is not None and len(full_df) > n_days:
        window_df = full_df.tail(n_days).reset_index(drop=True)
    else:
        window_df = full_df.copy()

    # Extract RIC for OI lookup (only for outright futures)
    kind, ident = instrument_key.split("::", 1)
    ric_for_oi = ident if kind == "outright" else None

    sections = {}

    # 1. Seasonal (uses full data for averages, current from latest)
    sections["seasonal"] = compute_seasonal(full_df)

    # 2. Extreme values (within selected timeframe)
    sections["extremes"] = compute_extremes(window_df)

    # 3. Highs and lows
    sections["highs_lows"] = compute_highs_lows(window_df)

    # 4. OI Buildup
    if ric_for_oi:
        oi = compute_oi(ric_for_oi)
        if oi:
            sections["oi"] = oi
        else:
            sections["oi"] = {"note": "OI data not available for this instrument."}
    else:
        sections["oi"] = {"note": "OI data not available for this instrument."}

    # 5. Support & Resistance
    sections["support_resistance"] = compute_support_resistance(window_df)

    # 6. Year Resemblance (uses full data)
    sections["year_resemblance"] = compute_year_resemblance(full_df)

    # 7. Momentum
    sections["momentum"] = compute_momentum(window_df)

    # Build raw text for Gemini prompt
    raw_text = _format_raw_stats(instrument_key, timeframe, sections)

    # Call Gemini
    narrative = _call_gemini(instrument_key, timeframe, raw_text)

    # Build plotly figures
    figures = build_figures(full_df, window_df, sections, instrument_key)

    return {
        "instrument": instrument_key,
        "timeframe": timeframe,
        "sections": sections,
        "figures": figures,
        "raw_text": raw_text,
        "narrative": narrative,
        "error": None,
    }


# ── Chart builders ───────────────────────────────────────────────────────────

import plotly.graph_objects as go

_CHART_LAYOUT = dict(
    plot_bgcolor="white",
    paper_bgcolor="white",
    margin=dict(l=50, r=30, t=40, b=40),
    hovermode="x unified",
    font=dict(family="Inter, Segoe UI, Arial, sans-serif", size=11),
    xaxis=dict(showgrid=True, gridcolor="#e8e8e8"),
    yaxis=dict(showgrid=True, gridcolor="#e8e8e8"),
)


def _fig_seasonal(sections: dict, full_df: pd.DataFrame) -> go.Figure:
    """Bar chart of monthly averages with current month highlighted."""
    s = sections.get("seasonal", {})
    avgs = s.get("monthly_averages", {})
    cur_month = s.get("current_month")
    cur_val = s.get("current_value")

    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    vals = [avgs.get(m, 0) for m in months]
    colors = ["#1a1a2e" if m == cur_month else "#b0c4de" for m in months]

    fig = go.Figure()
    fig.add_trace(go.Bar(x=months, y=vals, marker_color=colors, name="Avg"))
    if cur_val is not None and cur_month:
        fig.add_trace(go.Scatter(
            x=[cur_month], y=[cur_val], mode="markers+text",
            marker=dict(color="#d62728", size=12, symbol="diamond"),
            text=[f"{cur_val:,.1f}"], textposition="top center",
            name="Current",
        ))
    dev = s.get("pct_deviation")
    title = "Seasonal Pattern vs Current"
    if dev is not None:
        title += f" ({dev:+.1f}% vs avg)"
    fig.update_layout(**_CHART_LAYOUT, title=dict(text=title, font=dict(size=13)), height=320)
    return fig


def _fig_extremes(sections: dict, window_df: pd.DataFrame) -> go.Figure:
    """Distribution histogram with current value marker."""
    e = sections.get("extremes", {})
    fig = go.Figure()
    fig.add_trace(go.Histogram(
        x=window_df["value"], nbinsx=40, marker_color="#b0c4de",
        opacity=0.7, name="Distribution",
    ))
    cur = e.get("current_value")
    if cur is not None:
        fig.add_vline(x=cur, line_width=2, line_dash="dash", line_color="#d62728",
                      annotation_text=f"Current: {cur:,.2f}")
    z = e.get("z_score", 0)
    pct = e.get("percentile_rank", 0)
    fig.update_layout(**_CHART_LAYOUT, height=320,
                      title=dict(text=f"Value Distribution (Z={z:+.2f}, P{pct:.0f})", font=dict(size=13)),
                      yaxis_title="Frequency")
    return fig


def _fig_highs_lows(sections: dict, window_df: pd.DataFrame) -> go.Figure:
    """Price line with rolling 20D high/low bands."""
    fig = go.Figure()
    r20_high = window_df["value"].rolling(20, min_periods=1).max()
    r20_low = window_df["value"].rolling(20, min_periods=1).min()

    fig.add_trace(go.Scatter(
        x=window_df["date"], y=r20_high, mode="lines",
        line=dict(color="rgba(44,160,44,0.3)", width=1), name="20D High",
        fill=None,
    ))
    fig.add_trace(go.Scatter(
        x=window_df["date"], y=r20_low, mode="lines",
        line=dict(color="rgba(214,39,40,0.3)", width=1), name="20D Low",
        fill="tonexty", fillcolor="rgba(200,200,200,0.15)",
    ))
    fig.add_trace(go.Scatter(
        x=window_df["date"], y=window_df["value"], mode="lines",
        line=dict(color="#1a1a2e", width=2), name="Price",
    ))

    h = sections.get("highs_lows", {})
    title = f"Highs & Lows ({h.get('pct_from_high', 0):+.1f}% from high, {h.get('pct_from_low', 0):+.1f}% from low)"
    fig.update_layout(**_CHART_LAYOUT, height=320,
                      title=dict(text=title, font=dict(size=13)))
    return fig


def _fig_oi(sections: dict) -> go.Figure | None:
    """Stacked bar of 15D/30D OI signal distribution."""
    oi = sections.get("oi", {})
    if "note" in oi:
        return None

    sig_colors = {
        "Long Buildup":   "#2ca02c",
        "Short Buildup":  "#d62728",
        "Short Covering": "#f0c030",
        "Long Unwinding": "#ff7f0e",
        "Neutral":        "#9e9e9e",
    }

    fig = go.Figure()
    categories = ["15D", "30D"]
    for sig_name, color in sig_colors.items():
        vals = [
            oi.get("signal_15d", {}).get(sig_name, 0),
            oi.get("signal_30d", {}).get(sig_name, 0),
        ]
        fig.add_trace(go.Bar(
            x=categories, y=vals, name=sig_name, marker_color=color,
        ))

    latest = oi.get("latest_signal", "N/A")
    fig.update_layout(**_CHART_LAYOUT, barmode="stack", height=320,
                      title=dict(text=f"OI Signal Distribution (Latest: {latest})", font=dict(size=13)),
                      yaxis_title="Days")
    return fig


def _fig_support_resistance(sections: dict, window_df: pd.DataFrame) -> go.Figure:
    """Price line with horizontal S/R levels."""
    sr = sections.get("support_resistance", {})
    fig = go.Figure()

    fig.add_trace(go.Scatter(
        x=window_df["date"], y=window_df["value"], mode="lines",
        line=dict(color="#1a1a2e", width=2), name="Price",
    ))

    for lvl in sr.get("support_levels", []):
        fig.add_hline(y=lvl, line_width=1, line_dash="dash", line_color="#2ca02c",
                      annotation_text=f"S {lvl:,.1f}", annotation_position="left")
    for lvl in sr.get("resistance_levels", []):
        fig.add_hline(y=lvl, line_width=1, line_dash="dash", line_color="#d62728",
                      annotation_text=f"R {lvl:,.1f}", annotation_position="left")

    sdist = sr.get("support_distance_pct")
    rdist = sr.get("resistance_distance_pct")
    parts = []
    if sdist is not None:
        parts.append(f"S: {sdist:.1f}% below")
    if rdist is not None:
        parts.append(f"R: {rdist:.1f}% above")
    subtitle = " | ".join(parts) if parts else ""
    fig.update_layout(**_CHART_LAYOUT, height=320,
                      title=dict(text=f"Support & Resistance {subtitle}", font=dict(size=13)))
    return fig


def _fig_year_resemblance(sections: dict, full_df: pd.DataFrame) -> go.Figure:
    """Overlay current YTD vs top 3 similar years."""
    yr = sections.get("year_resemblance", {})
    current_year = yr.get("current_year")
    top = yr.get("top_matches", [])

    df = full_df.copy()
    df["year"] = df["date"].dt.year
    df["doy"] = df["date"].dt.dayofyear

    fig = go.Figure()

    # Plot top similar years first (thinner, lighter)
    sim_colors = ["#ff7f0e", "#2ca02c", "#9467bd"]
    for i, match in enumerate(top):
        ydata = df[df["year"] == match["year"]].sort_values("doy")
        if ydata.empty:
            continue
        # Normalize to start of year
        start_val = ydata.iloc[0]["value"]
        if start_val != 0:
            norm = (ydata["value"] / start_val - 1) * 100
        else:
            norm = ydata["value"]
        fig.add_trace(go.Scatter(
            x=ydata["doy"], y=norm, mode="lines",
            line=dict(color=sim_colors[i % len(sim_colors)], width=1.5, dash="dot"),
            name=f'{match["year"]} (r={match["correlation"]:.3f})',
            opacity=0.7,
        ))

    # Current year on top
    cur_data = df[df["year"] == current_year].sort_values("doy")
    if not cur_data.empty:
        start_val = cur_data.iloc[0]["value"]
        if start_val != 0:
            norm = (cur_data["value"] / start_val - 1) * 100
        else:
            norm = cur_data["value"]
        fig.add_trace(go.Scatter(
            x=cur_data["doy"], y=norm, mode="lines",
            line=dict(color="#1a1a2e", width=2.5),
            name=str(current_year),
        ))

    fig.update_layout(**_CHART_LAYOUT, height=320,
                      title=dict(text="Year Resemblance (Normalized % from Jan 1)", font=dict(size=13)),
                      xaxis_title="Day of Year", yaxis_title="% Change from Jan 1")
    return fig


def _fig_momentum(sections: dict, window_df: pd.DataFrame) -> go.Figure:
    """Price line with SMA20 and SMA60."""
    mo = sections.get("momentum", {})
    df = window_df.copy()
    df["sma20"] = df["value"].rolling(20).mean()
    df["sma60"] = df["value"].rolling(60).mean()

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df["date"], y=df["value"], mode="lines",
        line=dict(color="#1a1a2e", width=2), name="Price",
    ))
    fig.add_trace(go.Scatter(
        x=df["date"], y=df["sma20"], mode="lines",
        line=dict(color="#ff7f0e", width=1.5), name="SMA 20",
    ))
    fig.add_trace(go.Scatter(
        x=df["date"], y=df["sma60"], mode="lines",
        line=dict(color="#2ca02c", width=1.5), name="SMA 60",
    ))

    trend = mo.get("trend", "")
    roc20 = mo.get("roc_20d_pct")
    roc60 = mo.get("roc_60d_pct")
    parts = [trend]
    if roc20 is not None:
        parts.append(f"20D ROC: {roc20:+.1f}%")
    if roc60 is not None:
        parts.append(f"60D ROC: {roc60:+.1f}%")
    fig.update_layout(**_CHART_LAYOUT, height=320,
                      title=dict(text=f"Momentum & Trend — {' | '.join(parts)}", font=dict(size=13)))
    return fig


def build_figures(full_df: pd.DataFrame, window_df: pd.DataFrame,
                  sections: dict, instrument_key: str) -> dict:
    """Build all 7 plotly figures. Returns {name: go.Figure}."""
    figs = {}
    figs["seasonal"] = _fig_seasonal(sections, full_df)
    figs["extremes"] = _fig_extremes(sections, window_df)
    figs["highs_lows"] = _fig_highs_lows(sections, window_df)
    oi_fig = _fig_oi(sections)
    if oi_fig:
        figs["oi"] = oi_fig
    figs["support_resistance"] = _fig_support_resistance(sections, window_df)
    figs["year_resemblance"] = _fig_year_resemblance(sections, full_df)
    figs["momentum"] = _fig_momentum(sections, window_df)
    return figs


# ── Format raw stats for LLM ────────────────────────────────────────────────

def _format_raw_stats(instrument_key: str, timeframe: str, sections: dict) -> str:
    lines = [f"Instrument: {instrument_key}", f"Timeframe: {timeframe}", ""]

    s = sections.get("seasonal", {})
    lines.append("## 1. SEASONAL PATTERN")
    lines.append(f"Monthly averages: {s.get('monthly_averages', {})}")
    lines.append(f"Current month: {s.get('current_month')} | Value: {s.get('current_value')} | Seasonal avg: {s.get('seasonal_average')} | Deviation: {s.get('pct_deviation')}%")
    lines.append("")

    e = sections.get("extremes", {})
    lines.append("## 2. EXTREME VALUES")
    lines.append(f"Current: {e.get('current_value')} | Mean: {e.get('mean')} | Std: {e.get('std')}")
    lines.append(f"Z-score: {e.get('z_score')} | Percentile: {e.get('percentile_rank')}%")
    lines.append(f"In top 10%: {e.get('in_top_10_pct')} | In bottom 10%: {e.get('in_bottom_10_pct')}")
    lines.append("")

    h = sections.get("highs_lows", {})
    lines.append("## 3. HIGHS AND LOWS")
    lines.append(f"Period high: {h.get('period_high')} ({h.get('period_high_date')}) | Low: {h.get('period_low')} ({h.get('period_low_date')})")
    lines.append(f"Current vs high: {h.get('pct_from_high')}% | vs low: {h.get('pct_from_low')}%")
    lines.append(f"20D rolling high: {h.get('rolling_20d_high')} | low: {h.get('rolling_20d_low')}")
    lines.append("")

    oi = sections.get("oi", {})
    lines.append("## 4. OI BUILDUP")
    if "note" in oi:
        lines.append(oi["note"])
    else:
        lines.append(f"Latest signal: {oi.get('latest_signal')} ({oi.get('latest_date')})")
        lines.append(f"Price: {oi.get('latest_price')} | OI: {oi.get('latest_oi'):,}" if oi.get('latest_oi') else "")
        if "signal_15d" in oi:
            lines.append(f"15D distribution: {oi['signal_15d']}")
        if "signal_30d" in oi:
            lines.append(f"30D distribution: {oi['signal_30d']}")
        if "recent_signals" in oi:
            lines.append("Recent 5 signals:")
            for rs in oi["recent_signals"]:
                lines.append(f"  {rs['date']}: {rs['signal']} (price {rs['price_chg_pct']}%, OI {rs['oi_chg_pct']}%)")
    lines.append("")

    sr = sections.get("support_resistance", {})
    lines.append("## 5. SUPPORT & RESISTANCE")
    lines.append(f"Current: {sr.get('current_value')}")
    lines.append(f"Support levels: {sr.get('support_levels', [])}")
    lines.append(f"Resistance levels: {sr.get('resistance_levels', [])}")
    lines.append(f"Nearest support: {sr.get('nearest_support')} ({sr.get('support_distance_pct')}% away)")
    lines.append(f"Nearest resistance: {sr.get('nearest_resistance')} ({sr.get('resistance_distance_pct')}% away)")
    lines.append("")

    yr = sections.get("year_resemblance", {})
    lines.append("## 6. YEAR RESEMBLANCE")
    lines.append(f"Current year: {yr.get('current_year')} ({yr.get('ytd_days')} trading days YTD)")
    for m in yr.get("top_matches", []):
        lines.append(f"  {m['year']}: correlation = {m['correlation']}")
    lines.append("")

    mo = sections.get("momentum", {})
    lines.append("## 7. MOMENTUM & TREND")
    lines.append(f"Current: {mo.get('current_value')} | Trend: {mo.get('trend')}")
    lines.append(f"20D ROC: {mo.get('roc_20d_pct')}% | 60D ROC: {mo.get('roc_60d_pct')}%")
    lines.append(f"SMA20: {mo.get('sma_20')} ({mo.get('vs_sma_20_pct')}% vs current) | SMA60: {mo.get('sma_60')} ({mo.get('vs_sma_60_pct')}% vs current)")

    return "\n".join(lines)


# ── Gemini LLM call ──────────────────────────────────────────────────────────

def _get_gemini_key() -> str | None:
    if not CRED_FILE.exists():
        return None
    cfg = configparser.ConfigParser()
    cfg.read(str(CRED_FILE))
    return cfg.get("gemini", "api_key", fallback=None)


def _call_gemini(instrument_key: str, timeframe: str, raw_stats: str) -> str | None:
    """Call Gemini API for narrative. Returns markdown string or None on failure."""
    api_key = _get_gemini_key()
    if not api_key or api_key == "YOUR_GEMINI_API_KEY_HERE":
        logger.warning("Gemini API key not configured.")
        return None

    try:
        import google.generativeai as genai
    except ImportError:
        logger.warning("google-generativeai package not installed.")
        return None

    try:
        genai.configure(api_key=api_key)

        model = genai.GenerativeModel("gemini-2.0-flash")

        prompt = f"""You are a commodity markets analyst. Be concise, specific, and actionable.
Use precise numbers from the data provided. Do not invent data not present in the statistics.

Analyse the following instrument statistics and produce a structured report with
clearly labelled sections matching each of the 7 sections below.
End with a "Key Takeaways" section containing 3-5 bullet points summarising
the most important signals for a trader.

Instrument: {instrument_key}
Timeframe: {timeframe}

--- COMPUTED STATISTICS ---
{raw_stats}
--- END ---

Format the output as clean markdown with ## headings for each section."""

        response = model.generate_content(prompt)
        return response.text

    except Exception as exc:
        logger.warning("Gemini API call failed: %s", exc)
        return None
