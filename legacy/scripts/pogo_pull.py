"""
Standalone script: Pull FCPOc1-c7, LGOc1-c7, MYR= from LSEG,
calculate 8 POGO spreads, and display a change table.
"""
import sys
import time
from pathlib import Path
from datetime import date, timedelta

import pandas as pd
import requests

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from scripts.auth import get_token

# ── Config ────────────────────────────────────────────────────────────────────
EXTRACT_URL = "https://selectapi.datascope.lseg.com/RestApi/v1/Extractions/ExtractWithNotes"
FIELDS = ["RIC", "Trade Date", "Last", "Settlement Price", "Bid", "Ask"]
POLL_INTERVAL = 10

FCPO_RICS = [f"FCPOc{i}" for i in range(1, 8)]  # c1-c7
LGO_RICS  = [f"LGOc{i}"  for i in range(1, 8)]  # c1-c7
FX_RIC    = "MYR="
ALL_RICS  = FCPO_RICS + LGO_RICS + [FX_RIC]

START_DATE = "2025-03-17"
END_DATE   = "2026-03-22"  # today

# 8 spreads: POGO c1-c7 plus cross-tenor POc4GOc5
SPREAD_DEFS = [
    ("POGO c1", "FCPOc1", "LGOc1"),
    ("POGO c2", "FCPOc2", "LGOc2"),
    ("POGO c3", "FCPOc3", "LGOc3"),
    ("POGO c4", "FCPOc4", "LGOc4"),
    ("POGO c5", "FCPOc5", "LGOc5"),
    ("POGO c6", "FCPOc6", "LGOc6"),
    ("POGO c7", "FCPOc7", "LGOc7"),
    ("POc4GOc5", "FCPOc4", "LGOc5"),
]


# ── API helpers ───────────────────────────────────────────────────────────────

def _headers(token):
    return {
        "Authorization": f"Token {token}",
        "Content-Type": "application/json",
        "Prefer": "respond-async",
    }


def _build_body():
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
                    for ric in ALL_RICS
                ],
                "ValidationOptions": None,
                "UseUserPreferencesForValidationOptions": False,
            },
            "Condition": {
                "ReportDateRangeType": "Range",
                "StartDate": f"{START_DATE}T00:00:00.000Z",
                "EndDate":   f"{END_DATE}T00:00:00.000Z",
            },
        }
    }


def _poll(location, token):
    attempt = 0
    while True:
        attempt += 1
        print(f"  Waiting for data... (attempt {attempt})", flush=True)
        time.sleep(POLL_INTERVAL)
        resp = requests.get(location, headers=_headers(token), timeout=(30, 300))
        if resp.status_code == 200:
            return resp.json()
        if resp.status_code != 202:
            raise RuntimeError(f"Unexpected status while polling ({resp.status_code}): {resp.text[:300]}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    token = get_token()

    print(f"\nFetching {len(ALL_RICS)} RICs: {', '.join(ALL_RICS)}")
    print(f"Date range: {START_DATE} to {END_DATE}\n")

    resp = requests.post(
        EXTRACT_URL,
        headers=_headers(token),
        json=_build_body(),
        timeout=(30, 300),
    )

    if resp.status_code == 202:
        location = resp.headers.get("Location")
        if not location:
            raise RuntimeError("Got 202 but no Location header.")
        print("Request accepted (202). Polling...")
        data = _poll(location, token)
    elif resp.status_code == 200:
        print("Data returned immediately (200).")
        data = resp.json()
    else:
        try:
            msg = resp.json()["error"]["message"]
        except Exception:
            msg = resp.text[:400]
        raise RuntimeError(f"Extraction failed ({resp.status_code}): {msg}")

    contents = data.get("Contents", [])
    if not contents:
        raise ValueError("No data in response.")

    df = pd.DataFrame(contents)
    print(f"Received {len(df):,} rows across {df['RIC'].nunique()} RICs\n")

    # ── Parse prices ──────────────────────────────────────────────────────────
    df["Trade Date"] = pd.to_datetime(df["Trade Date"]).dt.normalize()

    prices = []
    for _, row in df.iterrows():
        ric = row["RIC"]
        dt = row["Trade Date"]
        if ric == FX_RIC:
            bid = pd.to_numeric(row.get("Bid"), errors="coerce")
            ask = pd.to_numeric(row.get("Ask"), errors="coerce")
            price = (bid + ask) / 2 if pd.notna(bid) and pd.notna(ask) else None
        else:
            last = pd.to_numeric(row.get("Last"), errors="coerce")
            sett = pd.to_numeric(row.get("Settlement Price"), errors="coerce")
            price = last if pd.notna(last) else sett
        prices.append({"date": dt, "RIC": ric, "price": price})

    pdf = pd.DataFrame(prices).drop_duplicates(subset=["date", "RIC"])
    wide = pdf.pivot(index="date", columns="RIC", values="price").sort_index()

    # Forward-fill FX
    if FX_RIC in wide.columns:
        wide[FX_RIC] = wide[FX_RIC].ffill()

    # Forward-fill all to cover holidays
    wide = wide.ffill()

    # ── Convert FCPO from MYR/MT to USD/MT ────────────────────────────────────
    if FX_RIC not in wide.columns:
        raise ValueError("MYR= not found in data — cannot convert FCPO.")

    myr = wide[FX_RIC]
    for ric in FCPO_RICS:
        if ric in wide.columns:
            wide[ric] = wide[ric] / myr

    print("FCPO converted from MYR/MT to USD/MT using MYR= mid rate.\n")

    # ── Calculate spreads ─────────────────────────────────────────────────────
    spreads = pd.DataFrame(index=wide.index)
    for name, ric_a, ric_b in SPREAD_DEFS:
        if ric_a in wide.columns and ric_b in wide.columns:
            spreads[name] = wide[ric_a] - wide[ric_b]
        else:
            missing = [r for r in [ric_a, ric_b] if r not in wide.columns]
            print(f"WARNING: {name} skipped — missing {', '.join(missing)}")

    spreads = spreads.dropna(how="all")

    # ── Change table ──────────────────────────────────────────────────────────
    today = spreads.index.max()
    print(f"Most recent date in data: {today.date()}\n")

    # Define lookback dates
    def _find_date(target):
        """Find the closest available date <= target."""
        mask = spreads.index <= target
        if mask.any():
            return spreads.index[mask][-1]
        return None

    # Reference dates
    ref_dates = {}
    # Daily: previous trading day
    prev_dates = spreads.index[spreads.index < today]
    if len(prev_dates) > 0:
        ref_dates["Daily"] = prev_dates[-1]

    # Weekly: 7 calendar days ago
    ref_dates["Weekly"] = _find_date(today - timedelta(days=7))

    # Monthly: 30 calendar days ago
    ref_dates["Monthly"] = _find_date(today - timedelta(days=30))

    # 3M: 90 days ago
    ref_dates["3M"] = _find_date(today - timedelta(days=90))

    # 6M: 180 days ago
    ref_dates["6M"] = _find_date(today - timedelta(days=180))

    # YTD: last trading day of previous year
    ytd_target = pd.Timestamp(f"{today.year - 1}-12-31")
    ref_dates["YTD"] = _find_date(ytd_target)

    # Build table
    current = spreads.loc[today]
    rows = []
    for name in spreads.columns:
        row = {"Spread": name, "Current": round(current[name], 2)}
        for period, ref_dt in ref_dates.items():
            if ref_dt is not None and ref_dt in spreads.index:
                chg = current[name] - spreads.loc[ref_dt, name]
                row[period] = round(chg, 2)
            else:
                row[period] = "N/A"
        rows.append(row)

    result = pd.DataFrame(rows)
    result = result.set_index("Spread")

    # ── Print ─────────────────────────────────────────────────────────────────
    print("=" * 90)
    print("POGO SPREAD ANALYSIS — All values in USD/MT")
    print(f"As of: {today.date()}")
    print("=" * 90)

    # Print reference dates
    for period, ref_dt in ref_dates.items():
        if ref_dt is not None:
            print(f"  {period:8s} change vs {ref_dt.date()}")
    print()

    print(result.to_string())
    print()

    # Also print outright levels for context
    print("\n" + "=" * 90)
    print("OUTRIGHT LEVELS (USD/MT) — Most recent date")
    print("=" * 90)
    outright_rics = FCPO_RICS + LGO_RICS
    for ric in outright_rics:
        if ric in wide.columns:
            val = wide.loc[today, ric]
            print(f"  {ric:<10s} {val:>10.2f}")
    print(f"\n  MYR=       {wide.loc[today, FX_RIC]:>10.4f}")


if __name__ == "__main__":
    main()
