import sys
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from scripts.auth import get_token
import config

EXTRACT_URL = "https://selectapi.datascope.lseg.com/RestApi/v1/Extractions/ExtractWithNotes"
RAW_DIR     = ROOT / "data" / "raw"
OUTPUT_FILE = RAW_DIR / "all_instruments_raw.csv"

FIELDS = ["RIC", "Trade Date", "Last", "Settlement Price", "Bid", "Ask", "Volume", "Open Interest"]

POLL_INTERVAL = 10


# ── Helpers ────────────────────────────────────────────────────────────────────

def _headers(token: str) -> dict:
    return {
        "Authorization": f"Token {token}",
        "Content-Type": "application/json",
        "Prefer": "respond-async",
    }


def _build_body(start_date: str) -> dict:
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
                    for ric in config.ALL_RICS
                ],
                "ValidationOptions": None,
                "UseUserPreferencesForValidationOptions": False,
            },
            "Condition": {
                "ReportDateRangeType": "Range",
                "StartDate": f"{start_date}T00:00:00.000Z",
                "EndDate":   f"{config.END_DATE}T00:00:00.000Z",
            },
        }
    }


def _poll(location: str, token: str) -> dict:
    attempt = 0
    while True:
        attempt += 1
        print(f"  Waiting for data... (attempt {attempt})", end="\r", flush=True)
        time.sleep(POLL_INTERVAL)
        resp = requests.get(location, headers=_headers(token), timeout=(30, 300))
        if resp.status_code == 200:
            print()
            return resp.json()
        if resp.status_code != 202:
            raise RuntimeError(
                f"Unexpected status while polling ({resp.status_code}): {resp.text[:300]}"
            )


# ── Summary ────────────────────────────────────────────────────────────────────

def _print_summary(df: pd.DataFrame) -> None:
    print(f"\n{'='*55}")
    print(f"  Total rows : {len(df):,}")
    print(f"{'='*55}")

    groups = {
        "Futures":          config.FUTURES_RICS,
        "FX":               config.RICS["fx"],
        "Malaysia Cash":    config.RICS["malaysia_cash"],
        "Indonesia Cash":   config.RICS["indonesia_cash"],
        "US Cash":          config.RICS["us_cash"],
        "SA Cash":          config.RICS["sa_cash"],
        "China Cash":       config.RICS["china_cash"],
        "Black Sea Cash":   config.RICS["blacksea_cash"],
    }

    for group, rics in groups.items():
        print(f"\n  {group}")
        for ric in rics:
            subset = df[df["RIC"] == ric]
            if subset.empty:
                print(f"    {ric:<24} NO DATA")
            else:
                dates = pd.to_datetime(subset["Trade Date"], errors="coerce")
                print(
                    f"    {ric:<24} {len(subset):>5} rows   "
                    f"{dates.min().date()} to {dates.max().date()}"
                )

    print(f"\n  Saved to: {OUTPUT_FILE}\n")


# ── Main ───────────────────────────────────────────────────────────────────────

def fetch_all() -> pd.DataFrame:
    RAW_DIR.mkdir(parents=True, exist_ok=True)

    # Incremental by default: pull only the trailing LOOKBACK_DAYS window and
    # merge into the existing raw CSV. Fall back to a full history pull when the
    # raw file is missing or --full is passed (first-time backfill / new RICs).
    full = "--full" in sys.argv
    incremental = (not full) and OUTPUT_FILE.exists()
    if incremental:
        start_date = (date.today() - timedelta(days=config.LOOKBACK_DAYS)).isoformat()
        mode = f"incremental ({config.LOOKBACK_DAYS}-day window)"
    else:
        start_date = config.START_DATE
        mode = "full history" + (" (--full)" if full else " (no existing CSV)")

    token = get_token()

    print(f"\nFetching {len(config.ALL_RICS)} RICs  [{mode}]")
    print(f"Fields     : {', '.join(FIELDS)}")
    print(f"Date range : {start_date} to {config.END_DATE}\n")

    resp = requests.post(
        EXTRACT_URL,
        headers=_headers(token),
        json=_build_body(start_date),
        timeout=(30, 300),  # (connect, read) — large requests need up to 5 min
    )

    if resp.status_code == 202:
        location = resp.headers.get("Location")
        if not location:
            raise RuntimeError("Got 202 but no Location header for polling.")
        print("Request accepted (202). Polling for results...")
        data = _poll(location, token)

    elif resp.status_code == 200:
        print("Data returned immediately (200).")
        data = resp.json()

    elif resp.status_code == 401:
        raise PermissionError(
            "Authorization failed (401). Delete token.json and retry."
        )
    else:
        try:
            msg = resp.json()["error"]["message"]
        except Exception:
            msg = resp.text[:400]
        raise RuntimeError(f"Extraction request failed ({resp.status_code}): {msg}")

    contents = data.get("Contents", [])

    if incremental:
        if not contents:
            # No trading days in the window (e.g. a holiday stretch) — keep the
            # existing file untouched rather than wiping it.
            print("No rows returned for the lookback window; keeping existing CSV.")
            df = pd.read_csv(OUTPUT_FILE)
        else:
            new_df = pd.DataFrame(contents)
            old_df = pd.read_csv(OUTPUT_FILE)
            combined = pd.concat([old_df, new_df], ignore_index=True)
            # Newest pull is appended last, so keep="last" lets refreshed
            # settlements/OI overwrite the prior values for the same RIC+date.
            df = combined.drop_duplicates(
                subset=["RIC", "Trade Date"], keep="last"
            ).sort_values(["RIC", "Trade Date"]).reset_index(drop=True)
            print(
                f"Merged: {len(old_df):,} existing + {len(new_df):,} fetched "
                f"= {len(df):,} rows ({len(combined) - len(df):,} overlapping "
                f"rows refreshed)."
            )
    else:
        if not contents:
            raise ValueError("Response contained no data in 'Contents'.")
        df = pd.DataFrame(contents)

    df.to_csv(OUTPUT_FILE, index=False)
    _print_summary(df)
    return df


if __name__ == "__main__":
    fetch_all()
