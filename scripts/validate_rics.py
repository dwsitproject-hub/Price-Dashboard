"""
Quick validation: check if new RICs return historical data from LSEG DataScope.
Usage: python scripts/validate_rics.py
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
POLL_INTERVAL = 10

TEST_RICS = [
    "SOY-CTILL-US",   # US Soybean Oil Basis Central Illinois
    "SM-ARGUPR-P1",   # SA Soybean Meal
    "S-ARGUPR-C1",    # SA Soybeans
    "BO-ARGUPR-C1",   # SA Soybean Oil c1
    "BO-ARGUPR-C3",   # SA Soybean Oil c3
    "JCI-SOA-DALN",   # China Dalian Cash Soybean Oil
    "KTS-SOUABS-FOB", # Sunflower Oil FOB Black Sea
]

FIELDS = ["RIC", "Trade Date", "Last", "Settlement Price", "Bid", "Ask", "Volume", "Open Interest"]

END = date.today()
START = END - timedelta(days=30)


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
                    for ric in TEST_RICS
                ],
                "ValidationOptions": None,
                "UseUserPreferencesForValidationOptions": False,
            },
            "Condition": {
                "ReportDateRangeType": "Range",
                "StartDate": f"{START}T00:00:00.000Z",
                "EndDate": f"{END}T00:00:00.000Z",
            },
        }
    }


def _poll(location, token):
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
            raise RuntimeError(f"Poll error ({resp.status_code}): {resp.text[:300]}")


def main():
    token = get_token()

    print(f"\nValidating {len(TEST_RICS)} RICs")
    print(f"Date range: {START} to {END}\n")

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
        data = resp.json()
    else:
        raise RuntimeError(f"Request failed ({resp.status_code}): {resp.text[:400]}")

    # Print any notes (warnings about unrecognised RICs etc.)
    notes = data.get("Notes", [])
    if notes:
        print("=" * 60)
        print("NOTES FROM LSEG:")
        for note in notes if isinstance(notes, list) else [notes]:
            print(f"  {note}")
        print("=" * 60)

    contents = data.get("Contents", [])
    if not contents:
        print("\nNo data returned for any RIC.")
        return

    df = pd.DataFrame(contents)

    print(f"\n{'='*60}")
    print(f"  VALIDATION RESULTS")
    print(f"{'='*60}")

    for ric in TEST_RICS:
        subset = df[df["RIC"] == ric]
        if subset.empty:
            print(f"\n  {ric:<20} ** NO DATA **")
        else:
            dates = pd.to_datetime(subset["Trade Date"], errors="coerce")
            last_prices = subset["Last"].dropna()
            settle_prices = subset["Settlement Price"].dropna()
            sample = last_prices.iloc[-1] if len(last_prices) > 0 else (
                settle_prices.iloc[-1] if len(settle_prices) > 0 else "N/A"
            )
            print(f"\n  {ric:<20} OK")
            print(f"    Rows:   {len(subset)}")
            print(f"    Range:  {dates.min().date()} to {dates.max().date()}")
            print(f"    Latest: {sample}")

    print(f"\n{'='*60}\n")


if __name__ == "__main__":
    main()
