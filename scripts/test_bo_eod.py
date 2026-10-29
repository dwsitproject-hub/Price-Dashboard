"""
Diagnostic: pull 30 days EOD for CBOT Soybean Oil options + compute Greeks.
Uses ElektronTimeseriesExtractionRequest / ExtractWithNotes.
Read-only — no files written.
"""

import sys
import time
import math
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import requests
from scipy.optimize import brentq
from scipy.stats import norm

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from scripts.auth import get_token

EXTRACT_URL = "https://selectapi.datascope.lseg.com/RestApi/v1/Extractions/ExtractWithNotes"
FIELDS = ["RIC", "Trade Date", "Universal Close Price", "Volume", "Open Interest",
          "Settlement Price", "Last", "Bid", "Ask"]
POLL_INTERVAL = 10

# ── Test RICs ────────────────────────────────────────────────────────────────
OPTION_RICS = [
    # Jun26 Calls
    "BO600F26", "BO620F26", "BO640F26", "BO660F26",
    # Jul26 Calls
    "BO620G26", "BO640G26",
    # Jun26 Puts
    "BO600R26", "BO620R26", "BO640R26", "BO660R26",
    # Jul26 Puts
    "BO620S26", "BO640S26",
]

UNDERLYING_RIC = "BOc1"

ALL_RICS = OPTION_RICS + [UNDERLYING_RIC]

# ── Option metadata ──────────────────────────────────────────────────────────
CALL_MONTHS = {"E", "F", "G"}
PUT_MONTHS = {"Q", "R", "S"}

# Expiry dates per month code
EXPIRY_MAP = {
    "F": date(2026, 5, 14),  # Jun26 options
    "R": date(2026, 5, 14),
    "G": date(2026, 6, 12),  # Jul26 options
    "S": date(2026, 6, 12),
}

R_FREE = 0.05  # 5% annualised


def _parse_ric(ric: str) -> dict | None:
    """Parse BO{strike}{monthcode}{year} into components."""
    if not ric.startswith("BO") or ric == "BOc1":
        return None
    body = ric[2:]  # e.g. "620F26"
    # Last 3 chars: month_code (1) + year (2)
    year_str = body[-2:]
    month_code = body[-3]
    strike_str = body[:-3]
    try:
        strike_hundredths = int(strike_str)
        strike = strike_hundredths / 10.0  # in cents/lb
        year = 2000 + int(year_str)
    except ValueError:
        return None

    is_call = month_code in CALL_MONTHS
    is_put = month_code in PUT_MONTHS
    if not is_call and not is_put:
        return None

    expiry = EXPIRY_MAP.get(month_code)
    return {
        "ric": ric,
        "strike": strike,
        "month_code": month_code,
        "year": year,
        "is_call": is_call,
        "cp": "Call" if is_call else "Put",
        "expiry": expiry,
    }


# ── Black-Scholes ────────────────────────────────────────────────────────────

def bs_price(S, K, T, r, sigma, is_call):
    """Black-Scholes European option price."""
    if T <= 0 or sigma <= 0:
        return max(0, (S - K) if is_call else (K - S))
    d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    if is_call:
        return S * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(d2)
    else:
        return K * math.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)


def implied_vol(market_price, S, K, T, r, is_call):
    """Solve for IV via Brent's method."""
    if T <= 0 or market_price <= 0:
        return None
    intrinsic = max(0, (S - K) if is_call else (K - S))
    if market_price < intrinsic - 0.001:
        return None
    try:
        iv = brentq(
            lambda sig: bs_price(S, K, T, r, sig, is_call) - market_price,
            0.001, 5.0, xtol=1e-6, maxiter=200,
        )
        return iv
    except (ValueError, RuntimeError):
        return None


def bs_greeks(S, K, T, r, sigma, is_call):
    """Return dict of delta, gamma, vega, theta."""
    if T <= 0 or sigma <= 0:
        return {"delta": float("nan"), "gamma": float("nan"),
                "vega": float("nan"), "theta": float("nan")}
    sqrtT = math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * sqrtT)
    d2 = d1 - sigma * sqrtT

    # Delta
    delta = norm.cdf(d1) if is_call else norm.cdf(d1) - 1

    # Gamma
    gamma = norm.pdf(d1) / (S * sigma * sqrtT)

    # Vega (per 1% move in vol, i.e. per 0.01)
    vega = S * norm.pdf(d1) * sqrtT / 100.0

    # Theta (per calendar day)
    term1 = -(S * norm.pdf(d1) * sigma) / (2 * sqrtT)
    if is_call:
        term2 = -r * K * math.exp(-r * T) * norm.cdf(d2)
    else:
        term2 = r * K * math.exp(-r * T) * norm.cdf(-d2)
    theta = (term1 + term2) / 365.0

    return {"delta": delta, "gamma": gamma, "vega": vega, "theta": theta}


# ── API helpers ──────────────────────────────────────────────────────────────

def _headers(token: str) -> dict:
    return {
        "Authorization": f"Token {token}",
        "Content-Type": "application/json",
        "Prefer": "respond-async",
    }


def _build_body(rics: list[str]) -> dict:
    end = date.today()
    start = end - timedelta(days=30)
    return {
        "ExtractionRequest": {
            "@odata.type": (
                "#DataScope.Select.Api.Extractions.ExtractionRequests"
                ".ElektronTimeseriesExtractionRequest"
            ),
            "ContentFieldNames": FIELDS,
            "IdentifierList": {
                "@odata.type": (
                    "#DataScope.Select.Api.Extractions.ExtractionRequests"
                    ".InstrumentIdentifierList"
                ),
                "InstrumentIdentifiers": [
                    {"Identifier": ric, "IdentifierType": "Ric"}
                    for ric in rics
                ],
                "ValidationOptions": None,
                "UseUserPreferencesForValidationOptions": False,
            },
            "Condition": {
                "ReportDateRangeType": "Range",
                "StartDate": f"{start}T00:00:00.000Z",
                "EndDate": f"{end}T00:00:00.000Z",
            },
        }
    }


def _poll(location: str, token: str) -> dict:
    attempt = 0
    while True:
        attempt += 1
        print(f"  Polling... (attempt {attempt})", end="\r", flush=True)
        time.sleep(POLL_INTERVAL)
        resp = requests.get(location, headers=_headers(token), timeout=(30, 300))
        if resp.status_code == 200:
            print(f"  Polling... done (attempt {attempt})")
            return resp.json()
        if resp.status_code != 202:
            raise RuntimeError(f"Poll failed ({resp.status_code}): {resp.text[:300]}")


def _extract(rics: list[str], token: str) -> pd.DataFrame:
    resp = requests.post(
        EXTRACT_URL,
        headers=_headers(token),
        json=_build_body(rics),
        timeout=(30, 300),
    )

    if resp.status_code == 202:
        location = resp.headers.get("Location")
        if not location:
            raise RuntimeError("Got 202 but no Location header.")
        data = _poll(location, token)
    elif resp.status_code == 200:
        data = resp.json()
    else:
        try:
            msg = resp.json()["error"]["message"]
        except Exception:
            msg = resp.text[:500]
        raise RuntimeError(f"Request failed ({resp.status_code}): {msg}")

    notes = data.get("Notes", [])
    if notes:
        note_str = str(notes)[:600]
        print(f"\n  API Notes: {note_str}")

    contents = data.get("Contents", [])
    return pd.DataFrame(contents) if contents else pd.DataFrame(columns=FIELDS)


def _resolve_price(row):
    """Best available price from a row."""
    for col in ["Settlement Price", "Universal Close Price", "Last"]:
        if col in row.index and pd.notna(row[col]):
            v = float(row[col])
            if v > 0:
                return v
    if "Bid" in row.index and "Ask" in row.index:
        b, a = row.get("Bid"), row.get("Ask")
        if pd.notna(b) and pd.notna(a) and float(b) > 0 and float(a) > 0:
            return (float(b) + float(a)) / 2
    return None


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    token = get_token()

    print(f"\n{'='*70}")
    print("  CBOT Soybean Oil Options — 30-Day EOD + Greeks")
    print(f"  Options: {len(OPTION_RICS)} RICs  |  Underlying: {UNDERLYING_RIC}")
    print(f"{'='*70}\n")

    # ── Fetch data ───────────────────────────────────────────────────────────
    print("  Fetching EOD data...")
    df = _extract(ALL_RICS, token)

    if "RIC" not in df.columns or len(df) == 0:
        print("\n  >> No data returned for any RIC.")
        return

    df["Trade Date"] = pd.to_datetime(df["Trade Date"], errors="coerce")
    for col in ["Universal Close Price", "Volume", "Open Interest",
                "Settlement Price", "Last", "Bid", "Ask"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    print(f"  Total rows: {len(df):,}")
    print(f"  Columns: {list(df.columns)}")

    # ── Underlying price series ──────────────────────────────────────────────
    und_df = df[df["RIC"] == UNDERLYING_RIC].copy().sort_values("Trade Date")
    und_df["_price"] = und_df.apply(_resolve_price, axis=1)
    und_prices = und_df.dropna(subset=["_price"]).set_index("Trade Date")["_price"]

    if und_prices.empty:
        print(f"\n  WARNING: No price data for underlying {UNDERLYING_RIC}")
    else:
        print(f"\n  Underlying ({UNDERLYING_RIC}): {len(und_prices)} days, "
              f"{und_prices.index[0].date()} to {und_prices.index[-1].date()}, "
              f"last = {und_prices.iloc[-1]:.4f}")

    # ── Per-RIC summary ──────────────────────────────────────────────────────
    print(f"\n  {'RIC':<14} {'Rows':>5} {'Date Range':<25} {'Last Settle':>12} "
          f"{'Last OI':>10} {'Last Vol':>10} {'Price Fill':>10}")
    print(f"  {'-'*12}  {'-'*5} {'-'*24} {'-'*12} {'-'*10} {'-'*10} {'-'*10}")

    ric_data = {}  # ric -> {last_price, last_date, ...}

    for ric in ALL_RICS:
        sub = df[df["RIC"] == ric].copy().sort_values("Trade Date")
        n = len(sub)
        if n == 0:
            print(f"  {ric:<14} {0:>5} {'—':<25} {'—':>12} {'—':>10} {'—':>10} {'—':>10}")
            continue

        dates = sub["Trade Date"].dropna()
        d_range = f"{dates.min().date()} to {dates.max().date()}" if not dates.empty else "—"

        sub["_price"] = sub.apply(_resolve_price, axis=1)
        priced = sub.dropna(subset=["_price"])
        n_priced = len(priced)
        last_price = priced["_price"].iloc[-1] if not priced.empty else None
        last_date = priced["Trade Date"].iloc[-1] if not priced.empty else None

        last_oi = None
        if "Open Interest" in sub.columns:
            oi_vals = sub["Open Interest"].dropna()
            oi_vals = oi_vals[oi_vals > 0]
            if not oi_vals.empty:
                last_oi = int(oi_vals.iloc[-1])

        last_vol = None
        if "Volume" in sub.columns:
            vol_vals = sub["Volume"].dropna()
            vol_vals = vol_vals[vol_vals > 0]
            if not vol_vals.empty:
                last_vol = int(vol_vals.iloc[-1])

        lp_str = f"{last_price:.4f}" if last_price else "—"
        oi_str = f"{last_oi:,}" if last_oi else "—"
        vol_str = f"{last_vol:,}" if last_vol else "—"
        fill_str = f"{n_priced}/{n}"

        print(f"  {ric:<14} {n:>5} {d_range:<25} {lp_str:>12} {oi_str:>10} {vol_str:>10} {fill_str:>10}")

        ric_data[ric] = {
            "last_price": last_price,
            "last_date": last_date,
            "last_oi": last_oi,
            "last_vol": last_vol,
            "n_priced": n_priced,
        }

    # ── Greeks computation ───────────────────────────────────────────────────
    print(f"\n{'='*70}")
    print("  Computing Black-Scholes Greeks")
    print(f"  r = {R_FREE*100:.1f}%  |  IV solved via Brent's method [0.001, 5.0]")
    print(f"{'='*70}")

    greeks_rows = []

    for ric in OPTION_RICS:
        meta = _parse_ric(ric)
        rd = ric_data.get(ric, {})

        if not meta or not rd.get("last_price") or not rd.get("last_date"):
            greeks_rows.append({
                "ric": ric, "cp": meta["cp"] if meta else "?",
                "strike": meta["strike"] if meta else 0,
                "expiry": meta["expiry"] if meta else None,
                "settle": None, "oi": rd.get("last_oi"),
                "vol": rd.get("last_vol"),
                "iv": None, "delta": None, "gamma": None,
                "vega": None, "theta": None, "note": "no price data",
            })
            continue

        if rd["n_priced"] < 5:
            greeks_rows.append({
                "ric": ric, "cp": meta["cp"], "strike": meta["strike"],
                "expiry": meta["expiry"], "settle": rd["last_price"],
                "oi": rd.get("last_oi"), "vol": rd.get("last_vol"),
                "iv": None, "delta": None, "gamma": None,
                "vega": None, "theta": None,
                "note": f"<5 priced days ({rd['n_priced']})",
            })
            continue

        opt_price = rd["last_price"]  # in cents/lb
        last_dt = rd["last_date"]
        last_d = last_dt.date() if hasattr(last_dt, "date") else last_dt

        # Get underlying price for the same date
        S = None
        if not und_prices.empty:
            # Find nearest date
            idx = und_prices.index.get_indexer([last_dt], method="nearest")
            if idx[0] >= 0:
                S = und_prices.iloc[idx[0]]

        if S is None or S <= 0:
            greeks_rows.append({
                "ric": ric, "cp": meta["cp"], "strike": meta["strike"],
                "expiry": meta["expiry"], "settle": opt_price,
                "oi": rd.get("last_oi"), "vol": rd.get("last_vol"),
                "iv": None, "delta": None, "gamma": None,
                "vega": None, "theta": None,
                "note": "no underlying price",
            })
            continue

        K = meta["strike"]  # cents/lb
        T = (meta["expiry"] - last_d).days / 365.0 if meta["expiry"] else None

        if T is None or T <= 0:
            greeks_rows.append({
                "ric": ric, "cp": meta["cp"], "strike": K,
                "expiry": meta["expiry"], "settle": opt_price,
                "oi": rd.get("last_oi"), "vol": rd.get("last_vol"),
                "iv": None, "delta": None, "gamma": None,
                "vega": None, "theta": None,
                "note": "expired or no expiry",
            })
            continue

        # All values in cents/lb — consistent units
        iv = implied_vol(opt_price, S, K, T, R_FREE, meta["is_call"])

        if iv is None:
            greeks_rows.append({
                "ric": ric, "cp": meta["cp"], "strike": K,
                "expiry": meta["expiry"], "settle": opt_price,
                "oi": rd.get("last_oi"), "vol": rd.get("last_vol"),
                "iv": None, "delta": None, "gamma": None,
                "vega": None, "theta": None,
                "note": "IV solve failed",
            })
            continue

        g = bs_greeks(S, K, T, R_FREE, iv, meta["is_call"])
        greeks_rows.append({
            "ric": ric, "cp": meta["cp"], "strike": K,
            "expiry": meta["expiry"], "settle": opt_price,
            "oi": rd.get("last_oi"), "vol": rd.get("last_vol"),
            "iv": iv, "delta": g["delta"], "gamma": g["gamma"],
            "vega": g["vega"], "theta": g["theta"],
            "note": "",
        })

    # ── Strike ladder tables ─────────────────────────────────────────────────
    # Group by expiry
    expiries = {}
    for row in greeks_rows:
        exp = row["expiry"]
        if exp not in expiries:
            expiries[exp] = []
        expiries[exp].append(row)

    for exp_date in sorted(k for k in expiries if k is not None):
        rows = sorted(expiries[exp_date], key=lambda r: (r["strike"], r["cp"]))
        print(f"\n  -- Expiry: {exp_date} --")
        print(f"  {'RIC':<14} {'Type':<6} {'Strike':>7} {'Settle':>9} "
              f"{'OI':>8} {'Vol':>8} {'IV':>7} {'Delta':>7} {'Gamma':>7} "
              f"{'Vega':>7} {'Theta':>7}  Notes")
        print(f"  {'-'*12}  {'-'*4}  {'-'*7} {'-'*9} {'-'*8} {'-'*8} "
              f"{'-'*7} {'-'*7} {'-'*7} {'-'*7} {'-'*7}  {'-'*15}")

        for r in rows:
            def _f(v, fmt=".4f"):
                return f"{v:{fmt}}" if v is not None else "N/A"

            settle_s = _f(r["settle"], ".4f")
            oi_s = f"{r['oi']:,}" if r["oi"] else "—"
            vol_s = f"{r['vol']:,}" if r["vol"] else "—"
            iv_s = f"{r['iv']*100:.1f}%" if r["iv"] is not None else "N/A"
            delta_s = _f(r["delta"], ".4f")
            gamma_s = _f(r["gamma"], ".4f")
            vega_s = _f(r["vega"], ".4f")
            theta_s = _f(r["theta"], ".4f")

            print(f"  {r['ric']:<14} {r['cp']:<6} {r['strike']:>7.1f} {settle_s:>9} "
                  f"{oi_s:>8} {vol_s:>8} {iv_s:>7} {delta_s:>7} {gamma_s:>7} "
                  f"{vega_s:>7} {theta_s:>7}  {r.get('note', '')}")

    print(f"\n{'='*70}")
    print("  Diagnostic complete.")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    main()