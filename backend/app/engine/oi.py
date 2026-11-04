"""OI/Volume cumulative tables — port of legacy scripts/oi_cumulative.py."""
from __future__ import annotations

import pandas as pd

from app.catalog.instruments import PRODUCTS, futures_ric

PRICE_FRONT_CONTRACTS = 5               # avg of c1..c5 (smooths prompt-month roll noise)
ARROW_THRESHOLD_START = "2023-01-01"    # trailing window for per-product arrow thresholds
OI_VOL_NOTABLE = 10.0                   # |OI/Vol %| > 10  → row highlighted
OI_VOL_ANOMALY = 100.0                  # |OI/Vol %| > 100 → likely vendor artefact, muted


def build_cumulative(long_df: pd.DataFrame) -> pd.DataFrame:
    """long_df columns: date, ric, price (native), oi, volume."""
    df = long_df.drop_duplicates(subset=["date", "ric"])
    price_w = df.pivot(index="date", columns="ric", values="price").sort_index()
    oi_w = df.pivot(index="date", columns="ric", values="oi").sort_index()
    vol_w = df.pivot(index="date", columns="ric", values="volume").sort_index()

    records = []
    for code, product in PRODUCTS.items():
        rics = [futures_ric(code, c) for c in range(1, product.oi_contracts + 1)]
        present = [r for r in rics if r in oi_w.columns]
        if not present:
            continue
        total_oi = oi_w[present].clip(lower=0).sum(axis=1, min_count=1)
        reporting = oi_w[present].notna().sum(axis=1)
        vol_rics = [r for r in present if r in vol_w.columns]
        total_vol = (vol_w[vol_rics].clip(lower=0).sum(axis=1, min_count=1)
                     if vol_rics else pd.Series(dtype=float))
        front = [r for r in rics[:PRICE_FRONT_CONTRACTS] if r in price_w.columns]
        avg_price = price_w[front].mean(axis=1, skipna=True) if front else pd.Series(dtype=float)

        p = pd.DataFrame({"total_oi": total_oi, "total_volume": total_vol, "avg_price": avg_price,
                          "contracts_reporting": reporting}).dropna(subset=["total_oi"]).sort_index()
        p["oi_chg"] = p["total_oi"].diff()
        p["volume_chg"] = p["total_volume"].diff()
        p["price_chg_pct"] = p["avg_price"].pct_change(fill_method=None) * 100
        p["oi_vol_pct"] = (p["oi_chg"] / p["total_volume"] * 100).where(p["total_volume"] > 0)
        p["product_code"] = code
        records.append(p.reset_index().rename(columns={"index": "date"}))

    if not records:
        return pd.DataFrame(columns=["date", "product_code", "total_oi", "oi_chg", "total_volume",
                                     "volume_chg", "oi_vol_pct", "avg_price", "price_chg_pct",
                                     "contracts_reporting"])
    out = pd.concat(records, ignore_index=True)
    return out[["date", "product_code", "total_oi", "oi_chg", "total_volume", "volume_chg",
                "oi_vol_pct", "avg_price", "price_chg_pct", "contracts_reporting"]]


def build_arrow_thresholds(cum: pd.DataFrame, start: str = ARROW_THRESHOLD_START) -> pd.DataFrame:
    start_ts = pd.Timestamp(start)
    rows = []
    for code, sub in cum.groupby("product_code"):
        recent = sub[pd.to_datetime(sub["date"]) >= start_ts]["price_chg_pct"].abs().dropna()
        if recent.empty:
            continue
        rows.append({"product_code": code, "median_pct": float(recent.median()),
                     "p75_pct": float(recent.quantile(0.75)), "n_obs": int(len(recent)),
                     "window_start": start_ts.date()})
    return pd.DataFrame(rows)


def arrow(pct: float | None, median: float, p75: float) -> tuple[str, int]:
    """(direction, magnitude 0–3): below median ×1, median–p75 ×2, above p75 ×3."""
    if pct is None or pd.isna(pct) or pct == 0:
        return "flat", 0
    a = abs(pct)
    n = 1 if a < median else (2 if a < p75 else 3)
    return ("up" if pct > 0 else "down"), n


def oi_vol_flag(pct: float | None) -> str:
    if pct is None or pd.isna(pct):
        return "none"
    if abs(pct) > OI_VOL_ANOMALY:
        return "anomaly"
    if abs(pct) > OI_VOL_NOTABLE:
        return "notable"
    return "normal"
