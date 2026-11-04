"""Price resolution and unit conversion — port of legacy process.py load_prices/to_wide/convert_currencies.

Order of operations (unchanged from legacy):
  resolve price → pivot wide → ffill FX → convert to USD/MT → ffill everything → spreads.
"""
from __future__ import annotations

import pandas as pd

from app.catalog.instruments import FX_RICS, instrument_conversion


def resolve_price(df: pd.DataFrame) -> pd.Series:
    """FX → mid (Bid+Ask)/2; everything else → Last, fallback Settlement Price."""
    last = pd.to_numeric(df["last_price"], errors="coerce")
    sett = pd.to_numeric(df["settlement_price"], errors="coerce")
    bid = pd.to_numeric(df["bid"], errors="coerce")
    ask = pd.to_numeric(df["ask"], errors="coerce")
    is_fx = df["ric"].isin(FX_RICS)
    return (bid + ask).div(2).where(is_fx, last.fillna(sett))


def to_wide(long_df: pd.DataFrame) -> pd.DataFrame:
    """long (date, ric, price) → wide (date × ric); FX forward-filled across non-quote days."""
    wide = (long_df.drop_duplicates(subset=["date", "ric"])
            .pivot(index="date", columns="ric", values="price").sort_index())
    wide.columns.name = None
    for ric in FX_RICS:
        if ric in wide.columns:
            wide[ric] = wide[ric].ffill()
    return wide


def convert_frame(wide: pd.DataFrame) -> pd.DataFrame:
    """Convert every native-unit column to USD/MT (same-row FX, as legacy convert_currencies)."""
    out = wide.copy()
    for ric in out.columns:
        ctype, factor, fx_ric, _ = instrument_conversion(ric)
        if ctype == "factor" and factor:
            out[ric] = out[ric] * factor
        elif ctype == "fx_div" and fx_ric:
            if fx_ric in wide.columns:
                out[ric] = out[ric] / wide[fx_ric]
            else:
                out[ric] = float("nan")
    return out


def to_usd(series: pd.Series, ric: str, fx_wide: pd.DataFrame) -> pd.Series:
    """Convert one native series to USD/MT using an FX frame aligned by date (ffill/bfill)."""
    ctype, factor, fx_ric, _ = instrument_conversion(ric)
    if ctype == "factor" and factor:
        return series * factor
    if ctype == "fx_div" and fx_ric:
        if fx_ric not in fx_wide.columns:
            return series * float("nan")
        rate = fx_wide[fx_ric].reindex(series.index.union(fx_wide.index)).ffill().bfill().reindex(series.index)
        return series / rate
    return series


def usd_to_local(series: pd.Series, ric_or_product_ric: str, fx_wide: pd.DataFrame) -> pd.Series:
    """Inverse conversion (legacy SPREADS_LOCAL): USD/MT → native unit."""
    ctype, factor, fx_ric, _ = instrument_conversion(ric_or_product_ric)
    if ctype == "factor" and factor:
        return series / factor
    if ctype == "fx_div" and fx_ric:
        if fx_ric not in fx_wide.columns:
            return series * float("nan")
        rate = fx_wide[fx_ric].reindex(series.index.union(fx_wide.index)).ffill().bfill().reindex(series.index)
        return series * rate
    return series


def build_frames(long_df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Return the three frames the engine needs.

    raw_wide : native prices, FX ffilled only (no other ffill) — outrights.
    usd      : converted to USD/MT then fully ffilled — spreads.
    raw_ffill: native prices fully ffilled — crush / oilshare (raw-unit formulas).
    """
    raw_wide = to_wide(long_df)
    usd = convert_frame(raw_wide).sort_index().ffill()
    raw_ffill = raw_wide.sort_index().ffill()
    return {"raw_wide": raw_wide, "usd": usd, "raw_ffill": raw_ffill}
