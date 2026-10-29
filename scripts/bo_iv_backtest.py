"""
CBOT Soybean Oil — ATM IV Backtest (Call vs Put Skew + IV vs RV).
Rolling 2nd expiry, Mar 2024 – Mar 2026.
"""

import sys
import time
import math
from datetime import date, timedelta
from pathlib import Path
from collections import OrderedDict

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np
import pandas as pd
import requests
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from scipy.optimize import brentq
from scipy.stats import norm

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
from scripts.auth import get_token

EXTRACT_URL = "https://selectapi.datascope.lseg.com/RestApi/v1/Extractions/ExtractWithNotes"
EOD_FIELDS = ["RIC", "Trade Date", "Universal Close Price", "Volume",
              "Open Interest", "Settlement Price", "Last", "Bid", "Ask"]
POLL_INTERVAL = 10
OUT_DIR = Path(__file__).parent
R_FREE = 0.05

# ── Expiry calendar ──────────────────────────────────────────────────────────
# month -> (call_code, put_code)
MONTH_CODES = {
    1: ("A", "M"), 2: ("B", "N"), 3: ("C", "O"), 4: ("D", "P"),
    5: ("E", "Q"), 6: ("F", "R"), 7: ("G", "S"), 8: ("H", "T"),
    9: ("I", "U"), 10: ("J", "V"), 11: ("K", "W"), 12: ("L", "X"),
}

EXPIRY_DATES = OrderedDict([
    ((2024, 4), date(2024, 3, 22)),
    ((2024, 6), date(2024, 5, 17)),
    ((2024, 8), date(2024, 7, 19)),
    ((2024, 10), date(2024, 9, 20)),
    ((2024, 12), date(2024, 11, 22)),
    ((2025, 2), date(2025, 1, 17)),
    ((2025, 4), date(2025, 3, 21)),
    ((2025, 6), date(2025, 5, 16)),
    ((2025, 8), date(2025, 7, 18)),
    ((2025, 10), date(2025, 9, 19)),
    ((2025, 12), date(2025, 11, 21)),
    ((2026, 2), date(2026, 1, 16)),
    ((2026, 4), date(2026, 3, 20)),
    ((2026, 6), date(2026, 5, 14)),
])

# 2nd expiry mapping: from calendar month -> delivery month
# Jan/Feb->Apr, Mar/Apr->Jun, May/Jun->Aug, Jul/Aug->Oct, Sep/Oct->Dec, Nov/Dec->Feb+1
SECOND_EXPIRY_MAP = {
    1: 4, 2: 4, 3: 6, 4: 6, 5: 8, 6: 8,
    7: 10, 8: 10, 9: 12, 10: 12, 11: 2, 12: 2,
}


def get_active_expiry(dt: date) -> tuple:
    """Return (year, delivery_month) for the 2nd expiry active on dt."""
    m = dt.month
    del_month = SECOND_EXPIRY_MAP[m]
    year = dt.year
    if del_month == 2 and m >= 11:
        year += 1

    # Check DTE — if < 30 days to this expiry, roll to next
    exp_date = EXPIRY_DATES.get((year, del_month))
    if exp_date and (exp_date - dt).days < 30:
        # Roll forward to next bimonthly
        keys = list(EXPIRY_DATES.keys())
        idx = keys.index((year, del_month)) if (year, del_month) in keys else -1
        if idx >= 0 and idx + 1 < len(keys):
            return keys[idx + 1]
    return (year, del_month)


def encode_strike(strike: float) -> str:
    val = strike * 10
    if val == int(val):
        return str(int(val))
    return str(int(round(strike * 100)))


def build_ric(strike: float, month_code: str, year2: int) -> str:
    return f"BO{encode_strike(strike)}{month_code}{year2}"


# ── API ──────────────────────────────────────────────────────────────────────

def _headers(token):
    return {"Authorization": f"Token {token}",
            "Content-Type": "application/json",
            "Prefer": "respond-async"}


def _poll(loc, token):
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


def extract_eod(rics, token, start, end, allow_historical=False):
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
        data = _poll(loc, token)
    elif resp.status_code == 200:
        data = resp.json()
    else:
        try:
            msg = resp.json()["error"]["message"]
        except Exception:
            msg = resp.text[:500]
        raise RuntimeError(f"EOD failed ({resp.status_code}): {msg}")
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
    if T <= 0 or mkt <= 0.01 or np.isnan(mkt):
        return np.nan
    try:
        return brentq(lambda sig: bs_price(S, K, T, r, sig, is_call) - mkt,
                      0.001, 5.0, xtol=1e-6, maxiter=200)
    except (ValueError, RuntimeError):
        return np.nan


# ══════════════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════════════

def main():
    token = get_token()

    START = date(2024, 3, 1)
    END = date.today()

    print(f"\n{'='*70}")
    print(f"  CBOT Soybean Oil — ATM IV Backtest")
    print(f"  Window: {START} to {END}")
    print(f"{'='*70}")

    # ── STEP 1: Pull BOc3 ────────────────────────────────────────────────────
    print(f"\n  Step 1a: Pulling BOc3...")
    boc3_raw = extract_eod(["BOc3"], token, START, END, allow_historical=True)
    boc3_raw["Trade Date"] = pd.to_datetime(boc3_raw["Trade Date"], errors="coerce")
    for c in ["Universal Close Price", "Settlement Price", "Last", "Bid", "Ask"]:
        if c in boc3_raw.columns:
            boc3_raw[c] = pd.to_numeric(boc3_raw[c], errors="coerce")
    boc3_raw["_price"] = boc3_raw.apply(resolve_price, axis=1)
    boc3 = boc3_raw.dropna(subset=["_price"]).set_index("Trade Date")["_price"].sort_index()
    print(f"    BOc3: {len(boc3)} days, {boc3.index[0].date()} to {boc3.index[-1].date()}")

    # ── Build per-day schedule: active expiry + ATM strike + RICs needed ─────
    print(f"\n  Step 1b: Building daily schedule...")
    schedule = []  # list of dicts per trading day
    all_call_rics = set()
    all_put_rics = set()
    roll_dates = []
    prev_expiry_key = None

    for dt in boc3.index:
        d = dt.date()
        S = boc3.loc[dt]
        atm = round(S * 2) / 2  # nearest 0.5

        ey, em = get_active_expiry(d)
        exp_date = EXPIRY_DATES.get((ey, em))
        if exp_date is None:
            continue

        # Track rolls
        ek = (ey, em)
        if prev_expiry_key is not None and ek != prev_expiry_key:
            roll_dates.append(dt)
        prev_expiry_key = ek

        yr2 = ey % 100
        call_code = MONTH_CODES[em][0]
        put_code = MONTH_CODES[em][1]
        c_ric = build_ric(atm, call_code, yr2)
        p_ric = build_ric(atm, put_code, yr2)
        all_call_rics.add(c_ric)
        all_put_rics.add(p_ric)

        schedule.append({
            "date": dt, "S": S, "atm": atm,
            "exp_year": ey, "exp_month": em, "exp_date": exp_date,
            "call_ric": c_ric, "put_ric": p_ric,
            "T": (exp_date - d).days / 365.0,
        })

    print(f"    Trading days: {len(schedule)}")
    print(f"    Roll dates: {len(roll_dates)}")
    print(f"    Unique call RICs: {len(all_call_rics)}")
    print(f"    Unique put RICs: {len(all_put_rics)}")

    # ── Pull all option RICs in one batch ────────────────────────────────────
    all_opt_rics = sorted(all_call_rics | all_put_rics)
    print(f"\n  Step 1c: Pulling {len(all_opt_rics)} option RICs...")
    opt_raw = extract_eod(all_opt_rics, token, START, END, allow_historical=True)
    opt_raw["Trade Date"] = pd.to_datetime(opt_raw["Trade Date"], errors="coerce")
    for c in ["Universal Close Price", "Settlement Price", "Last", "Bid", "Ask",
              "Volume", "Open Interest"]:
        if c in opt_raw.columns:
            opt_raw[c] = pd.to_numeric(opt_raw[c], errors="coerce")
    opt_raw["_price"] = opt_raw.apply(resolve_price, axis=1)
    print(f"    Option rows: {len(opt_raw):,}")
    print(f"    Unique RICs with data: {opt_raw['RIC'].nunique()}")

    # Build lookup: (ric, date) -> price
    opt_lookup = {}
    for _, row in opt_raw.iterrows():
        ric = row["RIC"]
        dt = row["Trade Date"]
        p = row["_price"]
        if pd.notna(p) and pd.notna(dt):
            opt_lookup[(ric, dt)] = p

    # ── STEP 2: Compute IV ───────────────────────────────────────────────────
    print(f"\n  Step 2: Computing ATM IV for each day...")
    records = []
    for entry in schedule:
        dt = entry["date"]
        S = entry["S"]
        K = entry["atm"]
        T = entry["T"]
        if T <= 0:
            continue

        c_price = opt_lookup.get((entry["call_ric"], dt), np.nan)
        p_price = opt_lookup.get((entry["put_ric"], dt), np.nan)

        call_iv = iv_solve(c_price, S, K, T, R_FREE, True)
        put_iv = iv_solve(p_price, S, K, T, R_FREE, False)

        records.append({
            "Date": dt, "S": S, "ATM": K, "T": T,
            "ExpMonth": entry["exp_month"], "ExpYear": entry["exp_year"],
            "CallRIC": entry["call_ric"], "PutRIC": entry["put_ric"],
            "CallSettle": c_price, "PutSettle": p_price,
            "CallIV": call_iv * 100 if not np.isnan(call_iv) else np.nan,
            "PutIV": put_iv * 100 if not np.isnan(put_iv) else np.nan,
        })

    df = pd.DataFrame(records).set_index("Date").sort_index()

    # Average IV and skew
    df["MidIV"] = (df["CallIV"] + df["PutIV"]) / 2
    df["Skew"] = df["PutIV"] - df["CallIV"]

    # RV30
    log_ret = np.log(boc3 / boc3.shift(1)).dropna()
    rv30 = log_ret.rolling(30).std() * np.sqrt(252) * 100
    df["RV30"] = rv30.reindex(df.index)

    # IV premium
    df["IVPremium"] = df["MidIV"] - df["RV30"]

    # Stats
    valid = df.dropna(subset=["CallIV", "PutIV"])
    mean_civ = valid["CallIV"].mean()
    mean_piv = valid["PutIV"].mean()
    mean_skew = valid["Skew"].mean()
    mean_prem = df["IVPremium"].dropna().mean()

    print(f"    Days with valid IV: {len(valid)} / {len(df)}")
    print(f"    Mean Call IV: {mean_civ:.1f}% | Mean Put IV: {mean_piv:.1f}%")
    print(f"    Mean Skew (Put-Call): {mean_skew:+.1f} pts")
    print(f"    Mean IV Premium vs RV30: {mean_prem:+.1f}%")

    # ── STEP 3: Plot 1 — Call IV vs Put IV vs BOc3 ──────────────────────────
    print(f"\n  Step 3: Generating bo_call_put_skew.png...")

    fig, (ax1, ax2, ax3) = plt.subplots(3, 1, figsize=(14, 10), sharex=True,
                                         gridspec_kw={"height_ratios": [40, 35, 25]})
    fig.suptitle("CBOT Soybean Oil -- ATM Call vs Put IV | Rolling 2nd Expiry | "
                 "Mar 2024-Mar 2026", fontsize=13, fontweight="bold", y=0.98)

    # Panel 1: BOc3
    ax1.plot(df.index, df["S"], color="black", linewidth=1.2)
    ax1.set_ylabel("BOc3 (c/lb)", fontsize=10)
    ax1.grid(True, alpha=0.2)
    for rd in roll_dates:
        ax1.axvline(rd, color="grey", linestyle="--", linewidth=0.6, alpha=0.5)
        ek = get_active_expiry(rd.date())
        mn = ["","Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
        lbl = f"{mn[ek[1]]}{str(ek[0])[-2:]}"
        ax1.text(rd, ax1.get_ylim()[1], lbl, fontsize=6, ha="center", va="bottom",
                 color="grey", rotation=90)

    # Panel 2: Call IV vs Put IV
    civ = df["CallIV"].dropna()
    piv = df["PutIV"].dropna()
    ax2.plot(civ.index, civ, color="blue", linewidth=1.1, label="Call IV")
    ax2.plot(piv.index, piv, color="red", linewidth=1.1, label="Put IV")

    # Fill between
    common = df.dropna(subset=["CallIV", "PutIV"])
    ax2.fill_between(common.index, common["CallIV"], common["PutIV"],
                     where=common["PutIV"] > common["CallIV"],
                     color="red", alpha=0.12, label="Put skew")
    ax2.fill_between(common.index, common["CallIV"], common["PutIV"],
                     where=common["CallIV"] > common["PutIV"],
                     color="blue", alpha=0.12, label="Call skew")
    ax2.set_ylabel("Implied Volatility (%)", fontsize=10)
    ax2.legend(loc="upper left", fontsize=8)
    ax2.grid(True, alpha=0.2)
    for rd in roll_dates:
        ax2.axvline(rd, color="grey", linestyle="--", linewidth=0.6, alpha=0.3)

    # Current values annotation
    last_valid = valid.iloc[-1] if len(valid) > 0 else None
    if last_valid is not None:
        txt = (f"Call IV: {last_valid['CallIV']:.1f}%\n"
               f"Put IV: {last_valid['PutIV']:.1f}%\n"
               f"Skew: {last_valid['Skew']:+.1f} pts")
        ax2.text(0.98, 0.95, txt, transform=ax2.transAxes, fontsize=8,
                 verticalalignment="top", horizontalalignment="right",
                 bbox=dict(boxstyle="round,pad=0.3", facecolor="lightyellow",
                           edgecolor="grey", alpha=0.9))

    # Panel 3: Skew bars
    skew = df["Skew"].dropna()
    colors = ["red" if v > 0 else "blue" for v in skew]
    ax3.bar(skew.index, skew, color=colors, width=1.5, alpha=0.7)
    ax3.axhline(0, color="black", linewidth=0.8)
    ax3.set_ylabel("Skew (IV points)", fontsize=10)
    ax3.grid(True, alpha=0.2)
    ax3.xaxis.set_major_formatter(mdates.DateFormatter("%b\n%Y"))
    ax3.xaxis.set_major_locator(mdates.MonthLocator(interval=2))

    fig.tight_layout(rect=[0, 0, 1, 0.96])
    p1 = OUT_DIR / "bo_call_put_skew.png"
    fig.savefig(str(p1), dpi=150)
    plt.close(fig)
    print(f"    Saved: {p1}")

    # ── STEP 4: Plot 2 — IV vs RV vs BOc3 ───────────────────────────────────
    print(f"\n  Step 4: Generating bo_iv_rv_spread.png...")

    fig, (ax1, ax2, ax3) = plt.subplots(3, 1, figsize=(14, 10), sharex=True,
                                         gridspec_kw={"height_ratios": [40, 35, 25]})
    fig.suptitle("CBOT Soybean Oil -- Implied vs Realised Vol | Rolling 2nd Expiry | "
                 "Mar 2024-Mar 2026", fontsize=13, fontweight="bold", y=0.98)

    # Panel 1: BOc3
    ax1.plot(df.index, df["S"], color="black", linewidth=1.2)
    ax1.set_ylabel("BOc3 (c/lb)", fontsize=10)
    ax1.grid(True, alpha=0.2)
    for rd in roll_dates:
        ax1.axvline(rd, color="grey", linestyle="--", linewidth=0.6, alpha=0.5)
        ek = get_active_expiry(rd.date())
        mn = ["","Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
        lbl = f"{mn[ek[1]]}{str(ek[0])[-2:]}"
        ax1.text(rd, ax1.get_ylim()[1], lbl, fontsize=6, ha="center", va="bottom",
                 color="grey", rotation=90)

    # Panel 2: Mid IV vs RV30
    mid_iv = df["MidIV"].dropna()
    rv = df["RV30"].dropna()
    ax2.plot(mid_iv.index, mid_iv, color="purple", linewidth=1.1, label="ATM IV (mid)")
    ax2.plot(rv.index, rv, color="orange", linewidth=1.1, label="RV30")
    ax2.set_ylabel("Volatility (%)", fontsize=10)
    ax2.legend(loc="upper left", fontsize=8)
    ax2.grid(True, alpha=0.2)
    for rd in roll_dates:
        ax2.axvline(rd, color="grey", linestyle="--", linewidth=0.6, alpha=0.3)

    # Current values annotation
    last_mid = df["MidIV"].dropna().iloc[-1] if not df["MidIV"].dropna().empty else np.nan
    last_rv = df["RV30"].dropna().iloc[-1] if not df["RV30"].dropna().empty else np.nan
    txt2 = (f"ATM IV: {last_mid:.1f}%\n"
            f"RV30: {last_rv:.1f}%\n"
            f"Premium: {last_mid - last_rv:+.1f}%")
    ax2.text(0.98, 0.95, txt2, transform=ax2.transAxes, fontsize=8,
             verticalalignment="top", horizontalalignment="right",
             bbox=dict(boxstyle="round,pad=0.3", facecolor="lightyellow",
                       edgecolor="grey", alpha=0.9))

    # Panel 3: IV premium
    prem = df["IVPremium"].dropna()
    ax3.fill_between(prem.index, 0, prem,
                     where=prem >= 0, color="green", alpha=0.4, label="IV rich")
    ax3.fill_between(prem.index, 0, prem,
                     where=prem < 0, color="red", alpha=0.4, label="IV cheap")
    ax3.axhline(0, color="black", linewidth=0.8)
    ax3.axhline(mean_prem, color="grey", linestyle="--", linewidth=0.8)
    ax3.text(prem.index[-1], mean_prem, f" Mean: {mean_prem:+.1f}%",
             fontsize=7, va="bottom", color="grey")
    ax3.set_ylabel("IV Premium (vol pts)", fontsize=10)
    ax3.legend(loc="upper left", fontsize=8)
    ax3.grid(True, alpha=0.2)
    ax3.xaxis.set_major_formatter(mdates.DateFormatter("%b\n%Y"))
    ax3.xaxis.set_major_locator(mdates.MonthLocator(interval=2))

    fig.tight_layout(rect=[0, 0, 1, 0.96])
    p2 = OUT_DIR / "bo_iv_rv_spread.png"
    fig.savefig(str(p2), dpi=150)
    plt.close(fig)
    print(f"    Saved: {p2}")

    # ── Final summary ────────────────────────────────────────────────────────
    d0 = df.index[0].date()
    d1 = df.index[-1].date()
    print(f"\n{'='*70}")
    print(f"  Data: {d0} to {d1} | Trading days: {len(df)} | "
          f"Roll dates: {len(roll_dates)} | Mean Call IV: {mean_civ:.1f}% | "
          f"Mean Put IV: {mean_piv:.1f}% | Mean Skew: {mean_skew:+.1f} pts | "
          f"Mean IV Premium vs RV30: {mean_prem:+.1f}% | Both plots saved")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    main()