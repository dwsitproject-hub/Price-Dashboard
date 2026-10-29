"""
oi_pull_dalian.py  –  Pull DCP c1-c8 and DBY c1-c8 for Dalian OI analysis.
Fields: Last, Settlement Price, Volume, Open Interest.
Saves to data/oi/dcp_dby_oi_raw.csv
"""

import sys
import time
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from scripts.auth import get_token

EXTRACT_URL = "https://selectapi.datascope.lseg.com/RestApi/v1/Extractions/ExtractWithNotes"

DCP_RICS = [f"DCPc{i}" for i in range(1, 9)]
DBY_RICS = [f"DBYc{i}" for i in range(1, 9)]
ALL_RICS = DCP_RICS + DBY_RICS

FIELDS = ["RIC", "Trade Date", "Last", "Settlement Price", "Volume", "Open Interest"]

START_DATE = "2015-01-01"
END_DATE = pd.Timestamp.now().strftime("%Y-%m-%d")

OUT_DIR = ROOT / "data" / "oi"
OUT_FILE = OUT_DIR / "dcp_dby_oi_raw.csv"
POLL_INTERVAL = 10


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
                "EndDate": f"{END_DATE}T00:00:00.000Z",
            },
        }
    }


def _poll(location, token):
    attempt = 0
    while True:
        attempt += 1
        print(f"  Polling... attempt {attempt}", end="\r", flush=True)
        time.sleep(POLL_INTERVAL)
        resp = requests.get(location, headers=_headers(token), timeout=(30, 300))
        if resp.status_code == 200:
            print()
            return resp.json()
        if resp.status_code != 202:
            raise RuntimeError(f"Unexpected poll status ({resp.status_code}): {resp.text[:300]}")


def pull():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    token = get_token()

    print(f"\nDalian OI Pull: {len(ALL_RICS)} RICs  ({len(DCP_RICS)} DCP + {len(DBY_RICS)} DBY)")
    print(f"Fields : {', '.join(FIELDS)}")
    print(f"Range  : {START_DATE} to {END_DATE}\n")

    resp = requests.post(EXTRACT_URL, headers=_headers(token), json=_build_body(), timeout=(30, 300))

    if resp.status_code == 202:
        location = resp.headers.get("Location")
        if not location:
            raise RuntimeError("Got 202 but no Location header.")
        print("Request accepted (202). Polling...")
        data = _poll(location, token)
    elif resp.status_code == 200:
        print("Data returned immediately (200).")
        data = resp.json()
    elif resp.status_code == 401:
        raise PermissionError("Auth failed (401). Delete token.json and retry.")
    else:
        try:
            msg = resp.json()["error"]["message"]
        except Exception:
            msg = resp.text[:400]
        raise RuntimeError(f"Extraction failed ({resp.status_code}): {msg}")

    contents = data.get("Contents", [])
    if not contents:
        raise ValueError("Response contained no data.")

    df = pd.DataFrame(contents)
    df.to_csv(OUT_FILE, index=False)

    print(f"\n{'='*60}")
    print(f"  Total rows: {len(df):,}")
    print(f"{'='*60}")
    for label, rics in [("DCP", DCP_RICS), ("DBY", DBY_RICS)]:
        print(f"\n  {label}")
        for ric in rics:
            sub = df[df["RIC"] == ric]
            if sub.empty:
                print(f"    {ric:<16} NO DATA")
            else:
                dates = pd.to_datetime(sub["Trade Date"], errors="coerce")
                n_oi = sub["Open Interest"].notna().sum()
                print(f"    {ric:<16} {len(sub):>5} rows  {dates.min().date()} to {dates.max().date()}  (OI rows: {n_oi})")
    print(f"\n  Saved to: {OUT_FILE}\n")
    return df


if __name__ == "__main__":
    pull()
