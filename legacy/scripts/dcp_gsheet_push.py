"""
Push a settlement-price curve to the "Dalian Px" Google Sheet tab.

Each daily run reads the raw LSEG pull, takes the **Settlement Price** for every
contract in config.DCP_GSHEET_CONTRACTS, and writes a date-rows x contract-cols
table to a Google Sheet. Units differ by product group (MIXED — not all USD/MT):

    date | DCPc1..7   (native CNY/MT)
         | DBYc1..7   (native CNY/MT)
         | FCPOc1..12 (native MYR/MT)
         | LGOc1..6   (USD/MT, already)
         | BOc1..6    (CBOT soyoil, cents/lb → USD/MT, × config.CENTSLB_CONV)
         | CNY= MYR=  (FX mid rate, (Bid+Ask)/2)

Only BO is unit-converted; Dalian (DCP/DBY) and MDEX (FCPO) are left in their
native exchange currency, ICE gas oil (LGO) is already USD/MT, and the CNY=/MYR=
FX mids are written as their own rate columns. No FX conversion is applied to
prices. The whole tab is rebuilt from full history on every run (one
values().update), so it is idempotent and self-healing — no append/dedup
bookkeeping.

Authentication
--------------
Reuses the Google service-account key at:

    LSEGDashboard/secrets/gsheet_service_account.json

but with **write** scope (spreadsheets, not spreadsheets.readonly). Create the
key (see secrets/README.txt), then **share the destination sheet as Editor**
with the service-account email. Set config.DCP_GSHEET_ID to the sheet's ID.

If the key is missing, the libs are absent, or DCP_GSHEET_ID is blank, the push
is a no-op (returns None) and never breaks the price pull — same contract as the
Tradable Prem tab.

Public API
----------
  push_dcp_prices() -> Optional[int]
      Live push. Returns the number of data rows written, or None when skipped.
"""

import logging
import sys
from pathlib import Path
from typing import Optional

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config

logger = logging.getLogger(__name__)

RAW_FILE = ROOT / "data" / "raw" / "all_instruments_raw.csv"
SA_PATH  = ROOT / "secrets" / "gsheet_service_account.json"
SCOPES   = ["https://www.googleapis.com/auth/spreadsheets"]   # write scope

CONTRACTS = config.DCP_GSHEET_CONTRACTS
FX_RICS   = set(config.RICS["fx"])   # {"MYR=", "CNY="} — written as mid-rate columns


# ── Build the USD/MT settlement table ─────────────────────────────────────────

def build_table() -> pd.DataFrame:
    """Return a wide DataFrame indexed by date with one column per contract in
    CONTRACTS. Futures use **Settlement Price**; FX (CNY=/MYR=) use the
    (Bid+Ask)/2 **mid**. Units are MIXED by product group:
      - BO  (CBOT soyoil, cents/lb) → USD/MT   (× config.CENTSLB_CONV)
      - DCP / DBY (Dalian)          → native CNY/MT  (no conversion)
      - FCPO (MDEX)                 → native MYR/MT   (no conversion)
      - LGO  (ICE gas oil)          → USD/MT          (already, pass-through)
      - CNY= / MYR=                 → FX mid rate (own columns; prices unconverted)
    Rows with no data at all are dropped."""
    df = pd.read_csv(RAW_FILE, parse_dates=["Trade Date"])
    df = df.rename(columns={"Trade Date": "date"})
    df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    for col in ("Settlement Price", "Bid", "Ask"):
        df[col] = pd.to_numeric(df.get(col), errors="coerce")

    # Futures: Settlement Price. Only BO is unit-converted (cents/lb → USD/MT);
    # Dalian/MDEX stay native, LGO is already USD/MT.
    fut_rics = [r for r in CONTRACTS if r not in FX_RICS]
    fut = df[df["RIC"].isin(fut_rics)]
    wide = fut.pivot_table(index="date", columns="RIC", values="Settlement Price").sort_index()

    out = pd.DataFrame(index=wide.index)
    for ric in wide.columns:
        if ric in config.CENTSLB_RICS:
            out[ric] = (wide[ric] * config.CENTSLB_CONV).round(2)
        else:
            out[ric] = wide[ric].round(2)

    # FX rates: (Bid+Ask)/2 mid, forward-filled onto the price dates (4 dp).
    for ric in (r for r in CONTRACTS if r in FX_RICS):
        fx = df[df["RIC"] == ric].set_index("date").sort_index()
        mid = (fx["Bid"] + fx["Ask"]) / 2
        if mid.dropna().empty:
            logger.warning("DCP push: %s FX rate not found in raw CSV — column left blank.", ric)
            out[ric] = pd.NA
        else:
            out[ric] = mid.reindex(out.index.union(mid.index)).ffill().reindex(out.index).round(4)

    out = out.reindex(columns=CONTRACTS)            # stable column order
    out = out.dropna(how="all")
    return out


# ── Write to the sheet ────────────────────────────────────────────────────────

def push_dcp_prices() -> Optional[int]:
    """Rebuild the DCP tab from full history. Returns rows written, or None when
    skipped (no key / libs / sheet ID)."""
    if not config.DCP_GSHEET_ID:
        logger.warning("DCP push: config.DCP_GSHEET_ID is blank — skipping.")
        return None
    if not SA_PATH.exists():
        logger.warning("DCP push: no service-account key at %s — skipping.", SA_PATH)
        return None
    try:
        from google.oauth2.service_account import Credentials
        from googleapiclient.discovery import build
    except Exception as exc:  # pragma: no cover
        logger.warning("DCP push: google api libs missing (%s).", exc)
        return None

    usd = build_table()
    if usd.empty:
        logger.warning("DCP push: no DCP settlement rows to write.")
        return None

    header = ["date"] + CONTRACTS
    rows = [
        [d.strftime("%Y-%m-%d")] + [None if pd.isna(v) else float(v) for v in vals]
        for d, vals in zip(usd.index, usd.to_numpy())
    ]
    body = {"values": [header] + rows}

    creds = Credentials.from_service_account_file(str(SA_PATH), scopes=SCOPES)
    service = build("sheets", "v4", credentials=creds, cache_discovery=False)

    # Resolve the target tab by gid so only that one tab is touched — every other
    # tab on the workbook is left exactly as-is.
    meta = service.spreadsheets().get(spreadsheetId=config.DCP_GSHEET_ID).execute()
    tab = next(
        (s["properties"]["title"] for s in meta.get("sheets", [])
         if s["properties"].get("sheetId") == config.DCP_GSHEET_GID),
        None,
    )
    if tab is None:
        logger.warning("DCP push: gid %s not found in sheet — skipping.", config.DCP_GSHEET_GID)
        return None

    # Clear stale cells in this tab only, then write the full grid in one shot.
    service.spreadsheets().values().clear(
        spreadsheetId=config.DCP_GSHEET_ID, range=tab,
    ).execute()
    service.spreadsheets().values().update(
        spreadsheetId=config.DCP_GSHEET_ID,
        range=f"{tab}!A1",
        valueInputOption="USER_ENTERED",
        body=body,
    ).execute()

    logger.info("DCP push: wrote %d rows (%s) to sheet %s tab %r.",
                len(rows), ", ".join(CONTRACTS), config.DCP_GSHEET_ID, tab)
    return len(rows)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("Pushing DCP settlement curve (mixed native / USD-MT units) to Google Sheet...")
    n = push_dcp_prices()
    if n is None:
        print("Skipped (missing credential, libs, or DCP_GSHEET_ID).")
    else:
        usd = build_table()
        print(f"Wrote {n} rows. Latest:")
        print(usd.tail(3).to_string())
