"""
BO Options — Data Store Builder + 10-Year Historical Depth Test.
Part 1: Display existing plots
Part 2: Build persistent parquet data store
Part 3: 10-year historical depth via BOc3-derived ATM strikes
"""

import sys
import os
import time
import math
import gzip
import io
import json
from datetime import date, datetime, timedelta
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np
import pandas as pd
import requests
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy.optimize import brentq
from scipy.stats import norm

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
from scripts.auth import get_token

EXTRACT_URL = "https://selectapi.datascope.lseg.com/RestApi/v1/Extractions/ExtractWithNotes"
EOD_FIELDS = ["RIC", "Trade Date", "Universal Close Price", "Volume",
              "Open Interest", "Settlement Price", "Last", "Bid", "Ask"]
POLL_INTERVAL = 10
SCRIPTS_DIR = Path(__file__).parent
DATA_DIR = ROOT / "data" / "options"

# ── Config ───────────────────────────────────────────────────────────────────
EXPIRY = date(2026, 5, 14)
R_FREE = 0.05
CALL_CODE = "F"
PUT_CODE = "R"
UNDERLYING_RICS = ["BOc2", "BOc1"]
STRIKES = [round(50.0 + i * 0.5, 1) for i in range(61)]

JUN_EXPIRIES = {
    2016: date(2016, 5, 13), 2017: date(2017, 5, 12), 2018: date(2018, 5, 11),
    2019: date(2019, 5, 10), 2020: date(2020, 5, 14), 2021: date(2021, 5, 13),
    2022: date(2022, 5, 13), 2023: date(2023, 5, 12), 2024: date(2024, 5, 10),
    2025: date(2025, 5, 9),
}


# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════

def encode_strike(strike: float) -> str:
    val = strike * 10
    if val == int(val):
        return str(int(val))
    return str(int(round(strike * 100)))


def build_ric(strike: float, month_code: str, year: int = 26) -> str:
    return f"BO{encode_strike(strike)}{month_code}{year}"


def _headers(token):
    return {"Authorization": f"Token {token}",
            "Content-Type": "application/json",
            "Prefer": "respond-async"}


def _poll_json(loc, token):
    attempt = 0
    while True:
        attempt += 1
        print(f"    Polling... ({attempt})", end="\r", flush=True)
        time.sleep(POLL_INTERVAL)
        r = requests.get(loc, headers=_headers(token), timeout=(30, 600))
        if r.status_code == 200:
            print(f"    Polling... done ({attempt})")
            return r.json()
        if r.status_code != 202:
            raise RuntimeError(f"Poll fail ({r.status_code}): {r.text[:300]}")


def extract_eod(rics, token, start=None, end=None, allow_historical=False, quiet=False):
    if end is None:
        end = date.today()
    if start is None:
        start = end - timedelta(days=30)
    validation = {"AllowHistoricalInstruments": True} if allow_historical else None
    body = {
        "ExtractionRequest": {
            "@odata.type": ("#DataScope.Select.Api.Extractions.ExtractionRequests"
                            ".ElektronTimeseriesExtractionRequest"),
            "ContentFieldNames": EOD_FIELDS,
            "IdentifierList": {
                "@odata.type": ("#DataScope.Select.Api.Extractions.ExtractionRequests"
                                ".InstrumentIdentifierList"),
                "InstrumentIdentifiers": [
                    {"Identifier": r, "IdentifierType": "Ric"} for r in rics
                ],
                "ValidationOptions": validation,
                "UseUserPreferencesForValidationOptions": False,
            },
            "Condition": {
                "ReportDateRangeType": "Range",
                "StartDate": f"{start}T00:00:00.000Z",
                "EndDate": f"{end}T00:00:00.000Z",
            },
        }
    }
    resp = requests.post(EXTRACT_URL, headers=_headers(token), json=body, timeout=(30, 600))
    if resp.status_code == 202:
        loc = resp.headers.get("Location")
        data = _poll_json(loc, token)
    elif resp.status_code == 200:
        data = resp.json()
    else:
        try:
            msg = resp.json()["error"]["message"]
        except Exception:
            msg = resp.text[:500]
        raise RuntimeError(f"EOD failed ({resp.status_code}): {msg}")
    if not quiet:
        notes = data.get("Notes", [])
        if notes:
            print(f"    Notes: {str(notes)[:200]}...")
    contents = data.get("Contents", [])
    return pd.DataFrame(contents) if contents else pd.DataFrame(columns=EOD_FIELDS)


def resolve_price(row):
    for col in ["Settlement Price", "Universal Close Price", "Last"]:
        if col in row.index and pd.notna(row[col]):
            v = float(row[col])
            if v > 0:
                return v
    if "Bid" in row.index and "Ask" in row.index:
        b, a = row.get("Bid"), row.get("Ask")
        if pd.notna(b) and pd.notna(a) and float(b) > 0 and float(a) > 0:
            return (float(b) + float(a)) / 2
    return np.nan


def bs_price(S, K, T, r, sigma, is_call):
    if T <= 0 or sigma <= 0:
        return max(0, (S - K) if is_call else (K - S))
    d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    if is_call:
        return S * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(d2)
    else:
        return K * math.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)


def iv_solve(mkt, S, K, T, r, is_call):
    if T <= 0 or mkt <= 0.01:
        return np.nan
    try:
        return brentq(lambda sig: bs_price(S, K, T, r, sig, is_call) - mkt,
                      0.001, 5.0, xtol=1e-6, maxiter=200)
    except (ValueError, RuntimeError):
        return np.nan


def bs_greeks(S, K, T, r, sigma, is_call):
    if T <= 0 or sigma <= 0 or np.isnan(sigma):
        return np.nan, np.nan, np.nan, np.nan
    sqT = math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * sqT)
    d2 = d1 - sigma * sqT
    delta = norm.cdf(d1) if is_call else norm.cdf(d1) - 1
    gamma = norm.pdf(d1) / (S * sigma * sqT)
    vega = S * norm.pdf(d1) * sqT / 100.0
    t1 = -(S * norm.pdf(d1) * sigma) / (2 * sqT)
    t2 = -r * K * math.exp(-r * T) * norm.cdf(d2) if is_call \
        else r * K * math.exp(-r * T) * norm.cdf(-d2)
    theta = (t1 + t2) / 365.0
    return delta, gamma, vega, theta


def file_modified_today(path: Path) -> bool:
    if not path.exists():
        return False
    mtime = datetime.fromtimestamp(path.stat().st_mtime).date()
    return mtime == date.today()


def file_size_str(path: Path) -> str:
    if not path.exists():
        return "0"
    sz = path.stat().st_size
    if sz < 1024:
        return f"{sz} B"
    elif sz < 1024**2:
        return f"{sz/1024:.1f} KB"
    else:
        return f"{sz/1024**2:.1f} MB"


def prep_df(df):
    """Standard numeric coercion for a raw EOD DataFrame."""
    df["Trade Date"] = pd.to_datetime(df["Trade Date"], errors="coerce")
    for c in ["Universal Close Price", "Volume", "Open Interest",
              "Settlement Price", "Last", "Bid", "Ask"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df["_price"] = df.apply(resolve_price, axis=1)
    return df


def build_chain_parquet(df, und_c1, und_c2):
    """Convert raw EOD df into clean chain format."""
    rows = []
    for _, r in df.iterrows():
        ric = r["RIC"]
        if ric in ("BOc1", "BOc2"):
            continue
        dt = r["Trade Date"]
        strike = None
        opt_type = None
        if isinstance(ric, str) and ric.startswith("BO") and len(ric) >= 6:
            mc = ric[-3]
            body = ric[2:-3]
            try:
                v = int(body)
                strike = v / 10.0 if len(body) <= 3 else v / 100.0
            except ValueError:
                continue
            if mc == CALL_CODE:
                opt_type = "Call"
            elif mc == PUT_CODE:
                opt_type = "Put"
            else:
                continue
        else:
            continue

        u1 = und_c1.get(dt, np.nan) if und_c1 is not None else np.nan
        u2 = und_c2.get(dt, np.nan) if und_c2 is not None else np.nan
        rows.append({
            "RIC": ric, "Date": dt, "Strike": strike, "OptionType": opt_type,
            "Settlement": r["_price"],
            "OI": r.get("Open Interest") if pd.notna(r.get("Open Interest")) else np.nan,
            "Volume": r.get("Volume") if pd.notna(r.get("Volume")) else np.nan,
            "Underlying_c1": u1, "Underlying_c2": u2,
        })
    return pd.DataFrame(rows)


# ══════════════════════════════════════════════════════════════════════════════
# PART 1 — Display plots
# ══════════════════════════════════════════════════════════════════════════════

def part1():
    print(f"\n{'#'*70}")
    print(f"  PART 1 -- Display Existing Plots")
    print(f"{'#'*70}\n")

    for fname in ["bo_options_atm_history.png", "bo_options_reference_table.png"]:
        p = SCRIPTS_DIR / fname
        if not p.exists():
            print(f"  {fname}: NOT FOUND -- skipping")
            continue
        print(f"  {fname}: EXISTS ({file_size_str(p)})")
        # Open in default viewer
        import subprocess
        subprocess.Popen(["cmd", "/c", "start", "", str(p)], shell=True)
        print(f"  Opened in viewer.")


# ══════════════════════════════════════════════════════════════════════════════
# PART 2 — Build persistent data store
# ══════════════════════════════════════════════════════════════════════════════

def part2(token):
    print(f"\n{'#'*70}")
    print(f"  PART 2 -- Build Persistent Data Store")
    print(f"{'#'*70}\n")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    call_rics = [build_ric(s, CALL_CODE) for s in STRIKES]
    put_rics = [build_ric(s, PUT_CODE) for s in STRIKES]
    all_opt = call_rics + put_rics
    all_rics = all_opt + UNDERLYING_RICS

    # ── File 1: bo_eod_chain_jun26.parquet (30 days) ────────────────────────
    f1 = DATA_DIR / "bo_eod_chain_jun26.parquet"
    if file_modified_today(f1):
        print(f"  [SKIP] {f1.name} (modified today)")
        df30 = pd.read_parquet(f1)
    else:
        print(f"  [PULL] {f1.name} -- 30-day Jun26 chain...")
        raw = extract_eod(all_rics, token)
        raw = prep_df(raw)
        # Build underlying lookups
        und_c1 = raw[raw["RIC"] == "BOc1"].set_index("Trade Date")["_price"].to_dict()
        und_c2 = raw[raw["RIC"] == "BOc2"].set_index("Trade Date")["_price"].to_dict()
        df30 = build_chain_parquet(raw, und_c1, und_c2)
        df30.to_parquet(str(f1), index=False)

    dr = f"{df30['Date'].min().date()} to {df30['Date'].max().date()}" if len(df30) else "--"
    print(f"    {f1.name:40s} | {len(df30):>7,} rows | {dr} | {file_size_str(f1)}")

    # ── File 2: bo_eod_chain_history.parquet (365 days) ─────────────────────
    f2 = DATA_DIR / "bo_eod_chain_history.parquet"
    if file_modified_today(f2):
        print(f"  [SKIP] {f2.name} (modified today)")
        df365 = pd.read_parquet(f2)
    else:
        print(f"  [PULL] {f2.name} -- 365-day Jun26 chain...")
        raw = extract_eod(all_rics, token,
                          start=date.today() - timedelta(days=365),
                          allow_historical=True)
        raw = prep_df(raw)
        und_c1 = raw[raw["RIC"] == "BOc1"].set_index("Trade Date")["_price"].to_dict()
        und_c2 = raw[raw["RIC"] == "BOc2"].set_index("Trade Date")["_price"].to_dict()
        df365 = build_chain_parquet(raw, und_c1, und_c2)
        df365.to_parquet(str(f2), index=False)

    dr = f"{df365['Date'].min().date()} to {df365['Date'].max().date()}" if len(df365) else "--"
    print(f"    {f2.name:40s} | {len(df365):>7,} rows | {dr} | {file_size_str(f2)}")

    # ── File 3: bo_greeks_jun26.parquet ──────────────────────────────────────
    f3 = DATA_DIR / "bo_greeks_jun26.parquet"
    if file_modified_today(f3):
        print(f"  [SKIP] {f3.name} (modified today)")
        greeks_df = pd.read_parquet(f3)
    else:
        print(f"  [CALC] {f3.name} -- computing Greeks for 30-day chain...")
        src = df30.copy()
        results = []
        for _, row in src.iterrows():
            dt = row["Date"]
            S = row["Underlying_c2"]
            if pd.isna(S) or S <= 0:
                S = row["Underlying_c1"]
            if pd.isna(S) or S <= 0:
                continue
            K = row["Strike"]
            settle = row["Settlement"]
            is_call = row["OptionType"] == "Call"
            T = (EXPIRY - dt.date()).days / 365.0 if pd.notna(dt) else 0
            if T <= 0:
                continue
            iv = iv_solve(settle, S, K, T, R_FREE, is_call) if pd.notna(settle) else np.nan
            delta, gamma, vega, theta = bs_greeks(S, K, T, R_FREE, iv, is_call)
            intrinsic = max(0, (S - K) if is_call else (K - S))
            tv = (settle - intrinsic) if pd.notna(settle) else np.nan
            results.append({
                "Date": dt, "Strike": K, "OptionType": row["OptionType"],
                "RIC": row["RIC"], "Settlement": settle, "Underlying": S,
                "IV": iv, "Delta": delta, "Gamma": gamma, "Vega": vega, "Theta": theta,
                "IntrinsicValue": intrinsic, "TimeValue": tv,
            })
        greeks_df = pd.DataFrame(results)
        greeks_df.to_parquet(str(f3), index=False)

    dr = f"{greeks_df['Date'].min().date()} to {greeks_df['Date'].max().date()}" if len(greeks_df) else "--"
    print(f"    {f3.name:40s} | {len(greeks_df):>7,} rows | {dr} | {file_size_str(f3)}")

    # ── File 4: bo_oi_buildup.parquet ────────────────────────────────────────
    f4 = DATA_DIR / "bo_oi_buildup.parquet"
    if file_modified_today(f4):
        print(f"  [SKIP] {f4.name} (modified today)")
        oi_df = pd.read_parquet(f4)
    else:
        print(f"  [CALC] {f4.name} -- computing OI buildup signals...")
        src = df30.sort_values(["RIC", "Date"]).copy()
        results = []
        for ric, grp in src.groupby("RIC"):
            grp = grp.sort_values("Date").reset_index(drop=True)
            for i in range(1, len(grp)):
                row = grp.iloc[i]
                prev = grp.iloc[i - 1]
                oi_t = row["OI"]
                oi_p = prev["OI"]
                pr_t = row["Settlement"]
                pr_p = prev["Settlement"]
                if pd.isna(oi_t) or pd.isna(oi_p) or pd.isna(pr_t) or pd.isna(pr_p):
                    continue
                if oi_p <= 0 or pr_p <= 0:
                    continue
                d_oi = oi_t - oi_p
                d_pr = pr_t - pr_p
                threshold = 0.01 * oi_p
                if abs(d_oi) < threshold:
                    sig = "Neutral"
                elif d_oi > 0 and d_pr > 0:
                    sig = "LB"
                elif d_oi > 0 and d_pr <= 0:
                    sig = "SB"
                elif d_oi <= 0 and d_pr <= 0:
                    sig = "LU"
                else:
                    sig = "SC"

                # WOAS contribution = |delta_OI| * strike
                woas_c = abs(d_oi) * row["Strike"]
                results.append({
                    "Date": row["Date"], "Strike": row["Strike"],
                    "OptionType": row["OptionType"], "RIC": ric,
                    "OI": oi_t, "OI_prev": oi_p, "OI_change": d_oi,
                    "Price": pr_t, "Price_prev": pr_p, "Price_change": d_pr,
                    "Signal": sig, "WOAS_contribution": woas_c,
                })
        oi_df = pd.DataFrame(results)
        oi_df.to_parquet(str(f4), index=False)

    dr = f"{oi_df['Date'].min().date()} to {oi_df['Date'].max().date()}" if len(oi_df) else "--"
    print(f"    {f4.name:40s} | {len(oi_df):>7,} rows | {dr} | {file_size_str(f4)}")

    print(f"\n  All 4 parquet files in: {DATA_DIR}")
    return df30


# ══════════════════════════════════════════════════════════════════════════════
# PART 3 — 10-Year Historical Depth Test
# ══════════════════════════════════════════════════════════════════════════════

def part3(token):
    print(f"\n{'#'*70}")
    print(f"  PART 3 -- 10-Year Historical Depth Test via BOc3")
    print(f"{'#'*70}\n")

    # ── Step 3a: Pull BOc3 full history ──────────────────────────────────────
    print("  Step 3a: Pulling BOc3 full history (2015-01-01 to today)...")
    boc3_raw = extract_eod(["BOc3"], token,
                           start=date(2015, 1, 1), end=date.today(),
                           allow_historical=True)
    boc3_raw = prep_df(boc3_raw)
    boc3 = boc3_raw[boc3_raw["RIC"] == "BOc3"].copy()
    boc3 = boc3.dropna(subset=["_price"]).set_index("Trade Date")["_price"].sort_index()
    print(f"    BOc3 rows: {len(boc3):,}, {boc3.index[0].date()} to {boc3.index[-1].date()}")
    boc3_start = boc3.index[0].date()

    # Derive ATM per year
    print("\n  Deriving ATM strike per year from BOc3 on first June trading day:\n")
    print(f"    {'Year':<6} {'BOc3 Jun1':>10} {'ATM Strike':>11} {'Call RIC':<14} {'Put RIC':<14}")
    print(f"    {'-'*5}  {'-'*10} {'-'*11} {'-'*13} {'-'*13}")

    year_tests = []
    for year in range(2016, 2026):
        # Find first trading day in June
        jun_start = pd.Timestamp(year, 6, 1)
        candidates = boc3.index[(boc3.index >= jun_start) &
                                (boc3.index < jun_start + pd.Timedelta(days=10))]
        if candidates.empty:
            # Try May if June data not available yet
            may_end = pd.Timestamp(year, 5, 31)
            candidates = boc3.index[(boc3.index >= may_end - pd.Timedelta(days=5)) &
                                    (boc3.index <= may_end)]
        if candidates.empty:
            print(f"    {year:<6} {'--':>10} {'--':>11} {'--':<14} {'--':<14}")
            continue

        price = float(boc3.loc[candidates[0]])
        # Round to nearest 0.5
        atm = round(price * 2) / 2
        yr2 = year % 100
        call_ric = build_ric(atm, "F", yr2)
        put_ric = build_ric(atm, "R", yr2)
        print(f"    {year:<6} {price:>10.2f} {atm:>11.1f} {call_ric:<14} {put_ric:<14}")
        year_tests.append({
            "year": year, "price": price, "atm": atm,
            "call_ric": call_ric, "put_ric": put_ric,
            "expiry": JUN_EXPIRIES.get(year),
        })

    # ── Step 3b: Pull options data for each year ─────────────────────────────
    print(f"\n  Step 3b: Pulling expired option data for each year...")

    hist_results = []
    earliest_data = None
    earliest_oi = None

    for yt in year_tests:
        year = yt["year"]
        exp = yt["expiry"]
        if exp is None:
            print(f"    {year}: no expiry date configured -- skip")
            continue

        start_dt = exp - timedelta(days=90)
        end_dt = exp + timedelta(days=5)

        # Try the primary ATM strike first, then fallbacks
        attempts = [yt["atm"]]
        for offset in [0.5, -0.5, 1.0, -1.0]:
            attempts.append(round((yt["atm"] + offset) * 2) / 2)

        for atm_try in attempts:
            yr2 = yt["year"] % 100
            c_ric = build_ric(atm_try, "F", yr2)
            p_ric = build_ric(atm_try, "R", yr2)

            try:
                raw = extract_eod([c_ric, p_ric], token,
                                  start=start_dt, end=end_dt,
                                  allow_historical=True, quiet=True)
                raw = prep_df(raw)
            except Exception as e:
                print(f"    {year} ATM={atm_try}: error -- {e}")
                continue

            c_sub = raw[raw["RIC"] == c_ric]
            p_sub = raw[raw["RIC"] == p_ric]

            if len(c_sub) > 0 or len(p_sub) > 0:
                # Found data
                for ric, sub, cp in [(c_ric, c_sub, "Call"), (p_ric, p_sub, "Put")]:
                    if sub.empty:
                        hist_results.append({
                            "year": year, "ric": ric, "cp": cp,
                            "atm_used": atm_try, "rows": 0,
                            "date_range": "--", "oi_pop": "No",
                        })
                        continue

                    dates = sub["Trade Date"].dropna()
                    dr = f"{dates.min().date()}-{dates.max().date()}"
                    oi_pop = "No"
                    if "Open Interest" in sub.columns:
                        oi_vals = sub["Open Interest"].dropna()
                        oi_vals = oi_vals[oi_vals > 0]
                        if not oi_vals.empty:
                            oi_pop = "Yes"
                            if earliest_oi is None or dates.min().date() < earliest_oi:
                                earliest_oi = dates.min().date()

                    if earliest_data is None or dates.min().date() < earliest_data:
                        earliest_data = dates.min().date()

                    hist_results.append({
                        "year": year, "ric": ric, "cp": cp,
                        "atm_used": atm_try, "rows": len(sub),
                        "date_range": dr, "oi_pop": oi_pop,
                    })

                print(f"    {year} ATM={atm_try}: OK "
                      f"(C={len(c_sub)}r, P={len(p_sub)}r)")
                break
            else:
                # Try next fallback
                continue
        else:
            # All attempts exhausted
            print(f"    {year}: NO DATA from any ATM attempt")
            for cp in ["Call", "Put"]:
                hist_results.append({
                    "year": year, "ric": f"BO{encode_strike(yt['atm'])}{'F' if cp=='Call' else 'R'}{yt['year']%100}",
                    "cp": cp, "atm_used": yt["atm"],
                    "rows": 0, "date_range": "--", "oi_pop": "No",
                })

    # ── Step 3c: Plot BOc3 history ───────────────────────────────────────────
    print(f"\n  Step 3c: Plotting BOc3 full history...")

    fig, ax = plt.subplots(figsize=(16, 7))
    ax.plot(boc3.index, boc3.values, color="#1F3864", linewidth=1.2)

    # Price regime bands
    ax.axhspan(25, 35, alpha=0.08, color="blue", label="Low regime (25-35)")
    ax.axhspan(35, 50, alpha=0.08, color="orange", label="Mid regime (35-50)")
    ax.axhspan(50, 90, alpha=0.08, color="red", label="Elevated (50+)")

    # Jun expiry markers
    for yt in year_tests:
        exp = yt["expiry"]
        if exp and pd.Timestamp(exp) in boc3.index:
            y = boc3.loc[pd.Timestamp(exp)]
        elif exp:
            # nearest
            idx = boc3.index.get_indexer([pd.Timestamp(exp)], method="nearest")
            y = boc3.iloc[idx[0]] if idx[0] >= 0 else yt["price"]
        else:
            continue
        ax.axvline(pd.Timestamp(exp), color="grey", alpha=0.4, linewidth=0.8)
        ax.annotate(f"{yt['atm']:.0f}", xy=(pd.Timestamp(exp), y),
                    xytext=(5, 10), textcoords="offset points",
                    fontsize=7, color="darkred", fontweight="bold")

    ax.set_ylabel("BOc3 (cents/lb)", fontsize=12)
    ax.set_title("CBOT Soybean Oil (BOc3) -- Full History with Jun Expiry ATM Strikes",
                 fontsize=13, fontweight="bold")
    ax.grid(True, alpha=0.3)
    ax.legend(loc="upper left", fontsize=9)
    fig.autofmt_xdate()
    fig.tight_layout()
    c3_path = SCRIPTS_DIR / "bo_c3_history.png"
    fig.savefig(str(c3_path), dpi=150)
    print(f"    Saved: {c3_path}")
    import subprocess
    subprocess.Popen(["cmd", "/c", "start", "", str(c3_path)], shell=True)

    # ── Step 3d: Summary table ───────────────────────────────────────────────
    print(f"\n  Step 3d: Historical Data Availability\n")
    print(f"    {'Year':<6} {'Call RIC':<14} {'C Rows':>7} {'Call Date Range':<26} {'OI?':<5} | "
          f"{'Put RIC':<14} {'P Rows':>7} {'Put Date Range':<26} {'OI?':<5}")
    print(f"    {'-'*5}  {'-'*12}  {'-'*7} {'-'*25} {'-'*4} | "
          f"{'-'*12}  {'-'*7} {'-'*25} {'-'*4}")

    years_with_data = set()
    for year in sorted(set(r["year"] for r in hist_results)):
        c_rows = [r for r in hist_results if r["year"] == year and r["cp"] == "Call"]
        p_rows = [r for r in hist_results if r["year"] == year and r["cp"] == "Put"]
        c = c_rows[0] if c_rows else None
        p = p_rows[0] if p_rows else None

        c_str = (f"{c['ric']:<14} {c['rows']:>7} {c['date_range']:<26} {c['oi_pop']:<5}"
                 if c else f"{'--':<14} {'0':>7} {'--':<26} {'--':<5}")
        p_str = (f"{p['ric']:<14} {p['rows']:>7} {p['date_range']:<26} {p['oi_pop']:<5}"
                 if p else f"{'--':<14} {'0':>7} {'--':<26} {'--':<5}")

        if c and c["rows"] > 0:
            years_with_data.add(year)
        if p and p["rows"] > 0:
            years_with_data.add(year)

        print(f"    {year:<6} {c_str} | {p_str}")

    depth = len(years_with_data)
    earliest_yr = min(years_with_data) if years_with_data else "?"

    print(f"\n  BOc3 history: {boc3_start} to {boc3.index[-1].date()} | "
          f"Options data confirmed back to {earliest_yr} | "
          f"Earliest OI data: {earliest_oi or '?'} | 4 parquet files saved")


# ══════════════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════════════

def main():
    token = get_token()
    part1()
    part2(token)
    part3(token)


if __name__ == "__main__":
    main()