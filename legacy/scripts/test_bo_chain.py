"""
Diagnostic: discover CBOT Soybean Oil option RIC format via 0#BO++ chain.
Uses TickHistoryTimeAndSalesExtractionRequest (not Elektron).
Read-only — no files written.
"""

import gzip
import io
import json
import sys
import time
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from scripts.auth import get_token

EXTRACT_URL = "https://selectapi.datascope.lseg.com/RestApi/v1/Extractions/ExtractRaw"
POLL_INTERVAL = 10


def _headers(token: str, accept_gzip: bool = False) -> dict:
    h = {
        "Authorization": f"Token {token}",
        "Content-Type": "application/json",
        "Prefer": "respond-async",
    }
    if accept_gzip:
        h["Accept-Encoding"] = "gzip"
    return h


def _build_body() -> dict:
    return {
        "ExtractionRequest": {
            "@odata.type": (
                "#DataScope.Select.Api.Extractions.ExtractionRequests"
                ".TickHistoryTimeAndSalesExtractionRequest"
            ),
            "ContentFieldNames": [
                "Trade - Price",
                "Trade - Volume",
                "Trade - Exchange Time",
            ],
            "IdentifierList": {
                "@odata.type": (
                    "#DataScope.Select.Api.Extractions.ExtractionRequests"
                    ".InstrumentIdentifierList"
                ),
                "InstrumentIdentifiers": [
                    {"Identifier": "0#BO++", "IdentifierType": "ChainRIC"}
                ],
                "ValidationOptions": {"AllowHistoricalInstruments": True},
                "UseUserPreferencesForValidationOptions": False,
            },
            "Condition": {
                "MessageTimeStampIn": "GmtUtc",
                "ApplyCorrectionsAndCancellations": False,
                "ReportDateRangeType": "Range",
                "TimeRangeMode": "Window",
                "QueryStartDate": "2026-03-24T00:00:00.000Z",
                "QueryEndDate": "2026-03-24T23:59:00.000Z",
                "DisplaySourceRIC": True,
            },
        }
    }


def _poll(location: str, token: str) -> requests.Response:
    """Poll until we get a 200 back, return the raw Response."""
    attempt = 0
    while True:
        attempt += 1
        print(f"  Polling... (attempt {attempt})", end="\r", flush=True)
        time.sleep(POLL_INTERVAL)
        resp = requests.get(
            location,
            headers=_headers(token, accept_gzip=True),
            timeout=(30, 600),
            stream=True,
        )
        if resp.status_code == 200:
            print(f"  Polling... done (attempt {attempt})")
            return resp
        if resp.status_code != 202:
            raise RuntimeError(
                f"Unexpected status while polling ({resp.status_code}): "
                f"{resp.text[:300]}"
            )


def _decompress_response(resp: requests.Response) -> dict:
    """Handle both gzipped and plain JSON responses."""
    raw = resp.content
    # Try gzip first
    try:
        decompressed = gzip.decompress(raw)
        return json.loads(decompressed)
    except (gzip.BadGzipFile, OSError):
        pass
    # Fall back to plain JSON
    return json.loads(raw)


def main():
    token = get_token()

    print(f"\n{'='*60}")
    print("  CBOT Soybean Oil Option Chain Discovery")
    print("  Chain RIC: 0#BO++")
    print("  Date: 2026-03-24")
    print(f"{'='*60}\n")

    # ── Submit request ───────────────────────────────────────────────────────
    print("  Submitting extraction request...")
    body = _build_body()

    resp = requests.post(
        EXTRACT_URL,
        headers=_headers(token),
        json=body,
        timeout=(30, 600),
    )

    if resp.status_code == 202:
        location = resp.headers.get("Location")
        if not location:
            raise RuntimeError("Got 202 but no Location header.")
        print(f"  Request accepted (202). Polling...")
        resp = _poll(location, token)

    elif resp.status_code != 200:
        try:
            msg = resp.json()["error"]["message"]
        except Exception:
            msg = resp.text[:500]
        raise RuntimeError(f"Request failed ({resp.status_code}): {msg}")

    # ── Parse response ───────────────────────────────────────────────────────
    # ExtractRaw returns JSON with JobId and Notes at top level,
    # and the actual data behind a results URL.
    # But for small extractions it may return inline.
    data = _decompress_response(resp)

    notes = data.get("Notes", [])
    if notes:
        print(f"\n  API Notes:\n    {str(notes)[:800]}")

    # ExtractRaw: check for JobId → need to fetch results separately
    job_id = data.get("JobId")
    contents = data.get("Contents", [])

    if job_id and not contents:
        print(f"\n  JobId: {job_id}")
        # Fetch the actual results
        results_url = (
            f"https://selectapi.datascope.lseg.com/RestApi/v1"
            f"/Extractions/RawExtractionResults('{job_id}')/$value"
        )
        print("  Fetching raw results...")
        results_resp = requests.get(
            results_url,
            headers=_headers(token, accept_gzip=True),
            timeout=(30, 600),
            stream=True,
        )
        if results_resp.status_code != 200:
            raise RuntimeError(
                f"Failed to fetch results ({results_resp.status_code}): "
                f"{results_resp.text[:300]}"
            )

        # Results come as CSV (possibly gzipped)
        raw_bytes = results_resp.content
        try:
            raw_bytes = gzip.decompress(raw_bytes)
        except (gzip.BadGzipFile, OSError):
            pass

        csv_text = raw_bytes.decode("utf-8-sig")
        df = pd.read_csv(io.StringIO(csv_text))
        print(f"  Results fetched ({len(df):,} rows).")

    elif contents:
        df = pd.DataFrame(contents)

    else:
        print("\n  >> No data returned. Chain may be empty or invalid.")
        print(f"  >> Response keys: {list(data.keys())}")
        raw_str = json.dumps(data, indent=2)[:2000]
        print(f"  >> Raw response (first 2000 chars):\n{raw_str}")
        return
    print(f"\n  Total trade records: {len(df):,}")
    print(f"  Raw columns: {list(df.columns)}")

    # ── Identify the RIC column ──────────────────────────────────────────────
    # DisplaySourceRIC=true should add a column with the resolved RIC
    ric_col = None
    for candidate in ["RIC", "Source RIC", "#RIC", "Identifier"]:
        if candidate in df.columns:
            ric_col = candidate
            break
    if ric_col is None:
        # Just use whichever column has string values that look like RICs
        for col in df.columns:
            sample = df[col].dropna().head(5).tolist()
            if sample and all(isinstance(s, str) and len(s) > 2 for s in sample):
                ric_col = col
                break

    if ric_col:
        unique_rics = sorted(df[ric_col].dropna().unique())
        print(f"\n  Unique RICs ({len(unique_rics)} total):")
        for ric in unique_rics[:80]:
            print(f"    {ric}")
        if len(unique_rics) > 80:
            print(f"    ... and {len(unique_rics) - 80} more")
    else:
        print("\n  WARNING: Could not identify RIC column!")
        unique_rics = []

    # ── Raw head ─────────────────────────────────────────────────────────────
    print(f"\n  --- Raw DataFrame head (10 rows) ---")
    with pd.option_context("display.max_columns", None, "display.width", 200):
        print(df.head(10).to_string(index=False))
    print("  ---")

    # ── Per-RIC summary ──────────────────────────────────────────────────────
    if ric_col and unique_rics:
        price_col = None
        vol_col = None
        for col in df.columns:
            cl = col.lower()
            if "price" in cl and price_col is None:
                price_col = col
            if "volume" in cl and vol_col is None:
                vol_col = col

        print(f"\n  {'RIC':<20} {'Last Price':>12} {'Total Vol':>12} {'Trades':>8}")
        print(f"  {'-'*18}  {'-'*12} {'-'*12} {'-'*8}")

        for ric in unique_rics[:60]:
            sub = df[df[ric_col] == ric]
            n = len(sub)

            lp = "—"
            if price_col:
                prices = pd.to_numeric(sub[price_col], errors="coerce").dropna()
                prices = prices[prices > 0]
                if not prices.empty:
                    lp = f"{prices.iloc[-1]:.4f}"

            tv = "—"
            if vol_col:
                vols = pd.to_numeric(sub[vol_col], errors="coerce").dropna()
                total = int(vols.sum()) if not vols.empty else 0
                tv = f"{total:,}" if total > 0 else "0"

            print(f"  {ric:<20} {lp:>12} {tv:>12} {n:>8}")

        if len(unique_rics) > 60:
            print(f"  ... {len(unique_rics) - 60} more RICs omitted")

    print(f"\n{'='*60}")
    print("  Diagnostic complete.")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()