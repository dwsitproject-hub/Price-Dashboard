"""Deterministic synthetic market data for demo mode (PRD DATA-11).

Generates every RIC in the universe with realistic levels, cross-product correlation, a seasonal cycle,
time-varying contango/backwardation, regional cash premiums, FX, and OI/volume. Output is shaped exactly
like LSEG rows, so the rest of the pipeline is identical in demo and live mode.
The numbers are SYNTHETIC — the UI shows a "demo data" banner whenever data.mode = demo.
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from app.catalog.instruments import CASH, PRODUCTS, all_rics, futures_ric, parse_futures_ric

SEED = 20261104
OI_HISTORY_START = date(2023, 1, 2)
# Random draws are made over a FIXED horizon, then truncated to the requested end date, so history
# never changes as days pass (incremental demo runs only add new days).
HORIZON_END = date(2030, 12, 31)

# USD/MT anchor levels, annualised vol, seasonal amplitude, seasonal phase (months), factor group
_PRODUCT_PARAMS = {
    "FCPO": (960.0, 0.26, 0.045, 1.5, "palm"),
    "DCP": (1080.0, 0.25, 0.040, 1.0, "palm"),
    "BO": (1100.0, 0.28, 0.035, 5.0, "soy"),
    "DBY": (1140.0, 0.24, 0.030, 4.5, "soy"),
    "S": (440.0, 0.20, 0.050, 6.0, "soy"),
    "SM": (390.0, 0.24, 0.045, 6.5, "soy"),
    "LGO": (760.0, 0.32, 0.040, 10.0, "energy"),
    "HO": (720.0, 0.33, 0.045, 10.5, "energy"),
    "CL": (480.0, 0.34, 0.035, 7.0, "energy"),
    "LCO": (510.0, 0.32, 0.035, 7.0, "energy"),
}
_OI_BASE = {"FCPO": 260_000, "DCP": 900_000, "BO": 520_000, "SM": 480_000, "S": 760_000, "LGO": 520_000,
            "HO": 330_000, "CL": 1_650_000, "LCO": 2_100_000, "DBY": 820_000}


def business_days(start: date, end: date) -> pd.DatetimeIndex:
    return pd.bdate_range(start, end)


def _ou_walk(rng: np.random.Generator, n: int, vol_daily: float, kappa: float = 0.004) -> np.ndarray:
    """Mean-reverting log walk around 0."""
    shocks = rng.normal(0, vol_daily, n)
    x = np.zeros(n)
    for i in range(1, n):
        x[i] = x[i - 1] * (1 - kappa) + shocks[i]
    return x


def generate(start: date, end: date) -> pd.DataFrame:
    """Return LSEG-shaped rows: RIC, Trade Date, Last, Settlement Price, Bid, Ask, Volume, Open Interest."""
    days = business_days(start, HORIZON_END)
    n = len(days)
    t = np.arange(n)
    doy = days.dayofyear.to_numpy()
    rng = np.random.default_rng(SEED)

    factors = {g: _ou_walk(rng, n, 0.011, 0.0015) for g in ("palm", "soy", "energy", "macro")}
    fx = {
        "MYR=": 4.35 * np.exp(_ou_walk(rng, n, 0.0035, 0.002) * 1.0),
        "CNY=": 6.95 * np.exp(_ou_walk(rng, n, 0.0022, 0.002)),
        "GBP=": 1.27 * np.exp(_ou_walk(rng, n, 0.005, 0.002)),
        "EUR=": 1.10 * np.exp(_ou_walk(rng, n, 0.0045, 0.002)),
    }

    usd: dict[str, np.ndarray] = {}     # USD/MT per futures RIC
    oi: dict[str, np.ndarray] = {}
    vol: dict[str, np.ndarray] = {}
    oi_mask = days.date >= OI_HISTORY_START
    for code, (level, ann_vol, s_amp, s_phase, grp) in _PRODUCT_PARAMS.items():
        dv = ann_vol / np.sqrt(252)
        idio = _ou_walk(rng, n, dv * 0.35, 0.012)
        common = (factors["macro"] * 0.8 + factors[grp] * 0.35) * (dv / 0.011) * 0.75
        season = s_amp * np.sin(2 * np.pi * (doy / 365.25 - s_phase / 12))
        spot = level * np.exp(common + idio + season)
        # contango/backwardation that oscillates slowly + seasonal kink → calendar spreads move
        slope = 0.006 * np.sin(2 * np.pi * t / 700 + sum(map(ord, code)) % 7) + 0.004 * np.sin(2 * np.pi * doy / 365.25)
        p = PRODUCTS[code]
        oi_total = _OI_BASE[code] * np.exp(_ou_walk(rng, n, 0.012, 0.01))
        weights = np.array([np.exp(-((k - 3.0) / (2.5 + p.oi_contracts / 12)) ** 2) + 0.02
                            for k in range(1, p.oi_contracts + 1)])
        if code in ("DCP", "DBY"):                       # Dalian liquidity sits in the Jan/May/Sep months
            weights = np.array([w * (6 if k in (4, 8, 12) else 0.4) for k, w in zip(range(1, p.oi_contracts + 1),
                                                                                    weights)])
        weights /= weights.sum()
        for k in range(1, p.oi_contracts + 1):
            ric = futures_ric(code, k)
            tenor = min(k, 24) - 1
            noise = rng.normal(0, 0.0012, n)
            usd[ric] = spot * (1 + slope * tenor / 1.0 + noise) * (1 - 0.0004 * max(0, k - 24))
            o = oi_total * weights[k - 1] * np.exp(rng.normal(0, 0.03, n))
            turnover = np.clip(rng.normal(0.22, 0.07, n), 0.02, 0.9) * (1.6 if k <= 3 else 0.6)
            oi[ric] = np.where(oi_mask, np.round(o), np.nan)
            vol[ric] = np.where(oi_mask, np.round(o * turnover), np.nan)

    def native(ric: str, usd_vals: np.ndarray) -> np.ndarray:
        code = parse_futures_ric(ric)[0]
        prod = PRODUCTS[code]
        if prod.conversion_type == "factor":
            return usd_vals / prod.conversion_factor
        if prod.conversion_type == "fx_div":
            return usd_vals * fx[prod.conversion_fx_ric]
        return usd_vals

    # Cash (USD/MT unless the instrument is MYR/CNY native) — premiums vs FCPO c3 / regional legs
    f3 = usd["FCPOc3"]
    prem = lambda base, sd, k=0.01: base + 18 * _ou_walk(rng, n, sd, k)  # noqa: E731
    cash_usd = {
        "CPO-MYSTH-M1": f3 + prem(-25, 0.08), "POL-MYRBD-M1": f3 + prem(55, 0.09),
        "PO-MYRBD-M1": f3 + prem(35, 0.09), "PSTR-MYRBD-M1": f3 + prem(5, 0.1),
        "PFAD-MY-M1": f3 + prem(-90, 0.12),
    }
    for k in list(cash_usd):
        cash_usd[k.replace("-M1", "-M2")] = cash_usd[k] + prem(4, 0.05)
    for my, idn, base in [("CPO-MYSTH", "CPO-ID", -18), ("POL-MYRBD", "POL-IDRBD", -12),
                          ("PO-MYRBD", "PO-IDRBD", -10), ("PSTR-MYRBD", "PSTR-IDRBD", -8)]:
        for m in ("M1", "M2"):
            cash_usd[f"{idn}-{m}"] = cash_usd[f"{my}-{m}"] + prem(base, 0.08)
    cash_usd["SOY-CTILL-US"] = prem(0, 0.12)
    cash_usd["SM-ARGUPR-P1"] = usd["SMc1"] + prem(-12, 0.1)
    cash_usd["S-ARGUPR-C1"] = usd["Sc1"] + prem(8, 0.1)
    cash_usd["BO-ARGUPR-C1"] = usd["BOc1"] + prem(-70, 0.12)
    cash_usd["JCI-SOA-DALN"] = usd["DBYc1"] + prem(25, 0.08)
    cash_usd["KTS-SOUABS-FOB"] = usd["BOc1"] * 0.94 + prem(0, 0.12)

    frames = []
    date_str = days.strftime("%Y-%m-%d")

    def frame(ric, last, settle, bid=np.nan, ask=np.nan, volume=np.nan, open_int=np.nan, drop_rate=0.0):
        df = pd.DataFrame({"RIC": ric, "Trade Date": date_str, "Last": last, "Settlement Price": settle,
                           "Bid": bid, "Ask": ask, "Volume": volume, "Open Interest": open_int})
        if drop_rate:
            df = df[rng.random(n) > drop_rate]
        frames.append(df)

    for ric in all_rics():
        fut = parse_futures_ric(ric)
        if fut:
            if ric not in usd:
                continue
            nat = native(ric, usd[ric])
            core = fut[1] <= 7 or (fut[0] == "FCPO" and fut[1] <= 12)
            px = np.round(nat, 4)
            if not core:                                  # extended curve: only since OI history start
                mask = oi_mask
                px = np.where(mask, px, np.nan)
            frame(ric, px, np.round(px * (1 + rng.normal(0, 0.0004, n)), 4), volume=vol[ric], open_int=oi[ric])
        elif ric in fx:
            mid = fx[ric]
            spread = mid * 0.0002
            frame(ric, np.nan, np.nan, np.round(mid - spread, 6), np.round(mid + spread, 6))
        elif ric in CASH:
            c = CASH[ric]
            vals = cash_usd[ric]
            if c.conversion_type == "fx_div":
                vals = vals * fx[c.conversion_fx_ric]
            vals = np.round(vals, 2)
            frame(ric, vals, vals, drop_rate=0.004)

    out = pd.concat(frames, ignore_index=True)
    out = out[(out["Last"].notna() | out["Bid"].notna()) & (out["Trade Date"] <= end.isoformat())]
    return out.reset_index(drop=True)


def generate_rows(start: date, end: date) -> list[dict]:
    df = generate(start, end)
    df = df.astype(object).where(pd.notna(df), None)
    return df.to_dict("records")
