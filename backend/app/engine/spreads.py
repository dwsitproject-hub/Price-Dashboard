"""Derived-series computation — port of legacy process.py calc_spreads (+ dashboard cash M1–M2 and
outright series). Pure functions: frames in, {series_key: DataFrame[value, value_local]} out.
"""
from __future__ import annotations

import pandas as pd

from app.catalog.instruments import CPO_FOB_DUTY_MULT, CPO_FOB_PREM_USD
from app.catalog.registry import SERIES, SeriesDef
from app.engine.conversions import to_usd, usd_to_local

_EMPTY = pd.Series(dtype=float)


def _col(frame: pd.DataFrame, ric: str) -> pd.Series:
    return frame[ric] if ric in frame.columns else _EMPTY


def _add_synthetic_fob(usd: pd.DataFrame) -> pd.DataFrame:
    """Synthetic FOB Malaysia CPO (USD/MT) = (Local Malaysia CPO + 10) × 1.10 (process.py §1)."""
    usd = usd.copy()
    for m in ("M1", "M2"):
        loc = f"CPO-MYSTH-{m}"
        if loc in usd.columns:
            usd[f"CPO-MYFOB-{m}"] = (usd[loc] + CPO_FOB_PREM_USD) * CPO_FOB_DUTY_MULT
    return usd


def _clean(s: pd.Series) -> pd.Series:
    s = s.dropna()
    return s[s.index.notna()]


def _compute_one(sd: SeriesDef, usd: pd.DataFrame, raw_ffill: pd.DataFrame, raw_wide: pd.DataFrame,
                 fx_wide: pd.DataFrame) -> pd.DataFrame | None:
    d = sd.definition
    op = d.get("op")
    local: pd.Series | None = None

    if op == "diff":
        value = _col(usd, d["a"]) - _col(usd, d["b"])
        value = _clean(value)
        if sd.kind == "calendar" and sd.local_unit:
            local = usd_to_local(value, d["a"], fx_wide)
    elif op == "value":
        value = _clean(_col(usd, d["a"]))
    elif op == "crush":
        c = d["contract"]
        # Crush = SM($/short ton)·0.022 + BO(¢/lb)·0.11 − S(¢/bu)/100, raw local units (process.py §7)
        value = _clean(_col(raw_ffill, f"SM{c}") * 0.022 + _col(raw_ffill, f"BO{c}") * 0.11
                       - _col(raw_ffill, f"S{c}") / 100)
    elif op == "sa_crush":
        value = _clean((_col(usd, "SM-ARGUPR-P1") * 0.790 + _col(usd, "BO-ARGUPR-C1") * 0.185
                        - _col(usd, "S-ARGUPR-C1")) * 0.0272155)
    elif op == "oilshare":
        c = d["contract"]
        oil = _col(raw_ffill, f"BO{c}") * 0.11
        meal = _col(raw_ffill, f"SM{c}") * 0.022
        value = _clean(oil / (oil + meal) * 100)
    elif op == "sa_oilshare":
        oil = _col(usd, "BO-ARGUPR-C1") * 0.185
        meal = _col(usd, "SM-ARGUPR-P1") * 0.790
        value = _clean(oil / (oil + meal) * 100)
    elif op == "outright":
        ric = d["a"]
        if ric.startswith("CPO-MYFOB-"):
            value = _clean(_col(usd, ric))          # synthetic, already USD/MT (legacy uses the CSV)
        else:
            raw = _clean(_col(raw_wide, ric))       # legacy outrights: raw quotes, no ffill
            value = _clean(to_usd(raw, ric, fx_wide))
            if sd.local_unit:
                local = raw.reindex(value.index)
    else:
        raise ValueError(f"unknown op {op!r} for {sd.key}")

    if value.empty:
        return None
    df = pd.DataFrame({"value": value})
    df["value_local"] = local.reindex(df.index) if local is not None else None
    df.index.name = "obs_date"
    return df


def compute_all(frames: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    usd = _add_synthetic_fob(frames["usd"])
    raw_ffill = frames["raw_ffill"]
    raw_wide = frames["raw_wide"]
    fx_cols = [c for c in raw_wide.columns if c.endswith("=")]
    fx_wide = raw_wide[fx_cols].ffill()

    out: dict[str, pd.DataFrame] = {}
    for sd in SERIES:
        df = _compute_one(sd, usd, raw_ffill, raw_wide, fx_wide)
        if df is not None:
            out[sd.key] = df
    return out
