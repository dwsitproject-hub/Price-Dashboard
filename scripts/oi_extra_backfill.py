"""
One-time backfill for the 75 new extended-curve OI RICs (config._EXTRA_OI_CUM)
needed by the OI/Volume -> Cumulative tab. Pulls only the NEW RICs (not the
101 already in the raw CSV) in yearly chunks, retrying transient ReadTimeouts,
and merges the result into data/raw/all_instruments_raw.csv without touching
existing rows for other RICs.
"""
import sys
import time
from datetime import date
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from scripts.auth import get_token
import config

EXTRACT_URL = "https://selectapi.datascope.lseg.com/RestApi/v1/Extractions/ExtractWithNotes"
RAW_FILE    = ROOT / "data" / "raw" / "all_instruments_raw.csv"
STAGING     = ROOT / "data" / "raw" / "_oi_extra_backfill_staging.csv"

FIELDS = ["RIC", "Trade Date", "Last", "Settlement Price", "Bid", "Ask", "Volume", "Open Interest"]
POLL_INTERVAL = 10
MAX_POLL_RETRIES = 40   # ~ up to ~7 min of transient-timeout tolerance per attempt

RICS = config._EXTRA_OI_CUM
YEARS = list(range(2015, date.today().year + 1))


def _headers(token):
    return {
        "Authorization": f"Token {token}",
        "Content-Type": "application/json",
        "Prefer": "respond-async",
    }


def _build_body(start_date, end_date):
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
                    {"Identifier": ric, "IdentifierType": "Ric"} for ric in RICS
                ],
                "ValidationOptions": None,
                "UseUserPreferencesForValidationOptions": False,
            },
            "Condition": {
                "ReportDateRangeType": "Range",
                "StartDate": f"{start_date}T00:00:00.000Z",
                "EndDate":   f"{end_date}T00:00:00.000Z",
            },
        }
    }


def _poll(location, token):
    """Poll until 200. Transient network ReadTimeouts on the GET itself are
    retried (not fatal) — the extraction job keeps running server-side
    regardless of whether our poll request round-trips in time."""
    attempt = 0
    timeouts = 0
    while True:
        attempt += 1
        print(f"    poll attempt {attempt} (timeouts so far: {timeouts})...", end="\r", flush=True)
        time.sleep(POLL_INTERVAL)
        try:
            resp = requests.get(location, headers=_headers(token), timeout=(30, 120))
        except (requests.exceptions.ReadTimeout, requests.exceptions.ConnectionError) as exc:
            timeouts += 1
            if timeouts > MAX_POLL_RETRIES:
                raise RuntimeError(f"Too many poll timeouts ({timeouts}): {exc}") from exc
            continue
        if resp.status_code == 200:
            print()
            return resp.json()
        if resp.status_code != 202:
            raise RuntimeError(f"Unexpected status while polling ({resp.status_code}): {resp.text[:300]}")


def _fetch_year(token, year: int) -> pd.DataFrame:
    start = f"{year}-01-01"
    end   = f"{year}-12-31" if year < date.today().year else date.today().isoformat()
    print(f"  Fetching {len(RICS)} RICs for {start} to {end}...")

    resp = requests.post(
        EXTRACT_URL, headers=_headers(token), json=_build_body(start, end),
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
        raise RuntimeError(f"Extraction failed ({resp.status_code}): {msg}")

    contents = data.get("Contents", [])
    if not contents:
        print(f"    no rows for {year}.")
        return pd.DataFrame()
    df = pd.DataFrame(contents)
    print(f"    {len(df):,} rows.")
    return df


def main():
    token = get_token()

    done_years = set()
    if STAGING.exists():
        staged = pd.read_csv(STAGING, low_memory=False)
        staged["Trade Date"] = pd.to_datetime(staged["Trade Date"], errors="coerce")
        done_years = set(staged["Trade Date"].dt.year.dropna().astype(int).unique())
        print(f"Resuming: {len(staged):,} rows already staged, years done: {sorted(done_years)}")
    else:
        staged = pd.DataFrame()

    for year in YEARS:
        if year in done_years:
            print(f"Year {year}: already staged, skipping.")
            continue
        try:
            year_df = _fetch_year(token, year)
        except Exception as exc:
            print(f"  ERROR fetching {year}: {exc}")
            print("  Stopping — re-run this script to resume from here.")
            break
        if not year_df.empty:
            staged = pd.concat([staged, year_df], ignore_index=True)
        staged.to_csv(STAGING, index=False)
        print(f"  Staged total so far: {len(staged):,} rows.")

    if staged.empty:
        print("No data staged — nothing to merge.")
        return

    print(f"\nMerging {len(staged):,} staged rows into {RAW_FILE.name}...")
    old_df = pd.read_csv(RAW_FILE, low_memory=False)
    combined = pd.concat([old_df, staged], ignore_index=True)
    merged = combined.drop_duplicates(subset=["RIC", "Trade Date"], keep="last") \
                      .sort_values(["RIC", "Trade Date"]).reset_index(drop=True)
    merged.to_csv(RAW_FILE, index=False)
    print(f"Merged: {len(old_df):,} existing + {len(staged):,} new/staged "
          f"= {len(merged):,} rows in {RAW_FILE.name}.")

    present_rics = set(merged["RIC"].unique())
    missing = [r for r in RICS if r not in present_rics]
    if missing:
        print(f"WARNING: {len(missing)} target RICs never returned any rows: {missing[:10]}...")
    else:
        print(f"All {len(RICS)} target RICs present in raw CSV.")


if __name__ == "__main__":
    main()
