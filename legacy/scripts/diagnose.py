import sys
from datetime import date, timedelta
from pathlib import Path

import requests

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from scripts.auth import get_token

EXTRACT_URL = "https://selectapi.datascope.lseg.com/RestApi/v1/Extractions/ExtractWithNotes"

# All fields needed across both calls
FIELDS_STANDARD = ["RIC", "Trade Date", "Last", "Settlement Price"]
FIELDS_FX       = ["RIC", "Trade Date", "Bid", "Ask"]

FX_RICS = {"MYR=", "CNY="}

GROUPS = {
    "Futures": [
        "FCPOc1", "FCPOc2", "FCPOc3", "FCPOc5",
        "BOc1",   "BOc2",   "BOc3",   "BOc5",
        "LGOc1",  "LGOc2",  "LGOc3",  "LGOc5",
        "DCPc1",  "DCPc2",  "DCPc3",  "DCPc5",
        "DBYc1",  "DBYc2",  "DBYc3",  "DBYc5",
    ],
    "FX": [
        "MYR=", "CNY=",
    ],
    "Malaysia Cash": [
        "CPO-MYSTH-M1",  "CPO-MYSTH-M2",
        "POL-MYRBD-M1",  "POL-MYRBD-M2",
        "PO-MYRBD-M1",   "PO-MYRBD-M2",
        "PSTR-MYRBD-M1", "PSTR-MYRBD-M2",
        "PFAD-MY-M1",    "PFAD-MY-M2",
    ],
    "Indonesia Cash": [
        "CPO-ID-M1",     "CPO-ID-M2",
        "POL-IDRBD-M1",  "POL-IDRBD-M2",
        "PO-IDRBD-M1",   "PO-IDRBD-M2",
        "PSTR-IDRBD-M1", "PSTR-IDRBD-M2",
        "PFAD-ID-M1",    "PFAD-ID-M2",
    ],
}

ALL_RICS    = [ric for rics in GROUPS.values() for ric in rics]
STD_RICS    = [ric for ric in ALL_RICS if ric not in FX_RICS]


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Token {token}",
        "Content-Type": "application/json",
        "Prefer": "respond-async",
    }


def _build_body(rics: list[str], fields: list[str], start: str, end: str) -> dict:
    return {
        "ExtractionRequest": {
            "@odata.type": (
                "#DataScope.Select.Api.Extractions.ExtractionRequests"
                ".ElektronTimeseriesExtractionRequest"
            ),
            "ContentFieldNames": fields,
            "IdentifierList": {
                "@odata.type": (
                    "#DataScope.Select.Api.Extractions.ExtractionRequests"
                    ".InstrumentIdentifierList"
                ),
                "InstrumentIdentifiers": [
                    {"Identifier": ric, "IdentifierType": "Ric"} for ric in rics
                ],
                "ValidationOptions": None,
                "UseUserPreferencesForValidationOptions": False,
            },
            "Condition": {
                "ReportDateRangeType": "Range",
                "StartDate": f"{start}T00:00:00.000Z",
                "EndDate":   f"{end}T00:00:00.000Z",
            },
        }
    }


def _most_recent(contents: list[dict]) -> dict[str, dict]:
    """Return {ric: most_recent_row} from a flat list of rows."""
    by_ric: dict[str, list[dict]] = {}
    for row in contents:
        ric = row.get("RIC", "")
        by_ric.setdefault(ric, []).append(row)
    return {
        ric: sorted(rows, key=lambda r: r.get("Trade Date") or "")[-1]
        for ric, rows in by_ric.items()
    }


def _call(token: str, rics: list[str], fields: list[str], start: str, end: str) -> dict[str, dict]:
    resp = requests.post(
        EXTRACT_URL,
        headers=_headers(token),
        json=_build_body(rics, fields, start, end),
        timeout=60,
    )
    if not resp.ok:
        try:
            msg = resp.json()["error"]["message"]
        except Exception:
            msg = resp.text[:300]
        raise RuntimeError(f"Request failed ({resp.status_code}): {msg}")
    return _most_recent(resp.json().get("Contents", []))


def fetch_data(token: str) -> dict[str, dict]:
    start = (date.today() - timedelta(days=60)).isoformat()
    end   = date.today().isoformat()

    print(f"Fetching standard RICs ({len(STD_RICS)} RICs, Last + Settlement)...")
    std_data = _call(token, STD_RICS, FIELDS_STANDARD, start, end)

    print(f"Fetching FX RICs ({len(FX_RICS)} RICs, Bid + Ask)...")
    fx_data = _call(token, list(FX_RICS), FIELDS_FX, start, end)

    return {**std_data, **fx_data}


def _fmt(val) -> str:
    return str(val) if val is not None else "N/A"


def print_results(data: dict[str, dict]) -> None:
    col_w = 22

    for group, rics in GROUPS.items():
        is_fx = (group == "FX")
        val1_hdr = "Bid"   if is_fx else "Last"
        val2_hdr = "Ask"   if is_fx else "Settlement Price"
        val1_key = "Bid"   if is_fx else "Last"
        val2_key = "Ask"   if is_fx else "Settlement Price"

        print(f"\n{'=' * 62}")
        print(f"  {group}")
        print(f"{'=' * 62}")
        print(f"  {'RIC':<{col_w}} {val1_hdr:>12} {val2_hdr:>16}")
        print(f"  {'-' * 52}")

        for ric in rics:
            if ric not in data:
                print(f"  {ric:<{col_w}} NO DATA RETURNED")
            else:
                row  = data[ric]
                val1 = _fmt(row.get(val1_key))
                val2 = _fmt(row.get(val2_key))
                print(f"  {ric:<{col_w}} {val1:>12} {val2:>16}")


def main():
    token = get_token()
    print()
    data = fetch_data(token)
    print_results(data)
    print()


if __name__ == "__main__":
    main()