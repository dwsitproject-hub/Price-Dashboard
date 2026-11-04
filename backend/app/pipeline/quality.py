"""Data-quality checks per run (PRD DATA-09). Findings are informational — they never fail a run."""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.catalog.instruments import CASH, PRODUCTS, futures_ric
from app.engine.oi import OI_VOL_ANOMALY

STALE_DAYS = 7          # ≈ 5 trading days
JUMP_SIGMA = 8.0


def run_checks(prices: pd.DataFrame, oi_cum: pd.DataFrame) -> list[dict]:
    """prices: long (date, ric, price). Returns finding dicts."""
    findings: list[dict] = []
    if prices.empty:
        return [{"check_code": "no_data", "severity": "error", "ric": None, "obs_date": None, "detail": {}}]
    latest = prices["date"].max()
    watched = [futures_ric(p, c) for p in PRODUCTS for c in range(1, 4)] + list(CASH)
    last_by_ric = prices.groupby("ric")["date"].max()
    for ric in watched:
        last = last_by_ric.get(ric)
        if last is None:
            findings.append({"check_code": "missing_ric", "severity": "warn", "ric": ric, "obs_date": None,
                             "detail": {"message": "No data at all for this RIC."}})
        elif (latest - last).days > STALE_DAYS:
            findings.append({"check_code": "stale_ric", "severity": "warn", "ric": ric, "obs_date": last.date(),
                             "detail": {"days_behind": int((latest - last).days)}})

    recent = prices[prices["date"] >= latest - pd.Timedelta(days=10)]
    bad = recent[(recent["price"] <= 0) & ~recent["ric"].str.contains("BASIS|SOY-CTILL", regex=True)]
    for r in bad.itertuples(index=False):
        if r.ric == "SOY-CTILL-US":
            continue
        findings.append({"check_code": "non_positive_price", "severity": "warn", "ric": r.ric,
                         "obs_date": r.date.date(), "detail": {"price": float(r.price)}})

    for p in PRODUCTS:
        ric = futures_ric(p, 1)
        s = prices[prices["ric"] == ric].set_index("date")["price"].sort_index().tail(260)
        if len(s) < 60 or (s <= 0).any():
            continue
        lr = np.log(s).diff().dropna()
        sd = lr.iloc[:-1].std()
        if sd and abs(lr.iloc[-1]) > JUMP_SIGMA * sd:
            findings.append({"check_code": "price_jump", "severity": "warn", "ric": ric,
                             "obs_date": s.index[-1].date(),
                             "detail": {"log_return": float(lr.iloc[-1]), "sigma": float(sd)}})

    if not oi_cum.empty:
        last_oi = oi_cum[pd.to_datetime(oi_cum["date"]) >= latest - pd.Timedelta(days=3)]
        for r in last_oi.itertuples(index=False):
            if pd.notna(r.oi_vol_pct) and abs(r.oi_vol_pct) > OI_VOL_ANOMALY:
                findings.append({"check_code": "oi_vol_anomaly", "severity": "info", "ric": r.product_code,
                                 "obs_date": pd.Timestamp(r.date).date(),
                                 "detail": {"oi_vol_pct": round(float(r.oi_vol_pct), 1)}})
    return findings
