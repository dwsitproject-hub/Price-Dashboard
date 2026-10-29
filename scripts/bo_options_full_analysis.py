"""
CBOT Soybean Oil Options — Full 4-Part Analysis.
Part 1: Re-generate chain + plots
Part 2: Long/Short Buildup OI analysis (last 5 days)
Part 3: Intraday 5-min bars for ATM +/-10% (TickHistory)
Part 4: Historical breadth test (expired contracts)
"""

import sys
import time
import math
import gzip
import io
import json
from datetime import date, timedelta
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
EXTRACT_RAW_URL = "https://selectapi.datascope.lseg.com/RestApi/v1/Extractions/ExtractRaw"
EOD_FIELDS = ["RIC", "Trade Date", "Universal Close Price", "Volume",
              "Open Interest", "Settlement Price", "Last", "Bid", "Ask"]
POLL_INTERVAL = 10
OUT_DIR = Path(__file__).parent

# ── Config ───────────────────────────────────────────────────────────────────
EXPIRY = date(2026, 5, 14)
R_FREE = 0.05
CALL_CODE = "F"
PUT_CODE = "R"
UNDERLYING_RICS = ["BOc2", "BOc1"]
STRIKES = [round(50.0 + i * 0.5, 1) for i in range(61)]  # 50.0 to 80.0

DELIVERY_CODES = {1: "F", 2: "G", 3: "H", 4: "J", 5: "K", 6: "M",
                  7: "N", 8: "Q", 9: "U", 10: "V", 11: "X", 12: "Z"}
CALL_CODES = {1: "A", 2: "B", 3: "C", 4: "D", 5: "E", 6: "F",
              7: "G", 8: "H", 9: "I", 10: "J", 11: "K", 12: "L"}
PUT_CODES = {1: "M", 2: "N", 3: "O", 4: "P", 5: "Q", 6: "R",
             7: "S", 8: "T", 9: "U", 10: "V", 11: "W", 12: "X"}
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
# Helpers
# ══════════════════════════════════════════════════════════════════════════════

def encode_strike(strike: float) -> str:
    val = strike * 10
    if val == int(val):
        return str(int(val))
    return str(int(round(strike * 100)))


def build_ric(strike: float, month_code: str, year: int = 26) -> str:
    return f"BO{encode_strike(strike)}{month_code}{year}"


def parse_strike(ric: str) -> float | None:
    if not ric.startswith("BO") or len(ric) < 6:
        return None
    body = ric[2:-3]
    try:
        v = int(body)
        return v / 10.0 if len(body) <= 3 else v / 100.0
    except ValueError:
        return None


def _headers(token, accept_gzip=False):
    h = {"Authorization": f"Token {token}",
         "Content-Type": "application/json",
         "Prefer": "respond-async"}
    if accept_gzip:
        h["Accept-Encoding"] = "gzip"
    return h


def _poll_json(location, token):
    """Poll until 200, return parsed JSON."""
    attempt = 0
    while True:
        attempt += 1
        print(f"  Polling... (attempt {attempt})", end="\r", flush=True)
        time.sleep(POLL_INTERVAL)
        r = requests.get(location, headers=_headers(token), timeout=(30, 600))
        if r.status_code == 200:
            print(f"  Polling... done (attempt {attempt})")
            return r.json()
        if r.status_code != 202:
            raise RuntimeError(f"Poll fail ({r.status_code}): {r.text[:300]}")


def _poll_raw(location, token):
    """Poll until 200, return raw Response (for gzip/CSV)."""
    attempt = 0
    while True:
        attempt += 1
        print(f"  Polling... (attempt {attempt})", end="\r", flush=True)
        time.sleep(POLL_INTERVAL)
        r = requests.get(location, headers=_headers(token, accept_gzip=True),
                         timeout=(30, 600), stream=True)
        if r.status_code == 200:
            print(f"  Polling... done (attempt {attempt})")
            return r
        if r.status_code != 202:
            raise RuntimeError(f"Poll fail ({r.status_code}): {r.text[:300]}")


def extract_eod(rics, token, start=None, end=None, allow_historical=False):
    """ElektronTimeseriesExtractionRequest via ExtractWithNotes."""
    if end is None:
        end = date.today()
    if start is None:
        start = end - timedelta(days=30)

    validation = None
    if allow_historical:
        validation = {"AllowHistoricalInstruments": True}

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
        raise RuntimeError(f"EOD request failed ({resp.status_code}): {msg}")

    notes = data.get("Notes", [])
    if notes:
        print(f"  API Notes: {str(notes)[:300]}...")
    contents = data.get("Contents", [])
    return pd.DataFrame(contents) if contents else pd.DataFrame(columns=EOD_FIELDS)


def extract_tick(rics, token, query_date, id_type="Ric"):
    """TickHistoryTimeAndSalesExtractionRequest via ExtractRaw."""
    ds = query_date.isoformat()
    body = {
        "ExtractionRequest": {
            "@odata.type": ("#DataScope.Select.Api.Extractions.ExtractionRequests"
                            ".TickHistoryTimeAndSalesExtractionRequest"),
            "ContentFieldNames": [
                "Trade - Price", "Trade - Volume", "Trade - Exchange Time",
            ],
            "IdentifierList": {
                "@odata.type": ("#DataScope.Select.Api.Extractions.ExtractionRequests"
                                ".InstrumentIdentifierList"),
                "InstrumentIdentifiers": [
                    {"Identifier": r, "IdentifierType": id_type} for r in rics
                ],
                "ValidationOptions": {"AllowHistoricalInstruments": True},
                "UseUserPreferencesForValidationOptions": False,
            },
            "Condition": {
                "MessageTimeStampIn": "GmtUtc",
                "ApplyCorrectionsAndCancellations": False,
                "ReportDateRangeType": "Range",
                "TimeRangeMode": "Window",
                "QueryStartDate": f"{ds}T00:00:00.000Z",
                "QueryEndDate": f"{ds}T23:59:00.000Z",
                "DisplaySourceRIC": True,
            },
        }
    }

    resp = requests.post(EXTRACT_RAW_URL, headers=_headers(token), json=body, timeout=(30, 600))
    if resp.status_code == 202:
        loc = resp.headers.get("Location")
        resp2 = _poll_raw(loc, token)
        raw = resp2.content
    elif resp.status_code == 200:
        raw = resp.content
    else:
        try:
            msg = resp.json()["error"]["message"]
        except Exception:
            msg = resp.text[:500]
        raise RuntimeError(f"Tick request failed ({resp.status_code}): {msg}")

    # Parse — may be gzip, may contain JobId requiring result fetch
    try:
        decompressed = gzip.decompress(raw)
    except (gzip.BadGzipFile, OSError):
        decompressed = raw

    # Try JSON first (may have JobId)
    try:
        data = json.loads(decompressed)
        notes = data.get("Notes", [])
        if notes:
            print(f"  Tick Notes: {str(notes)[:300]}...")
        job_id = data.get("JobId")
        if job_id:
            print(f"  JobId: {job_id}, fetching results...")
            res_url = (f"https://selectapi.datascope.lseg.com/RestApi/v1"
                       f"/Extractions/RawExtractionResults('{job_id}')/$value")
            res_resp = requests.get(res_url, headers=_headers(token, accept_gzip=True),
                                    timeout=(30, 600))
            if res_resp.status_code != 200:
                raise RuntimeError(f"Results fetch failed ({res_resp.status_code})")
            raw2 = res_resp.content
            try:
                raw2 = gzip.decompress(raw2)
            except (gzip.BadGzipFile, OSError):
                pass
            csv_text = raw2.decode("utf-8-sig")
            return pd.read_csv(io.StringIO(csv_text))
        # Inline contents
        contents = data.get("Contents", [])
        return pd.DataFrame(contents) if contents else pd.DataFrame()
    except (json.JSONDecodeError, UnicodeDecodeError):
        # Already CSV
        csv_text = decompressed.decode("utf-8-sig")
        return pd.read_csv(io.StringIO(csv_text))


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


# Black-Scholes
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
    vega = S * norm.pdf(d1) * sqT / 100.0
    t1 = -(S * norm.pdf(d1) * sigma) / (2 * sqT)
    t2 = -r * K * math.exp(-r * T) * norm.cdf(d2) if is_call \
        else r * K * math.exp(-r * T) * norm.cdf(-d2)
    theta = (t1 + t2) / 365.0
    return {"delta": delta, "gamma": gamma, "vega": vega, "theta": theta}


# ══════════════════════════════════════════════════════════════════════════════
# PART 1 — Full chain + plots
# ══════════════════════════════════════════════════════════════════════════════

def part1(token):
    print(f"\n{'#'*70}")
    print(f"  PART 1 -- Jun 2026 Full Chain + Plots")
    print(f"{'#'*70}\n")

    call_rics = [build_ric(s, CALL_CODE) for s in STRIKES]
    put_rics = [build_ric(s, PUT_CODE) for s in STRIKES]
    all_option_rics = call_rics + put_rics
    all_rics = all_option_rics + UNDERLYING_RICS

    print(f"  {len(call_rics)} calls + {len(put_rics)} puts + "
          f"{len(UNDERLYING_RICS)} underlying = {len(all_rics)} RICs")

    df = extract_eod(all_rics, token)
    if "RIC" not in df.columns or len(df) == 0:
        print("  >> No data returned.")
        return None, None, None, None

    df["Trade Date"] = pd.to_datetime(df["Trade Date"], errors="coerce")
    for c in ["Universal Close Price", "Volume", "Open Interest",
              "Settlement Price", "Last", "Bid", "Ask"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    print(f"  Total rows: {len(df):,}")

    # Underlying
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

    S_last = und_series.iloc[-1]
    last_date = und_series.index[-1]
    T = (EXPIRY - last_date.date()).days / 365.0
    atm_strike = min(STRIKES, key=lambda s: abs(s - S_last))

    print(f"  Last data date: {last_date.date()}")
    print(f"  Underlying: {und_ric} = {S_last:.4f}")
    print(f"  ATM: {atm_strike} | T: {T:.4f}y ({(EXPIRY - last_date.date()).days}d)")

    # Build chain
    chain = {}
    for strike in STRIKES:
        chain[strike] = {"call": None, "put": None}
        for cp, code in [("call", CALL_CODE), ("put", PUT_CODE)]:
            ric = build_ric(strike, code)
            sub = df[df["RIC"] == ric].copy().sort_values("Trade Date")
            if sub.empty:
                continue
            sub["_price"] = sub.apply(resolve_price, axis=1)
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
            iv = implied_vol(settle, S_last, strike, T, R_FREE, is_call) \
                if pd.notna(settle) and settle > 0.01 else None
            greeks = bs_greeks(S_last, strike, T, R_FREE, iv, is_call) \
                if iv is not None else \
                {"delta": np.nan, "gamma": np.nan, "vega": np.nan, "theta": np.nan}
            chain[strike][cp] = {
                "ric": ric, "settle": settle if pd.notna(settle) else None,
                "oi": oi, "vol": vol, "iv": iv, **greeks,
                "series": sub.set_index("Trade Date")["_price"].dropna(),
            }

    # Print chain table
    print(f"\n{'='*145}")
    print(f"  Jun26 Chain | {und_ric}={S_last:.2f} | ATM={atm_strike} | T={T:.3f}y | Expiry={EXPIRY}")
    print(f"{'='*145}")
    print(f"  {'':>3} {'':>6} | {'--- CALLS ---':^51} || {'--- PUTS ---':^51}")
    print(f"  {'':>3} {'Strike':>6} | {'Settle':>7} {'OI':>7} {'IV':>6} "
          f"{'Delta':>6} {'Gamma':>6} {'Vega':>6} {'Theta':>6} "
          f"|| {'Settle':>7} {'OI':>7} {'IV':>6} "
          f"{'Delta':>6} {'Gamma':>6} {'Vega':>6} {'Theta':>6}")
    print(f"  {'':>3} {'-'*6}-+-{'-'*51}-++-{'-'*51}")

    call_ivs_atm, put_ivs_atm = [], []
    for strike in STRIKES:
        c = chain[strike]["call"]
        p = chain[strike]["put"]
        is_atm = (strike == atm_strike)
        marker = "***" if is_atm else (" > " if strike < S_last else "   ")

        def _f(d):
            if d is None:
                return f"{'--':>7} {'--':>7} {'--':>6} {'--':>6} {'--':>6} {'--':>6} {'--':>6}"
            s = f"{d['settle']:.2f}" if d.get("settle") else "--"
            o = f"{d['oi']:,}" if d.get("oi") else "--"
            i = f"{d['iv']*100:.1f}" if d.get("iv") else "--"
            de = f"{d['delta']:.3f}" if pd.notna(d.get("delta")) else "--"
            ga = f"{d['gamma']:.4f}" if pd.notna(d.get("gamma")) else "--"
            ve = f"{d['vega']:.3f}" if pd.notna(d.get("vega")) else "--"
            th = f"{d['theta']:.3f}" if pd.notna(d.get("theta")) else "--"
            return f"{s:>7} {o:>7} {i:>6} {de:>6} {ga:>6} {ve:>6} {th:>6}"

        print(f"  {marker} {strike:>6.1f} | {_f(c)} || {_f(p)}")
        if abs(strike - atm_strike) <= 1.5:
            if c and c.get("iv"): call_ivs_atm.append(c["iv"])
            if p and p.get("iv"): put_ivs_atm.append(p["iv"])

    print(f"{'='*145}")
    skew = (np.mean(put_ivs_atm)*100 - np.mean(call_ivs_atm)*100) \
        if call_ivs_atm and put_ivs_atm else 0

    # ── ATM history plot ─────────────────────────────────────────────────────
    atm_idx = STRIKES.index(atm_strike)
    nearby = STRIKES[max(0, atm_idx-2): atm_idx+3]
    fig, ax1 = plt.subplots(figsize=(14, 7))
    ax2 = ax1.twinx()
    cc = plt.cm.Blues(np.linspace(0.4, 0.9, len(nearby)))
    cp_colors = plt.cm.Reds(np.linspace(0.4, 0.9, len(nearby)))
    for i, st in enumerate(nearby):
        c = chain[st].get("call")
        p = chain[st].get("put")
        if c and "series" in c and not c["series"].empty:
            ax1.plot(c["series"].index, c["series"], color=cc[i], lw=1.5,
                     label=f"C {st:.0f}", marker="o", ms=3)
        if p and "series" in p and not p["series"].empty:
            ax1.plot(p["series"].index, p["series"], color=cp_colors[i], lw=1.5,
                     ls="--", label=f"P {st:.0f}", marker="s", ms=3)
    ax2.plot(und_series.index, und_series, color="black", lw=2, alpha=0.6, label=und_ric)
    ax2.set_ylabel(f"{und_ric} (cents/lb)", fontsize=11)
    ax1.set_ylabel("Option Settlement (cents/lb)", fontsize=11)
    ax1.set_title("Jun26 BO Options - ATM Zone Settlement History", fontsize=13, fontweight="bold")
    ax1.grid(True, alpha=0.3)
    l1, lb1 = ax1.get_legend_handles_labels()
    l2, lb2 = ax2.get_legend_handles_labels()
    ax1.legend(l1+l2, lb1+lb2, loc="upper left", fontsize=8, ncol=3)
    fig.autofmt_xdate(); fig.tight_layout()
    p1 = OUT_DIR / "bo_options_atm_history.png"
    fig.savefig(str(p1), dpi=150); plt.close(fig)
    print(f"\n  Saved: {p1}")

    # ── Reference table plot ─────────────────────────────────────────────────
    today = date.today()
    atm_enc = encode_strike(atm_strike)
    ref_rows = []
    for year in [2026, 2027]:
        sm = 4 if year == 2026 else 1
        for m in range(sm, 13):
            yr2 = year % 100
            mn = ["","Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"][m]
            exp = EXPIRY_DATES.get((year, m))
            dte = (exp - today).days if exp else "?"
            ref_rows.append([
                f"{mn} {year}", f"0#BO{DELIVERY_CODES[m]}{yr2}+",
                CALL_CODES[m], PUT_CODES[m],
                f"BO{atm_enc}{CALL_CODES[m]}{yr2}",
                f"BO{atm_enc}{PUT_CODES[m]}{yr2}",
                dte
            ])

    fig, ax = plt.subplots(figsize=(16, 10))
    ax.axis("off")
    ax.set_title(f"CBOT Soybean Oil (BO) Options - Monthly Reference Table\n"
                 f"ATM Strike: {atm_strike} | Generated: {today}",
                 fontsize=14, fontweight="bold", pad=20)
    col_labels = ["Expiry", "Futures Chain", "Call Code", "Put Code",
                  "Example Call RIC", "Example Put RIC", "Days to Expiry"]
    tdata = []
    rcolors = []
    for r in ref_rows:
        dte = r[6]
        if isinstance(dte, int):
            clr = "#D0D0D0" if dte < 0 else "#FFB3B3" if dte < 30 \
                else "#FFE0A3" if dte <= 90 else "#B3E6B3"
            r[6] = str(dte)
        else:
            clr = "#FFFFFF"
        tdata.append(r)
        rcolors.append(clr)
    tbl = ax.table(cellText=tdata, colLabels=col_labels, cellLoc="center", loc="center")
    tbl.auto_set_font_size(False); tbl.set_fontsize(10); tbl.scale(1.0, 1.6)
    for j in range(len(col_labels)):
        tbl[0, j].set_facecolor("#1F3864")
        tbl[0, j].set_text_props(color="white", fontweight="bold", fontsize=10)
    for i, clr in enumerate(rcolors):
        for j in range(len(col_labels)):
            tbl[i+1, j].set_facecolor(clr)
            tbl[i+1, j].set_edgecolor("#CCCCCC")
    cw = [0.10, 0.14, 0.08, 0.08, 0.16, 0.16, 0.12]
    for j, w in enumerate(cw):
        for i in range(len(tdata)+1):
            tbl[i, j].set_width(w)
    fig.tight_layout()
    p2 = OUT_DIR / "bo_options_reference_table.png"
    fig.savefig(str(p2), dpi=150, bbox_inches="tight"); plt.close(fig)
    print(f"  Saved: {p2}")

    print(f"\n  ATM: {atm_strike} | Underlying: {S_last:.2f} | T: {T:.4f}y | "
          f"Put skew vs Calls: +{skew:.1f} IV points")

    return df, und_series, atm_strike, last_date


# ══════════════════════════════════════════════════════════════════════════════
# PART 2 — Long/Short Buildup OI Analysis
# ══════════════════════════════════════════════════════════════════════════════

def part2(df, und_series, atm_strike, last_date):
    print(f"\n{'#'*70}")
    print(f"  PART 2 -- OI Buildup / Unwinding Analysis (last 5 days)")
    print(f"{'#'*70}\n")

    # Get last 6 trading dates (need 6 to compute 5 days of changes)
    all_dates = sorted(df["Trade Date"].dropna().unique())
    last_6 = all_dates[-6:]
    analysis_dates = last_6[1:]  # the 5 dates where we can compute delta

    print(f"  Analysis dates: {[d.strftime('%Y-%m-%d') for d in analysis_dates]}")

    # Build per-RIC daily panel
    option_rics = [build_ric(s, CALL_CODE) for s in STRIKES] + \
                  [build_ric(s, PUT_CODE) for s in STRIKES]

    results = []

    for dt_idx, dt in enumerate(analysis_dates):
        prev_dt = last_6[dt_idx]  # one position earlier in last_6

        # Get underlying close for this date
        und_close = None
        if dt in und_series.index:
            und_close = und_series.loc[dt]
        elif len(und_series) > 0:
            idx = und_series.index.get_indexer([dt], method="nearest")
            if idx[0] >= 0:
                und_close = und_series.iloc[idx[0]]

        for cp_label, code in [("Call", CALL_CODE), ("Put", PUT_CODE)]:
            signals = {"LB": [], "SB": [], "LU": [], "SC": [], "Neutral": []}

            for strike in STRIKES:
                ric = build_ric(strike, code)
                sub = df[df["RIC"] == ric].copy()
                sub = sub.set_index("Trade Date")

                if dt not in sub.index or prev_dt not in sub.index:
                    continue

                row_t = sub.loc[dt] if isinstance(sub.loc[dt], pd.Series) \
                    else sub.loc[dt].iloc[-1]
                row_p = sub.loc[prev_dt] if isinstance(sub.loc[prev_dt], pd.Series) \
                    else sub.loc[prev_dt].iloc[-1]

                oi_t = pd.to_numeric(row_t.get("Open Interest"), errors="coerce")
                oi_p = pd.to_numeric(row_p.get("Open Interest"), errors="coerce")
                price_t = resolve_price(row_t)
                price_p = resolve_price(row_p)

                if pd.isna(oi_t) or pd.isna(oi_p) or pd.isna(price_t) or pd.isna(price_p):
                    continue
                if oi_p <= 0 or price_p <= 0:
                    continue

                d_oi = oi_t - oi_p
                d_price = price_t - price_p

                # Neutral threshold: 1% of prior OI
                if abs(d_oi) < 0.01 * oi_p:
                    signals["Neutral"].append((strike, d_oi))
                    continue

                oi_up = d_oi > 0
                price_up = d_price > 0

                if oi_up and price_up:
                    signals["LB"].append((strike, d_oi))
                elif oi_up and not price_up:
                    signals["SB"].append((strike, d_oi))
                elif not oi_up and not price_up:
                    signals["LU"].append((strike, d_oi))
                elif not oi_up and price_up:
                    signals["SC"].append((strike, d_oi))

            for sig_name, items in signals.items():
                if not items:
                    continue
                strikes_arr = [x[0] for x in items]
                doi_arr = [x[1] for x in items]
                total_doi = sum(doi_arr)
                abs_doi = [abs(x) for x in doi_arr]
                woas = sum(s * a for s, a in zip(strikes_arr, abs_doi)) / sum(abs_doi) \
                    if sum(abs_doi) > 0 else 0

                vs_und = ""
                if und_close and woas > 0:
                    diff = woas - und_close
                    otm = "OTM" if ((cp_label == "Call" and diff > 0) or
                                    (cp_label == "Put" and diff < 0)) else "ITM"
                    vs_und = f"{diff:+.1f} ({otm})"

                results.append({
                    "date": dt, "type": cp_label, "signal": sig_name,
                    "n_strikes": len(items), "total_doi": int(total_doi),
                    "woas": woas, "und_close": und_close, "vs_und": vs_und,
                })

    # Print table
    sig_names = {"LB": "Long Build", "SB": "Short Build",
                 "LU": "Long Unwind", "SC": "Short Cover", "Neutral": "Neutral"}

    print(f"\n  {'Date':<12} {'Type':<6} {'Signal':<14} {'#Str':>5} "
          f"{'TotalDOI':>10} {'WOAS':>7} {'Underlying':>11} {'WOAS vs Und':>15}")
    print(f"  {'-'*10}  {'-'*4}  {'-'*12}  {'-'*5} {'-'*10} {'-'*7} {'-'*11} {'-'*15}")

    for r in results:
        if r["signal"] == "Neutral":
            continue  # skip neutral for readability
        dt_str = r["date"].strftime("%b-%d")
        sig_str = sig_names.get(r["signal"], r["signal"])
        doi_str = f"{r['total_doi']:+,}"
        woas_str = f"{r['woas']:.1f}" if r["woas"] > 0 else "--"
        und_str = f"{r['und_close']:.2f}" if r["und_close"] else "--"
        print(f"  {dt_str:<12} {r['type']:<6} {sig_str:<14} {r['n_strikes']:>5} "
              f"{doi_str:>10} {woas_str:>7} {und_str:>11} {r['vs_und']:>15}")

    # ── Interpretation for most recent day ───────────────────────────────────
    latest = analysis_dates[-1]
    latest_results = [r for r in results if r["date"] == latest and r["signal"] != "Neutral"]

    print(f"\n  === Interpretation for {latest.strftime('%Y-%m-%d')} ===")

    call_lb = [r for r in latest_results if r["type"] == "Call" and r["signal"] == "LB"]
    put_lb = [r for r in latest_results if r["type"] == "Put" and r["signal"] == "LB"]
    call_sb = [r for r in latest_results if r["type"] == "Call" and r["signal"] == "SB"]
    put_sb = [r for r in latest_results if r["type"] == "Put" and r["signal"] == "SB"]

    und_close = und_series.iloc[-1]
    bias = "neutral"

    if call_lb:
        cl = call_lb[0]
        if cl["woas"] > und_close:
            print(f"  Call LB: WOAS={cl['woas']:.1f} > Underlying={und_close:.2f} "
                  f"-> market pricing UPSIDE to ~{cl['woas']:.1f}")
    if put_lb:
        pl = put_lb[0]
        if pl["woas"] < und_close:
            print(f"  Put LB:  WOAS={pl['woas']:.1f} < Underlying={und_close:.2f} "
                  f"-> market pricing DOWNSIDE protection to ~{pl['woas']:.1f}")

    call_lb_doi = sum(r["total_doi"] for r in call_lb) if call_lb else 0
    put_lb_doi = sum(r["total_doi"] for r in put_lb) if put_lb else 0

    if call_lb_doi > 0 and put_lb_doi > 0:
        if call_lb_doi > put_lb_doi * 1.2:
            bias = "bullish"
        elif put_lb_doi > call_lb_doi * 1.2:
            bias = "bearish"
    elif call_lb_doi > 0:
        bias = "bullish"
    elif put_lb_doi > 0:
        bias = "bearish"

    print(f"  Net directional bias: {bias.upper()} "
          f"(Call LB DOI: {call_lb_doi:+,} | Put LB DOI: {put_lb_doi:+,})")

    return bias


# ══════════════════════════════════════════════════════════════════════════════
# PART 3 — Intraday 5-min bars (ATM +/-10%)
# ══════════════════════════════════════════════════════════════════════════════

def part3(token, df_eod, atm_strike, last_date):
    print(f"\n{'#'*70}")
    print(f"  PART 3 -- Intraday 5-min Bars (ATM +/-10%)")
    print(f"{'#'*70}\n")

    # ATM = 65.5, +/-10% -> 58.95 to 72.05
    lo = round(atm_strike * 0.9, 1)
    hi = round(atm_strike * 1.1, 1)
    # Snap to 0.5 grid
    lo = max(50.0, math.floor(lo * 2) / 2)
    hi = min(80.0, math.ceil(hi * 2) / 2)

    intra_strikes = [round(lo + i * 0.5, 1) for i in range(int((hi - lo) / 0.5) + 1)]
    intra_calls = [build_ric(s, CALL_CODE) for s in intra_strikes]
    intra_puts = [build_ric(s, PUT_CODE) for s in intra_strikes]
    intra_rics = intra_calls + intra_puts

    print(f"  Strike range: {lo} to {hi} ({len(intra_strikes)} strikes)")
    print(f"  RICs: {len(intra_calls)} calls + {len(intra_puts)} puts = {len(intra_rics)}")

    # Use last_date as the query date (most recent trading day in EOD data)
    query_date = last_date.date() if hasattr(last_date, "date") else last_date
    print(f"  Query date: {query_date}")

    print("  Fetching tick data via TickHistoryTimeAndSales...")
    tick_df = extract_tick(intra_rics, token, query_date)

    if tick_df.empty:
        print("  >> No tick data returned.")
        return 0

    print(f"  Raw tick rows: {len(tick_df):,}")
    print(f"  Columns: {list(tick_df.columns)}")

    # Identify RIC and time columns
    ric_col = "#RIC" if "#RIC" in tick_df.columns else "RIC"
    time_col = "Exch Time" if "Exch Time" in tick_df.columns else "Trade - Exchange Time"
    price_col = "Price" if "Price" in tick_df.columns else "Trade - Price"
    vol_col = "Volume" if "Volume" in tick_df.columns else "Trade - Volume"

    tick_df[price_col] = pd.to_numeric(tick_df[price_col], errors="coerce")
    tick_df[vol_col] = pd.to_numeric(tick_df[vol_col], errors="coerce")

    # Filter to actual trades with price > 0
    trades = tick_df[tick_df[price_col] > 0].copy()
    print(f"  Trades with price > 0: {len(trades):,}")

    if trades.empty:
        print("  >> No priced trades found.")
        # Still save empty parquet
        empty_df = pd.DataFrame(columns=["RIC", "bar_time", "open", "high",
                                         "low", "close", "volume", "eod_oi"])
        empty_df.to_parquet(str(OUT_DIR / "bo_options_intraday_5min.parquet"))
        return 0

    # Parse time into datetime for resampling
    # Exch Time format: HH:MM:SS.ffffff
    trades["datetime"] = pd.to_datetime(
        query_date.isoformat() + " " + trades[time_col].astype(str).str[:8],
        format="%Y-%m-%d %H:%M:%S", errors="coerce"
    )
    trades = trades.dropna(subset=["datetime"])

    # Build EOD OI lookup from Part 1 data
    oi_lookup = {}
    if df_eod is not None:
        for ric in intra_rics:
            sub = df_eod[(df_eod["RIC"] == ric) &
                         (df_eod["Trade Date"] == last_date)]
            if not sub.empty:
                oi_val = pd.to_numeric(sub.iloc[-1].get("Open Interest"), errors="coerce")
                if pd.notna(oi_val) and oi_val > 0:
                    oi_lookup[ric] = int(oi_val)

    # Resample into 5-min bars per RIC
    bars_list = []
    rics_with_trades = set()

    for ric in intra_rics:
        ric_trades = trades[trades[ric_col] == ric].copy()
        if ric_trades.empty:
            continue
        rics_with_trades.add(ric)

        ric_trades = ric_trades.set_index("datetime").sort_index()
        resampled = ric_trades[price_col].resample("5min").ohlc()
        vol_resampled = ric_trades[vol_col].resample("5min").sum()

        # Drop empty bars
        resampled = resampled.dropna(subset=["open"])
        vol_resampled = vol_resampled.reindex(resampled.index).fillna(0)

        for ts, row in resampled.iterrows():
            bars_list.append({
                "RIC": ric,
                "bar_time": ts,
                "open": row["open"],
                "high": row["high"],
                "low": row["low"],
                "close": row["close"],
                "volume": int(vol_resampled.loc[ts]),
                "eod_oi": oi_lookup.get(ric),
            })

    bars_df = pd.DataFrame(bars_list)
    print(f"\n  5-min bars generated: {len(bars_df):,}")
    print(f"  RICs with intraday trades: {len(rics_with_trades)} / {len(intra_rics)}")

    # Per-RIC summary
    print(f"\n  {'RIC':<14} {'Bars':>6} {'IntraVol':>10} {'Price Range':>15} {'EOD OI':>8}")
    print(f"  {'-'*12}  {'-'*6} {'-'*10} {'-'*15} {'-'*8}")

    for ric in sorted(rics_with_trades):
        rsub = bars_df[bars_df["RIC"] == ric]
        n_bars = len(rsub)
        total_vol = int(rsub["volume"].sum())
        plo = rsub["low"].min()
        phi = rsub["high"].max()
        oi = oi_lookup.get(ric, "")
        oi_str = f"{oi:,}" if oi else "--"
        rng = f"{plo:.2f}-{phi:.2f}"
        print(f"  {ric:<14} {n_bars:>6} {total_vol:>10,} {rng:>15} {oi_str:>8}")

    # Save parquet
    pq_path = OUT_DIR / "bo_options_intraday_5min.parquet"
    bars_df.to_parquet(str(pq_path), index=False)
    print(f"\n  Saved: {pq_path}")

    return len(rics_with_trades)


# ══════════════════════════════════════════════════════════════════════════════
# PART 4 — Historical breadth test (expired contracts)
# ══════════════════════════════════════════════════════════════════════════════

def part4(token):
    print(f"\n{'#'*70}")
    print(f"  PART 4 -- Historical Breadth Test (Expired Contracts)")
    print(f"{'#'*70}\n")

    test_rics = [
        # May 2025
        ("May 2025", "BO650E25", "BO650Q25"),
        # Jun 2025
        ("Jun 2025", "BO650F25", "BO650R25"),
        # Sep 2025
        ("Sep 2025", "BO650I25", "BO650U25"),
        # Dec 2025
        ("Dec 2025", "BO650L25", "BO650X25"),
        # Feb 2026
        ("Feb 2026", "BO650B26", "BO650N26"),
        # Mar 2026
        ("Mar 2026", "BO650C26", "BO650O26"),
    ]

    all_test = []
    for _, call_ric, put_ric in test_rics:
        all_test.extend([call_ric, put_ric])

    print(f"  Testing {len(all_test)} expired RICs with AllowHistoricalInstruments=true")
    print(f"  Pulling last 30 days before expiry for each...\n")

    # Pull all at once with a wide date range (covers all expiries)
    df = extract_eod(all_test, token,
                     start=date(2025, 4, 1), end=date(2026, 3, 25),
                     allow_historical=True)

    if "RIC" not in df.columns:
        print("  >> No data returned.")
        return 0

    df["Trade Date"] = pd.to_datetime(df["Trade Date"], errors="coerce")
    for c in ["Universal Close Price", "Volume", "Open Interest",
              "Settlement Price", "Last", "Bid", "Ask"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    print(f"  Total rows returned: {len(df):,}\n")

    # Summary table
    print(f"  {'Expiry':<12} {'RIC (Call)':<14} {'Data?':<6} {'Rows':>6} "
          f"{'Last Date':<12} {'Last Settle':>12} {'OI?':<5} | "
          f"{'RIC (Put)':<14} {'Data?':<6} {'Rows':>6} "
          f"{'Last Date':<12} {'Last Settle':>12} {'OI?':<5}")
    print(f"  {'-'*10}  {'-'*12}  {'-'*4}  {'-'*6} {'-'*11} {'-'*12} {'-'*4} | "
          f"{'-'*12}  {'-'*4}  {'-'*6} {'-'*11} {'-'*12} {'-'*4}")

    rics_with_data = 0

    for expiry_label, call_ric, put_ric in test_rics:
        parts = []
        for ric in [call_ric, put_ric]:
            sub = df[df["RIC"] == ric].copy().sort_values("Trade Date")
            if sub.empty:
                parts.append(("NO", 0, "--", "--", "--"))
                continue

            rics_with_data += 1
            n = len(sub)
            dates = sub["Trade Date"].dropna()
            last_dt = dates.max().date() if not dates.empty else "--"

            sub["_price"] = sub.apply(resolve_price, axis=1)
            priced = sub.dropna(subset=["_price"])
            last_settle = f"{priced['_price'].iloc[-1]:.4f}" if not priced.empty else "--"

            oi_pop = "No"
            if "Open Interest" in sub.columns:
                oi = sub["Open Interest"].dropna()
                oi = oi[oi > 0]
                if not oi.empty:
                    oi_pop = "Yes"

            parts.append(("YES", n, str(last_dt), last_settle, oi_pop))

        c, p = parts
        print(f"  {expiry_label:<12} {call_ric:<14} {c[0]:<6} {c[1]:>6} "
              f"{c[2]:<12} {c[3]:>12} {c[4]:<5} | "
              f"{put_ric:<14} {p[0]:<6} {p[1]:>6} "
              f"{p[2]:<12} {p[3]:>12} {p[4]:<5}")

    print(f"\n  Result: {rics_with_data}/{len(all_test)} expired RICs returned data.")
    if rics_with_data > 0:
        print("  -> Historical IV surface and OI positioning history IS feasible.")
    else:
        print("  -> Historical data for expired options NOT available.")

    return rics_with_data


# ══════════════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════════════

def main():
    token = get_token()

    # Part 1
    df, und_series, atm_strike, last_date = part1(token)
    if df is None:
        return

    # Part 2
    bias = part2(df, und_series, atm_strike, last_date)

    # Part 3
    n_intra = part3(token, df, atm_strike, last_date)

    # Part 4
    n_hist = part4(token)

    # Final summary
    last_dt_str = last_date.strftime("%Y-%m-%d") if hasattr(last_date, "strftime") \
        else str(last_date)
    print(f"\n{'='*70}")
    print(f"  Last data date: {last_dt_str} | Part2 bias: {bias} | "
          f"Part3 RICs with intraday trades: {n_intra}/56 | "
          f"Part4 expired data: {n_hist}/12 RICs returned data")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    main()
