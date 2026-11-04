"""
Diagnostic: test CBOT Soybean Oil option RICs via DataScope.
Tries multiple RIC format conventions to find which one works.
Read-only — no files written.
"""

import sys
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from scripts.auth import get_token

EXTRACT_URL = "https://selectapi.datascope.lseg.com/RestApi/v1/Extractions/ExtractWithNotes"

# Use the same field names that work for the existing futures extraction
# (Open/High/Low/Strike are not valid for ElektronTimeseries)
FIELDS = [
    "RIC",
    "Trade Date",
    "Universal Close Price",
    "Last",
    "Volume",
    "Open Interest",
    "Settlement Price",
    "Bid",
    "Ask",
    "Expiration Date",
]

POLL_INTERVAL = 10

# ── RIC formats to try ──────────────────────────────────────────────────────
# Format A: RULES2 month codes, 2-digit year, 3-digit strike
#   Call months: F=Jan,G=Feb,H=Mar,J=Apr,K=May,F=Jun... wait, RULES2:
#   Calls: A=Jan,B=Feb,C=Mar,D=Apr,E=May,F=Jun,G=Jul,H=Aug,I=Sep,J=Oct,K=Nov,L=Dec
#   Puts:  M=Jan,N=Feb,O=Mar,P=Apr,Q=May,R=Jun,S=Jul,T=Aug,U=Sep,V=Oct,W=Nov,X=Dec

FORMAT_SETS = {
    # Refinitiv standard: root + strike(with decimal) + month-code + year + .U  (exchange suffix)
    "Format 1 (BO45.00N6.U — decimal strike)": [
        "BO45.00N6.U",
        "BO50.00N6.U",
        "BO45.00N26.U",
        "BO45N26C.CB",
        "BO45N6.CB",
    ],
    # CME-style: root + expiry-monthyear + strike + C/P
    "Format 2 (BON26 45000C — strike in thousandths)": [
        "BON2645000C",
        "BON2650000C",
        "BON2645000P",
        "BOQ2645000C",
    ],
    # Refinitiv OTC/exchange option: root + strike + callput + month + year
    "Format 3 (BO4500F6 — RULES2, strike*100)": [
        "BO4500F6",
        "BO5000F6",
        "BO4500R6",
        "BO4000R6",
        "BO4500G6",
        "BO4500S6",
    ],
    # Another common format: BOc1 option chains
    "Format 4 (BON6 45C / BON26 45C — simple)": [
        "BON6 45C",
        "BON26 45C",
        "BON6C45",
        "BON26C45",
        "BON645C",
        "BON2645C",
    ],
    # Try known working patterns — FCPOc1 option style if any
    "Format 5 (BO45.00N6 / BO4500N6 — no exchange suffix)": [
        "BO45.00N6",
        "BO4500N6",
        "BO45N6",
        "BO45.00Q6",
        "BO4500Q6",
        "BO45Q6",
    ],
    # CBT exchange options: instrument + month + year + strike + type
    "Format 6 (BO N26C 45.00 variants)": [
        "BON260045.0C",
        "BON260045.0P",
        "BON2645.0C",
        "BON2645.00C",
        "BON26P45.0",
        "BON26C45.0",
    ],
}


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
        print(f"    Polling... (attempt {attempt})", end="\r", flush=True)
        time.sleep(POLL_INTERVAL)
        resp = requests.get(location, headers=_headers(token), timeout=(30, 300))
        if resp.status_code == 200:
            print()
            return resp.json()
        if resp.status_code != 202:
            raise RuntimeError(f"Poll failed ({resp.status_code}): {resp.text[:300]}")


def _extract(rics: list[str], token: str) -> tuple[pd.DataFrame, str]:
    """Return (DataFrame, notes_string)."""
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
            msg = resp.text[:400]
        raise RuntimeError(f"Request failed ({resp.status_code}): {msg}")

    contents = data.get("Contents", [])
    notes = data.get("Notes", [])
    notes_str = str(notes)[:600] if notes else ""

    df = pd.DataFrame(contents) if contents else pd.DataFrame()
    return df, notes_str


def _analyze_ric(df: pd.DataFrame, ric: str) -> dict:
    """Analyze results for a single RIC."""
    if df.empty or "RIC" not in df.columns:
        return {"status": "EMPTY", "rows": 0}

    subset = df[df["RIC"] == ric]
    if subset.empty:
        return {"status": "EMPTY", "rows": 0}

    subset = subset.copy()
    n = len(subset)

    # Parse dates
    if "Trade Date" in subset.columns:
        subset["Trade Date"] = pd.to_datetime(subset["Trade Date"], errors="coerce")
        valid_dates = subset["Trade Date"].dropna()
        first_date = valid_dates.min().date() if not valid_dates.empty else None
        last_date = valid_dates.max().date() if not valid_dates.empty else None
    else:
        first_date = last_date = None

    # Last price
    last_price = None
    for col in ["Settlement Price", "Universal Close Price", "Last"]:
        if col in subset.columns:
            vals = pd.to_numeric(subset[col], errors="coerce").dropna()
            vals = vals[vals != 0]
            if not vals.empty:
                last_price = float(vals.iloc[-1])
                break

    # OI and Volume check
    oi_populated = False
    vol_populated = False
    if "Open Interest" in subset.columns:
        oi_vals = pd.to_numeric(subset["Open Interest"], errors="coerce").dropna()
        oi_populated = (oi_vals != 0).any()
    if "Volume" in subset.columns:
        vol_vals = pd.to_numeric(subset["Volume"], errors="coerce").dropna()
        vol_populated = (vol_vals != 0).any()

    return {
        "status": "OK" if last_price is not None else "NO_PRICE",
        "rows": n,
        "first_date": first_date,
        "last_date": last_date,
        "last_price": last_price,
        "oi_populated": oi_populated,
        "vol_populated": vol_populated,
    }


def main():
    token = get_token()
    first_raw_printed = False

    for fmt_name, rics in FORMAT_SETS.items():
        print(f"\n{'='*70}")
        print(f"  {fmt_name}")
        print(f"  RICs: {', '.join(rics)}")
        print(f"{'='*70}")

        try:
            df, notes = _extract(rics, token)
        except Exception as e:
            print(f"  ERROR: {e}")
            continue

        if notes:
            print(f"  API Notes: {notes}")

        if df.empty or "RIC" not in df.columns:
            print(f"  >> No data returned for any RIC in this format.\n")
            continue

        # Print raw column names
        print(f"\n  Raw columns returned: {list(df.columns)}")
        print(f"  Total rows: {len(df)}")
        print(f"  Unique RICs in response: {df['RIC'].unique().tolist()}")

        # Print raw head for first successful format
        if not first_raw_printed and len(df) > 0:
            print(f"\n  --- Raw DataFrame head (first RIC with data) ---")
            first_ric_data = df.head(5)
            with pd.option_context("display.max_columns", None, "display.width", 200):
                print(first_ric_data.to_string(index=False))
            print("  ---")
            first_raw_printed = True

        # Per-RIC summary
        print(f"\n  {'RIC':<16} {'Status':<10} {'Rows':>5} {'First Date':<12} {'Last Date':<12} {'Last Price':>12} {'OI?':<5} {'Vol?':<5}")
        print(f"  {'-'*14}  {'-'*8}  {'-'*5} {'-'*11} {'-'*11} {'-'*12} {'-'*4} {'-'*4}")

        for ric in rics:
            r = _analyze_ric(df, ric)
            if r["status"] == "EMPTY":
                print(f"  {ric:<16} {'EMPTY':<10} {0:>5} {'—':<12} {'—':<12} {'—':>12} {'—':<5} {'—':<5}")
            else:
                price_str = f"{r['last_price']:.4f}" if r.get("last_price") else "—"
                fd = str(r.get("first_date", "—")) or "—"
                ld = str(r.get("last_date", "—")) or "—"
                oi = "Yes" if r.get("oi_populated") else "No"
                vol = "Yes" if r.get("vol_populated") else "No"
                print(f"  {ric:<16} {r['status']:<10} {r['rows']:>5} {fd:<12} {ld:<12} {price_str:>12} {oi:<5} {vol:<5}")

    print(f"\n{'='*70}")
    print("  Diagnostic complete.")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    main()