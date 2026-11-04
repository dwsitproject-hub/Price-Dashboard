"""
Diagnostic: test whether 7 new LSEG RICs return usable price data.
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
FIELDS = ["RIC", "Trade Date", "Last", "Settlement Price", "Bid", "Ask"]
POLL_INTERVAL = 10

# Base product codes — we'll test with -M1 suffix (matching existing cash pattern)
BASE_PRODUCTS = [
    "ASSOYA-CN",
    "ASSOYMEAL-CN",
    "ASSOYOIL-CN",
    "ASPALMOIL-CN",
    "SOYBEAN-AR",
    "AMSOYMEAL-AR",
    "AMSOYOIL-AR",
]

# Test multiple suffix patterns
SUFFIXES = ["-M1", "-M2", "-c1"]

# Build full test list: each base with all suffixes
TEST_RICS = [f"{base}{sfx}" for base in BASE_PRODUCTS for sfx in SUFFIXES]

CURRENCY_MAP = {
    ric: ("CNY/T" if "CN" in ric else "USD/T")
    for ric in TEST_RICS
}


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Token {token}",
        "Content-Type": "application/json",
        "Prefer": "respond-async",
    }


def _build_body(rics: list[str]) -> dict:
    end = date.today()
    start = end - timedelta(days=10)  # ~5 business days
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
            print()
            return resp.json()
        if resp.status_code != 202:
            raise RuntimeError(f"Poll failed ({resp.status_code}): {resp.text[:300]}")


def _extract(rics: list[str], token: str) -> pd.DataFrame:
    """Send extraction request and return DataFrame of results."""
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
    if notes:
        note_str = str(notes)[:500] if isinstance(notes, list) else str(notes)[:500]
        print(f"\n  API Notes: {note_str}")

    return pd.DataFrame(contents) if contents else pd.DataFrame(columns=FIELDS)


def _resolve_price(row: pd.Series) -> float | None:
    """Pick best available price from a row."""
    for col in ["Last", "Settlement Price"]:
        if col in row and pd.notna(row[col]) and row[col] != 0:
            return float(row[col])
    # Try mid of bid/ask
    if "Bid" in row and "Ask" in row and pd.notna(row["Bid"]) and pd.notna(row["Ask"]):
        bid, ask = float(row["Bid"]), float(row["Ask"])
        if bid > 0 and ask > 0:
            return (bid + ask) / 2
    return None


def main():
    token = get_token()

    # ── Send single request with all suffix variants ────────────────────────
    print(f"\n{'='*70}")
    print(f"  Testing {len(TEST_RICS)} RIC variants (last ~5 business days)")
    print(f"  Suffixes tried per product: {SUFFIXES}")
    print(f"{'='*70}\n")

    df = _extract(TEST_RICS, token)

    # Build results per RIC
    results = {}

    for ric in TEST_RICS:
        subset = df[df["RIC"] == ric] if ("RIC" in df.columns and len(df)) else pd.DataFrame()
        if subset.empty:
            results[ric] = {
                "status": "EMPTY", "last_price": None,
                "last_date": None, "currency": CURRENCY_MAP.get(ric, "?"),
                "note": "no rows",
            }
        else:
            subset = subset.copy()
            subset["Trade Date"] = pd.to_datetime(subset["Trade Date"], errors="coerce")
            subset = subset.dropna(subset=["Trade Date"]).sort_values("Trade Date")
            subset["_price"] = subset.apply(_resolve_price, axis=1)
            priced = subset.dropna(subset=["_price"])

            if priced.empty:
                results[ric] = {
                    "status": "EMPTY", "last_price": None,
                    "last_date": None, "currency": CURRENCY_MAP.get(ric, "?"),
                    "note": f"{len(subset)} rows but all prices null",
                }
            else:
                last = priced.iloc[-1]
                results[ric] = {
                    "status": "OK",
                    "last_price": last["_price"],
                    "last_date": last["Trade Date"].date(),
                    "currency": CURRENCY_MAP.get(ric, "?"),
                    "note": f"{len(priced)} rows, {priced['Trade Date'].min().date()} to {priced['Trade Date'].max().date()}",
                }

    # ── Summary table ─────────────────────────────────────────────────────────
    print(f"\n{'='*95}")
    print(f"  {'RIC':<24} {'Status':<8} {'Last Price':>12} {'Last Date':<12} {'Currency':<8} Notes")
    print(f"  {'-'*22}  {'-'*6}  {'-'*12} {'-'*11} {'-'*7}  {'-'*30}")

    for base in BASE_PRODUCTS:
        for sfx in SUFFIXES:
            ric = f"{base}{sfx}"
            r = results[ric]
            price_str = f"{r['last_price']:.2f}" if r["last_price"] is not None else "—"
            date_str = str(r["last_date"]) if r["last_date"] else "—"
            marker = " <<" if r["status"] == "OK" else ""
            print(f"  {ric:<24} {r['status']:<8} {price_str:>12} {date_str:<12} {r['currency']:<8} {r['note']}{marker}")
        print()  # blank line between products

    print(f"{'='*95}")

    ok_count = sum(1 for r in results.values() if r["status"] == "OK")
    ok_products = set()
    for ric, r in results.items():
        if r["status"] == "OK":
            # strip suffix to get base
            for sfx in SUFFIXES:
                if ric.endswith(sfx):
                    ok_products.add(ric[:-len(sfx)])
                    break

    print(f"\n  Result: {ok_count}/{len(TEST_RICS)} RIC variants returned data")
    print(f"  Products with data: {len(ok_products)}/{len(BASE_PRODUCTS)}")
    if ok_products:
        print(f"  Working products: {', '.join(sorted(ok_products))}")
    print()


if __name__ == "__main__":
    main()
