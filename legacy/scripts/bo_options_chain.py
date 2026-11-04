"""
CBOT Soybean Oil — Full Jun 2026 Options Chain + Greeks + Visualisations.
Uses ElektronTimeseriesExtractionRequest / ExtractWithNotes.
"""

import sys
import time
import math
from datetime import date, timedelta
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np
import pandas as pd
import requests
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
from matplotlib.patches import FancyBboxPatch
from scipy.optimize import brentq
from scipy.stats import norm

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from scripts.auth import get_token

EXTRACT_URL = "https://selectapi.datascope.lseg.com/RestApi/v1/Extractions/ExtractWithNotes"
FIELDS = ["RIC", "Trade Date", "Universal Close Price", "Volume", "Open Interest",
          "Settlement Price", "Last", "Bid", "Ask"]
POLL_INTERVAL = 10

OUT_DIR = Path(__file__).parent

# ── Config ───────────────────────────────────────────────────────────────────
EXPIRY = date(2026, 5, 14)
R_FREE = 0.05
CALL_CODE = "F"
PUT_CODE = "R"
UNDERLYING_RICS = ["BOc2", "BOc1"]  # prefer c2 (Jun future), fallback c1

STRIKES = [round(50.0 + i * 0.5, 1) for i in range(61)]  # 50.0 to 80.0

# ── RULES2 codes ─────────────────────────────────────────────────────────────
DELIVERY_CODES = {1: "F", 2: "G", 3: "H", 4: "J", 5: "K", 6: "M",
                  7: "N", 8: "Q", 9: "U", 10: "V", 11: "X", 12: "Z"}
CALL_CODES = {1: "A", 2: "B", 3: "C", 4: "D", 5: "E", 6: "F",
              7: "G", 8: "H", 9: "I", 10: "J", 11: "K", 12: "L"}
PUT_CODES = {1: "M", 2: "N", 3: "O", 4: "P", 5: "Q", 6: "R",
             7: "S", 8: "T", 9: "U", 10: "V", 11: "W", 12: "X"}

# Approximate option expiry dates (3rd Friday before delivery month, roughly)
# These are standard CME BO option expiry dates
EXPIRY_DATES = {
    (2026, 4): date(2026, 3, 13), (2026, 5): date(2026, 4, 14),
    (2026, 6): date(2026, 5, 14), (2026, 7): date(2026, 6, 12),
    (2026, 8): date(2026, 7, 14), (2026, 9): date(2026, 8, 14),
    (2026, 10): date(2026, 9, 14), (2026, 11): date(2026, 10, 14),
    (2026, 12): date(2026, 11, 13),
    (2027, 1): date(2026, 12, 14), (2027, 2): date(2027, 1, 14),
    (2027, 3): date(2027, 2, 12), (2027, 4): date(2027, 3, 12),
    (2027, 5): date(2027, 4, 14), (2027, 6): date(2027, 5, 14),
    (2027, 7): date(2027, 6, 14), (2027, 8): date(2027, 7, 14),
    (2027, 9): date(2027, 8, 13), (2027, 10): date(2027, 9, 14),
    (2027, 11): date(2027, 10, 14), (2027, 12): date(2027, 11, 12),
}


# ══════════════════════════════════════════════════════════════════════════════
# RIC construction
# ══════════════════════════════════════════════════════════════════════════════

def encode_strike(strike: float) -> str:
    """50.0 -> '500', 63.5 -> '635', 63.25 -> '6325'."""
    val = strike * 10
    if val == int(val):
        return str(int(val))
    # quarter-point
    return str(int(round(strike * 100)))


def build_option_ric(strike: float, month_code: str, year: int = 26) -> str:
    return f"BO{encode_strike(strike)}{month_code}{year}"


def parse_strike_from_ric(ric: str) -> float | None:
    """Inverse of build: BO635F26 -> 63.5."""
    if not ric.startswith("BO") or len(ric) < 6:
        return None
    body = ric[2:-3]  # strip 'BO' and month+year
    try:
        v = int(body)
        return v / 10.0 if len(body) <= 3 else v / 100.0
    except ValueError:
        return None


# ══════════════════════════════════════════════════════════════════════════════
# Black-Scholes
# ══════════════════════════════════════════════════════════════════════════════

def bs_price(S, K, T, r, sigma, is_call):
    if T <= 0 or sigma <= 0:
        return max(0, (S - K) if is_call else (K - S))
    d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    if is_call:
        return S * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(d2)
    else:
        return K * math.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)


def implied_vol(mkt, S, K, T, r, is_call):
    if T <= 0 or mkt <= 0.01:
        return None
    try:
        return brentq(lambda sig: bs_price(S, K, T, r, sig, is_call) - mkt,
                      0.001, 5.0, xtol=1e-6, maxiter=200)
    except (ValueError, RuntimeError):
        return None


def bs_greeks(S, K, T, r, sigma, is_call):
    if T <= 0 or sigma <= 0:
        return {"delta": np.nan, "gamma": np.nan, "vega": np.nan, "theta": np.nan}
    sqT = math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * sqT)
    d2 = d1 - sigma * sqT
    delta = norm.cdf(d1) if is_call else norm.cdf(d1) - 1
    gamma = norm.pdf(d1) / (S * sigma * sqT)
    vega = S * norm.pdf(d1) * sqT / 100.0  # per 1% vol
    t1 = -(S * norm.pdf(d1) * sigma) / (2 * sqT)
    t2 = -r * K * math.exp(-r * T) * norm.cdf(d2) if is_call \
        else r * K * math.exp(-r * T) * norm.cdf(-d2)
    theta = (t1 + t2) / 365.0
    return {"delta": delta, "gamma": gamma, "vega": vega, "theta": theta}


# ══════════════════════════════════════════════════════════════════════════════
# API
# ══════════════════════════════════════════════════════════════════════════════

def _headers(token):
    return {"Authorization": f"Token {token}",
            "Content-Type": "application/json",
            "Prefer": "respond-async"}


def _extract(rics, token):
    end = date.today()
    start = end - timedelta(days=30)
    body = {
        "ExtractionRequest": {
            "@odata.type": ("#DataScope.Select.Api.Extractions.ExtractionRequests"
                            ".ElektronTimeseriesExtractionRequest"),
            "ContentFieldNames": FIELDS,
            "IdentifierList": {
                "@odata.type": ("#DataScope.Select.Api.Extractions.ExtractionRequests"
                                ".InstrumentIdentifierList"),
                "InstrumentIdentifiers": [
                    {"Identifier": r, "IdentifierType": "Ric"} for r in rics
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

    resp = requests.post(EXTRACT_URL, headers=_headers(token), json=body, timeout=(30, 600))

    if resp.status_code == 202:
        loc = resp.headers.get("Location")
        if not loc:
            raise RuntimeError("202 but no Location header")
        attempt = 0
        while True:
            attempt += 1
            print(f"  Polling... (attempt {attempt})", end="\r", flush=True)
            time.sleep(POLL_INTERVAL)
            r2 = requests.get(loc, headers=_headers(token), timeout=(30, 600))
            if r2.status_code == 200:
                print(f"  Polling... done (attempt {attempt})")
                data = r2.json()
                break
            if r2.status_code != 202:
                raise RuntimeError(f"Poll fail ({r2.status_code}): {r2.text[:300]}")
    elif resp.status_code == 200:
        data = resp.json()
    else:
        try:
            msg = resp.json()["error"]["message"]
        except Exception:
            msg = resp.text[:500]
        raise RuntimeError(f"Request failed ({resp.status_code}): {msg}")

    contents = data.get("Contents", [])
    notes = data.get("Notes", [])
    if notes:
        n = str(notes)[:400]
        print(f"  API Notes: {n}...")

    return pd.DataFrame(contents) if contents else pd.DataFrame(columns=FIELDS)


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


# ══════════════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════════════

def main():
    token = get_token()

    # ── Step 1: Build RIC list ───────────────────────────────────────────────
    call_rics = [build_option_ric(s, CALL_CODE) for s in STRIKES]
    put_rics = [build_option_ric(s, PUT_CODE) for s in STRIKES]
    all_option_rics = call_rics + put_rics
    all_rics = all_option_rics + UNDERLYING_RICS

    print(f"\n{'='*70}")
    print(f"  Jun 2026 CBOT Soybean Oil Full Options Chain")
    print(f"  Strikes: {STRIKES[0]} to {STRIKES[-1]} ({len(STRIKES)} strikes)")
    print(f"  RICs: {len(call_rics)} calls + {len(put_rics)} puts + "
          f"{len(UNDERLYING_RICS)} underlying = {len(all_rics)} total")
    print(f"  Expiry: {EXPIRY}")
    print(f"{'='*70}\n")

    # ── Fetch ────────────────────────────────────────────────────────────────
    print("  Fetching 30 days EOD data...")
    df = _extract(all_rics, token)

    if "RIC" not in df.columns or len(df) == 0:
        print("  >> No data returned.")
        return

    df["Trade Date"] = pd.to_datetime(df["Trade Date"], errors="coerce")
    for c in ["Universal Close Price", "Volume", "Open Interest",
              "Settlement Price", "Last", "Bid", "Ask"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    print(f"  Total rows: {len(df):,}")
    rics_returned = set(df["RIC"].unique())
    opt_returned = rics_returned & set(all_option_rics)
    print(f"  Option RICs with data: {len(opt_returned)} / {len(all_option_rics)}")

    # ── Underlying ───────────────────────────────────────────────────────────
    und_ric = None
    und_series = pd.Series(dtype=float)
    for uric in UNDERLYING_RICS:
        sub = df[df["RIC"] == uric].copy().sort_values("Trade Date")
        sub["_price"] = sub.apply(resolve_price, axis=1)
        s = sub.dropna(subset=["_price"]).set_index("Trade Date")["_price"]
        if not s.empty:
            und_ric = uric
            und_series = s
            break

    if und_series.empty:
        print("  >> No underlying price data.")
        return

    S_last = und_series.iloc[-1]
    last_date = und_series.index[-1]
    T = (EXPIRY - last_date.date()).days / 365.0
    print(f"  Underlying: {und_ric} = {S_last:.4f} (as of {last_date.date()})")
    print(f"  T = {T:.4f} years ({(EXPIRY - last_date.date()).days} days)")

    # ATM strike
    atm_strike = min(STRIKES, key=lambda s: abs(s - S_last))
    print(f"  ATM strike: {atm_strike}")

    # ── Build chain data ─────────────────────────────────────────────────────
    chain = {}  # strike -> {call: {...}, put: {...}}

    for strike in STRIKES:
        chain[strike] = {"call": None, "put": None}

        for cp, code in [("call", CALL_CODE), ("put", PUT_CODE)]:
            ric = build_option_ric(strike, code)
            sub = df[df["RIC"] == ric].copy().sort_values("Trade Date")

            if sub.empty:
                continue

            sub["_price"] = sub.apply(resolve_price, axis=1)

            # Last date row
            last_row = sub[sub["Trade Date"] == last_date]
            if last_row.empty:
                last_row = sub.dropna(subset=["_price"]).tail(1)
            if last_row.empty:
                continue

            lr = last_row.iloc[-1]
            settle = lr["_price"] if pd.notna(lr.get("_price")) else resolve_price(lr)
            oi = lr.get("Open Interest")
            vol = lr.get("Volume")
            oi = int(oi) if pd.notna(oi) and oi > 0 else None
            vol = int(vol) if pd.notna(vol) and vol > 0 else None

            is_call = (cp == "call")
            intrinsic = max(0, (S_last - strike) if is_call else (strike - S_last))
            tv = (settle - intrinsic) if pd.notna(settle) and settle > 0 else None

            iv = implied_vol(settle, S_last, strike, T, R_FREE, is_call) \
                if pd.notna(settle) and settle > 0.01 else None
            greeks = bs_greeks(S_last, strike, T, R_FREE, iv, is_call) \
                if iv is not None else \
                {"delta": np.nan, "gamma": np.nan, "vega": np.nan, "theta": np.nan}

            chain[strike][cp] = {
                "ric": ric,
                "settle": settle if pd.notna(settle) else None,
                "oi": oi, "vol": vol,
                "iv": iv,
                "intrinsic": intrinsic,
                "tv": tv,
                **greeks,
                "series": sub.set_index("Trade Date")["_price"].dropna(),
            }

    # ── Step 3: Print full chain table ───────────────────────────────────────
    print(f"\n{'='*145}")
    print(f"  CBOT Soybean Oil Jun26 Options Chain  |  Underlying: {und_ric} = "
          f"{S_last:.2f}  |  ATM: {atm_strike}  |  T: {T:.3f}y  |  Expiry: {EXPIRY}")
    print(f"{'='*145}")

    hdr = (f"  {'':>3} {'Strike':>6} | {'Settle':>7} {'OI':>7} {'IV':>6} "
           f"{'Delta':>6} {'Gamma':>6} {'Vega':>6} {'Theta':>6} "
           f"|| {'Settle':>7} {'OI':>7} {'IV':>6} "
           f"{'Delta':>6} {'Gamma':>6} {'Vega':>6} {'Theta':>6}")
    print(f"  {'':>3} {'':>6} | {'--- CALLS ---':^51} || {'--- PUTS ---':^51}")
    print(hdr)
    print(f"  {'':>3} {'-'*6}-+-{'-'*51}-++-{'-'*51}")

    # Collect IV data for skew calculation
    call_ivs_atm = []
    put_ivs_atm = []

    for strike in STRIKES:
        c = chain[strike]["call"]
        p = chain[strike]["put"]

        # Markers
        is_atm = (strike == atm_strike)
        c_itm = strike < S_last
        p_itm = strike > S_last
        marker = "***" if is_atm else (" > " if c_itm else "   ")

        def _fmt_side(d, is_put=False):
            if d is None:
                return f"{'--':>7} {'--':>7} {'--':>6} {'--':>6} {'--':>6} {'--':>6} {'--':>6}"
            s = f"{d['settle']:.2f}" if d.get("settle") else "--"
            o = f"{d['oi']:,}" if d.get("oi") else "--"
            iv_s = f"{d['iv']*100:.1f}" if d.get("iv") else "--"
            de = f"{d['delta']:.3f}" if pd.notna(d.get("delta")) else "--"
            ga = f"{d['gamma']:.4f}" if pd.notna(d.get("gamma")) else "--"
            ve = f"{d['vega']:.3f}" if pd.notna(d.get("vega")) else "--"
            th = f"{d['theta']:.3f}" if pd.notna(d.get("theta")) else "--"
            return f"{s:>7} {o:>7} {iv_s:>6} {de:>6} {ga:>6} {ve:>6} {th:>6}"

        # For put marker
        p_marker = "***" if is_atm else (" > " if p_itm else "   ")

        c_str = _fmt_side(c)
        p_str = _fmt_side(p, is_put=True)

        print(f"  {marker} {strike:>6.1f} | {c_str} || {p_str}")

        # Collect ATM-zone IVs (within +/- 3 strikes of ATM)
        if abs(strike - atm_strike) <= 1.5:
            if c and c.get("iv"):
                call_ivs_atm.append(c["iv"])
            if p and p.get("iv"):
                put_ivs_atm.append(p["iv"])

    print(f"{'='*145}")

    # ── Skew calculation ─────────────────────────────────────────────────────
    avg_call_iv = np.mean(call_ivs_atm) * 100 if call_ivs_atm else 0
    avg_put_iv = np.mean(put_ivs_atm) * 100 if put_ivs_atm else 0
    skew = avg_put_iv - avg_call_iv

    # ── Step 4: ATM settlement history plot ──────────────────────────────────
    print("\n  Generating ATM settlement history plot...")

    atm_idx = STRIKES.index(atm_strike)
    nearby_strikes = STRIKES[max(0, atm_idx - 2): atm_idx + 3]

    fig, ax1 = plt.subplots(figsize=(14, 7))
    ax2 = ax1.twinx()

    colors_call = plt.cm.Blues(np.linspace(0.4, 0.9, len(nearby_strikes)))
    colors_put = plt.cm.Reds(np.linspace(0.4, 0.9, len(nearby_strikes)))

    for i, strike in enumerate(nearby_strikes):
        c = chain[strike].get("call")
        p = chain[strike].get("put")
        if c and "series" in c and not c["series"].empty:
            ax1.plot(c["series"].index, c["series"].values,
                     color=colors_call[i], linewidth=1.5,
                     label=f"C {strike:.0f}", marker="o", markersize=3)
        if p and "series" in p and not p["series"].empty:
            ax1.plot(p["series"].index, p["series"].values,
                     color=colors_put[i], linewidth=1.5, linestyle="--",
                     label=f"P {strike:.0f}", marker="s", markersize=3)

    # Underlying on secondary axis
    ax2.plot(und_series.index, und_series.values,
             color="black", linewidth=2, alpha=0.6, label=f"{und_ric}")
    ax2.set_ylabel(f"{und_ric} (cents/lb)", fontsize=11)

    ax1.set_ylabel("Option Settlement (cents/lb)", fontsize=11)
    ax1.set_title("Jun26 BO Options - ATM Zone Settlement History", fontsize=13, fontweight="bold")
    ax1.grid(True, alpha=0.3)

    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, loc="upper left",
               fontsize=8, ncol=3)

    fig.autofmt_xdate()
    fig.tight_layout()
    atm_path = OUT_DIR / "bo_options_atm_history.png"
    fig.savefig(str(atm_path), dpi=150)
    plt.close(fig)
    print(f"  Saved: {atm_path}")

    # ── Step 5: Monthly reference table ──────────────────────────────────────
    print("  Generating monthly reference table...")

    today = date.today()
    atm_enc = encode_strike(atm_strike)

    ref_rows = []
    for year in [2026, 2027]:
        start_m = 4 if year == 2026 else 1
        end_m = 12
        for m in range(start_m, end_m + 1):
            yr2 = year % 100
            del_code = DELIVERY_CODES[m]
            call_code = CALL_CODES[m]
            put_code = PUT_CODES[m]

            month_names = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
                           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
            label = f"{month_names[m]} {year}"
            fut_chain = f"0#BO{del_code}{yr2}+"
            ex_call = f"BO{atm_enc}{call_code}{yr2}"
            ex_put = f"BO{atm_enc}{put_code}{yr2}"

            exp = EXPIRY_DATES.get((year, m))
            dte = (exp - today).days if exp else "?"

            ref_rows.append({
                "Expiry": label,
                "Futures Chain": fut_chain,
                "Call Code": call_code,
                "Put Code": put_code,
                "Example Call": ex_call,
                "Example Put": ex_put,
                "DTE": dte,
                "exp_date": exp,
            })

    # Build matplotlib table figure
    fig, ax = plt.subplots(figsize=(16, 10))
    ax.axis("off")
    ax.set_title("CBOT Soybean Oil (BO) Options - Monthly Reference Table\n"
                 f"ATM Strike: {atm_strike} | Generated: {today}",
                 fontsize=14, fontweight="bold", pad=20)

    col_labels = ["Expiry", "Futures Chain", "Call Code", "Put Code",
                  "Example Call RIC", "Example Put RIC", "Days to Expiry"]
    table_data = []
    row_colors = []

    for r in ref_rows:
        dte = r["DTE"]
        if isinstance(dte, int):
            if dte < 0:
                color = "#D0D0D0"  # grey - expired
            elif dte < 30:
                color = "#FFB3B3"  # red
            elif dte <= 90:
                color = "#FFE0A3"  # amber
            else:
                color = "#B3E6B3"  # green
            dte_str = str(dte)
        else:
            color = "#FFFFFF"
            dte_str = "?"

        table_data.append([
            r["Expiry"], r["Futures Chain"], r["Call Code"],
            r["Put Code"], r["Example Call"], r["Example Put"], dte_str
        ])
        row_colors.append(color)

    tbl = ax.table(cellText=table_data, colLabels=col_labels,
                   cellLoc="center", loc="center")
    tbl.auto_set_font_size(False)
    tbl.set_fontsize(10)
    tbl.scale(1.0, 1.6)

    # Style header
    for j in range(len(col_labels)):
        cell = tbl[0, j]
        cell.set_facecolor("#1F3864")
        cell.set_text_props(color="white", fontweight="bold", fontsize=10)

    # Style data rows
    for i, color in enumerate(row_colors):
        for j in range(len(col_labels)):
            cell = tbl[i + 1, j]
            cell.set_facecolor(color)
            cell.set_edgecolor("#CCCCCC")

    # Adjust column widths
    col_widths = [0.10, 0.14, 0.08, 0.08, 0.16, 0.16, 0.12]
    for j, w in enumerate(col_widths):
        for i in range(len(table_data) + 1):
            tbl[i, j].set_width(w)

    fig.tight_layout()
    ref_path = OUT_DIR / "bo_options_reference_table.png"
    fig.savefig(str(ref_path), dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved: {ref_path}")

    # ── Final status line ────────────────────────────────────────────────────
    print(f"\n  ATM strike: {atm_strike} | Underlying: {S_last:.2f} | "
          f"T: {T:.4f} years | Put skew vs Calls: +{skew:.1f} IV points")
    print()


if __name__ == "__main__":
    main()
