"""
Push three cash/physical vegoil prices (2022 -> latest) to a Google Sheet tab.

    date | BO_ARG_FOB_UPRIVER_USD | CPO_MY_LOCAL_M1_MYR | CPO_MY_LOCAL_M1_USD
         | SUNOIL_FOB_BLACKSEA_USD | MYR=

  BO-ARGUPR-C1    Soybean oil FOB Argentine Upriver c1   (USD/MT, native)
  CPO-MYSTH-M1    Local Malaysia (PK/PG) CPO M1          (MYR/MT native; USD/MT = / MYR=)
  KTS-SOUABS-FOB  Sunflower oil FOB Black Sea c1         (USD/MT, native)

Price = Last, falling back to Settlement Price (same rule as process.load_prices).
The Black Sea assessment is published sporadically, so it is forward-filled onto
the other series' trading dates (limit config.CASH_GSHEET_FFILL_DAYS) -- a stale
assessment is still the last known price. Rows before config.CASH_GSHEET_START
are dropped.

The whole tab is rebuilt from the raw CSV on every run (clear + one update), so
it is idempotent and self-healing. The tab is created in the workbook
config.DCP_GSHEET_ID if it does not exist; no other tab is touched. Same
no-op-on-missing-key contract as dcp_gsheet_push.

Public API
----------
  push_cash_prices() -> Optional[int]   rows written, or None when skipped
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
SCOPES   = ["https://www.googleapis.com/auth/spreadsheets"]

BO_ARG, CPO_MY, SUNOIL = "BO-ARGUPR-C1", "CPO-MYSTH-M1", "KTS-SOUABS-FOB"
CPO_MY_M2 = "CPO-MYSTH-M2"
# Palm cash legs already quoted in USD/MT by LSEG (no FX): RBD olein MY/ID, CPO Indonesia.
PALM_USD_RICS = {
    "POL_MY_FOB_M1_USD":  "POL-MYRBD-M1",  "POL_MY_FOB_M2_USD":  "POL-MYRBD-M2",
    "POL_ID_FOB_M1_USD":  "POL-IDRBD-M1",  "POL_ID_FOB_M2_USD":  "POL-IDRBD-M2",
    "CPO_ID_FOB_M1_USD":  "CPO-ID-M1",     "CPO_ID_FOB_M2_USD":  "CPO-ID-M2",
}
COLUMNS = ["BO_ARG_FOB_UPRIVER_USD", "CPO_MY_LOCAL_M1_MYR", "CPO_MY_LOCAL_M1_USD",
           "SUNOIL_FOB_BLACKSEA_USD", "MYR=",
           "CPO_MY_LOCAL_M2_USD", *PALM_USD_RICS]
PRICE_COLS = [c for c in COLUMNS if c not in ("MYR=", "CPO_MY_LOCAL_M1_USD", "CPO_MY_LOCAL_M2_USD")]


def build_table() -> pd.DataFrame:
    df = pd.read_csv(RAW_FILE, low_memory=False)
    df["date"] = pd.to_datetime(df["Trade Date"]).dt.normalize()
    for col in ("Last", "Settlement Price", "Bid", "Ask"):
        df[col] = pd.to_numeric(df.get(col), errors="coerce")
    df["price"] = df["Last"].fillna(df["Settlement Price"])

    def series(ric: str) -> pd.Series:
        s = df[df["RIC"] == ric].dropna(subset=["price"])
        return s.drop_duplicates("date", keep="last").set_index("date")["price"].sort_index()

    fx = df[df["RIC"] == "MYR="].drop_duplicates("date", keep="last").set_index("date").sort_index()
    myr = ((fx["Bid"] + fx["Ask"]) / 2).dropna()

    bo, cpo, sun = series(BO_ARG), series(CPO_MY), series(SUNOIL)
    cpo2 = series(CPO_MY_M2)
    palm = {col: series(ric) for col, ric in PALM_USD_RICS.items()}
    idx = bo.index.union(cpo.index).union(sun.index)
    for s in (cpo2, *palm.values()):
        idx = idx.union(s.index)
    idx = idx[idx >= pd.Timestamp(config.CASH_GSHEET_START)]

    out = pd.DataFrame(index=idx)
    out["BO_ARG_FOB_UPRIVER_USD"] = bo.reindex(idx)
    out["CPO_MY_LOCAL_M1_MYR"]    = cpo.reindex(idx)
    out["SUNOIL_FOB_BLACKSEA_USD"] = (
        sun.reindex(sun.index.union(idx)).ffill(limit=config.CASH_GSHEET_FFILL_DAYS).reindex(idx)
    )
    out["MYR="] = myr.reindex(myr.index.union(idx)).ffill().reindex(idx)
    out["CPO_MY_LOCAL_M1_USD"] = out["CPO_MY_LOCAL_M1_MYR"] / out["MYR="]
    out["CPO_MY_LOCAL_M2_USD"] = cpo2.reindex(idx) / out["MYR="]
    for col, s in palm.items():
        out[col] = s.reindex(idx)

    out = out[COLUMNS].round({c: 2 for c in COLUMNS if c != "MYR="} | {"MYR=": 4})
    return out.dropna(subset=PRICE_COLS, how="all")


def push_cash_prices() -> Optional[int]:
    if not config.DCP_GSHEET_ID:
        logger.warning("Cash push: config.DCP_GSHEET_ID is blank — skipping.")
        return None
    if not SA_PATH.exists():
        logger.warning("Cash push: no service-account key at %s — skipping.", SA_PATH)
        return None
    try:
        from google.oauth2.service_account import Credentials
        from googleapiclient.discovery import build
    except Exception as exc:  # pragma: no cover
        logger.warning("Cash push: google api libs missing (%s).", exc)
        return None

    tbl = build_table()
    if tbl.empty:
        logger.warning("Cash push: no rows to write.")
        return None

    rows = [
        [d.strftime("%Y-%m-%d")] + [None if pd.isna(v) else float(v) for v in vals]
        for d, vals in zip(tbl.index, tbl.to_numpy())
    ]
    body = {"values": [["date"] + COLUMNS] + rows}

    creds = Credentials.from_service_account_file(str(SA_PATH), scopes=SCOPES)
    svc = build("sheets", "v4", credentials=creds, cache_discovery=False)
    sid, tab = config.DCP_GSHEET_ID, config.CASH_GSHEET_TAB

    meta = svc.spreadsheets().get(spreadsheetId=sid).execute()
    if tab not in [s["properties"]["title"] for s in meta.get("sheets", [])]:
        svc.spreadsheets().batchUpdate(
            spreadsheetId=sid,
            body={"requests": [{"addSheet": {"properties": {"title": tab}}}]},
        ).execute()
        logger.info("Cash push: created tab %r.", tab)

    svc.spreadsheets().values().clear(spreadsheetId=sid, range=tab).execute()
    svc.spreadsheets().values().update(
        spreadsheetId=sid, range=f"{tab}!A1",
        valueInputOption="USER_ENTERED", body=body,
    ).execute()
    logger.info("Cash push: wrote %d rows to sheet %s tab %r.", len(rows), sid, tab)
    return len(rows)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    n = push_cash_prices()
    if n is None:
        print("Skipped (missing credential, libs, or sheet ID).")
    else:
        t = build_table()
        print(f"Wrote {n} rows ({t.index.min().date()} -> {t.index.max().date()}). Latest:")
        print(t.tail(3).to_string())
