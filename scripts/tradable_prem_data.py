"""
Tradable Premium data for the dashboard's "Tradable Prem" sub-tab.

Pulls the "Tradeable Prices Pull" Google Sheet (private, gid 0) and computes, per
product, the premium  =  Tradable price  -  raw Reuters price, separately for
Malaysia (Reuters Msia) and Indonesia (Reuters Indo). Raw columns only (no
smoothed series), so non-trading days simply have no point. USD/MT.

Authentication
--------------
The sheet is private, so the headless daily pull authenticates with a Google
service-account key placed at:

    LSEGDashboard/secrets/gsheet_service_account.json

Share the sheet (Viewer) with the service-account email. If the key is absent
the fetch is a no-op (the dashboard shows "awaiting credential"), so it never
breaks the price pull.

Public API
----------
  fetch_tradable_prem() -> Optional[pd.DataFrame]
      Live pull; writes the tidy cache CSV. Returns the tidy df or None.
  load_tradable_prem()  -> tuple[dict[str, pd.DataFrame], list[int]]
      Read the cache and return ({key: chart_df}, sorted_years) for the
      dashboard. Graceful ({}, []) when the cache is missing.

Cache format (data/processed/tradable_prem.csv): tidy rows of
  region, product, date, premium
where region in {MY, ID}, product in TRADABLE_PREM_PRODUCTS.
"""

import logging
import re
import sys
from pathlib import Path
from typing import Optional

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config

logger = logging.getLogger(__name__)

SA_PATH    = ROOT / "secrets" / "gsheet_service_account.json"
CACHE_PATH = ROOT / "data" / "processed" / "tradable_prem.csv"
SCOPES     = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

PRODUCTS = config.TRADABLE_PREM_PRODUCTS
REGIONS  = ["MY", "ID"]

REGION_LABEL = {"MY": "Malaysia", "ID": "Indonesia"}


# ── Parsing helpers ───────────────────────────────────────────────────────────

def _num(cell) -> Optional[float]:
    """Parse a formatted spreadsheet cell to float. Handles thousands commas,
    parentheses-negatives, blanks and stray spaces."""
    if cell is None:
        return None
    s = str(cell).strip()
    if not s or s in {"-", "#N/A", "N/A", "NA", "#REF!", "#DIV/0!"}:
        return None
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()").replace(",", "").replace(" ", "")
    try:
        v = float(s)
    except ValueError:
        return None
    return -v if neg else v


# A raw price that sits more than this fraction away from the median of its
# surrounding prints is treated as a sheet typo (e.g. 168 for 1,168) and dropped.
OUTLIER_TOL = 0.25
OUTLIER_WINDOW = 5  # prints on each side


def _drop_outliers(vals: list, label: str = "") -> list:
    """Blank out isolated typo prices. Compares each print with the median of
    the nearest OUTLIER_WINDOW non-blank prints either side (itself excluded),
    so weekends/holidays don't matter. Dropped, never "corrected"."""
    idx = [i for i, v in enumerate(vals) if v is not None]
    out = list(vals)
    for k, i in enumerate(idx):
        nb = [vals[j] for j in idx[max(0, k - OUTLIER_WINDOW):k] + idx[k + 1:k + 1 + OUTLIER_WINDOW]]
        if len(nb) < 3:
            continue
        med = sorted(nb)[len(nb) // 2]
        if med and abs(vals[i] - med) / abs(med) > OUTLIER_TOL:
            logger.warning("Tradable Prem: dropped outlier %s idx %d value %s (neighbour median %s)",
                           label, i, vals[i], med)
            out[i] = None
    return out


def _ffill_row(row: list, ncols: int) -> list:
    """Left-to-right forward fill to reconstruct merged header cells: a merged
    range stores its value only in the top-left cell, blanks elsewhere."""
    out, last = [], ""
    for c in range(ncols):
        v = (row[c].strip() if c < len(row) and row[c] is not None else "")
        if v:
            last = v
        out.append(last)
    return out


_DATE_RE = re.compile(r"\d{1,2}\s+[A-Za-z]{3,9}\s+\d{2,4}|\d{1,2}[/-]\d{1,2}[/-]\d{2,4}")


def _parse_date(cell) -> Optional[pd.Timestamp]:
    if cell is None:
        return None
    s = str(cell).strip()
    if not _DATE_RE.search(s):
        return None
    t = pd.to_datetime(s, dayfirst=True, errors="coerce")
    return None if pd.isna(t) else t


def _locate_columns(values: list) -> tuple[int, int, dict]:
    """Return (header_row, date_col, {series_key: {product: col_index}}).

    The header row is the early row with the most cells reading exactly
    "Tradable". The product code (CPO/RPO/...) sits on the row above and is
    forward-filled across each block. Only the RAW columns are matched: the
    exact header text "Reuters Indo" / "Reuters Msia" / "Tradable" (the smoothed
    twins read "... (Smooth)" and the premium columns carry a product prefix, so
    neither matches). series_key is one of TRADABLE, MY, ID.
    """
    ncols = max((len(r) for r in values), default=0)

    def cell(r: int, c: int) -> str:
        return (values[r][c] or "").strip() if c < len(values[r]) else ""

    hrow, best = 0, -1
    for i, row in enumerate(values[:15]):
        cnt = sum(1 for c in row if (c or "").strip().lower() == "tradable")
        if cnt > best:
            best, hrow = cnt, i
    if hrow == 0:
        return hrow, 0, {}
    products = _ffill_row(values[hrow - 1], ncols)

    date_col = next((c for c in range(ncols) if cell(hrow, c).lower() == "date"), 0)

    wanted = {"reuters indo": "ID", "reuters msia": "MY", "tradable": "TRADABLE"}
    found: dict[str, dict] = {"TRADABLE": {}, "MY": {}, "ID": {}}
    for c in range(ncols):
        key = wanted.get(cell(hrow, c).lower())
        if key and products[c] in PRODUCTS:
            found[key].setdefault(products[c], c)  # first match wins
    return hrow, date_col, found


# ── Fetch ─────────────────────────────────────────────────────────────────────

def _read_grid() -> Optional[list]:
    """Authenticate with the service account and return the target tab as a
    list-of-rows grid. Returns None if the credential is missing/unusable."""
    if not SA_PATH.exists():
        logger.warning("Tradable Prem: no service-account key at %s — skipping.", SA_PATH)
        return None
    try:
        from google.oauth2.service_account import Credentials
        from googleapiclient.discovery import build
    except Exception as exc:  # pragma: no cover
        logger.warning("Tradable Prem: google api libs missing (%s).", exc)
        return None

    creds = Credentials.from_service_account_file(str(SA_PATH), scopes=SCOPES)
    service = build("sheets", "v4", credentials=creds, cache_discovery=False)

    meta = service.spreadsheets().get(
        spreadsheetId=config.TRADABLE_PREM_SHEET_ID
    ).execute()
    title = next(
        (s["properties"]["title"] for s in meta.get("sheets", [])
         if s["properties"].get("sheetId") == config.TRADABLE_PREM_GID),
        None,
    )
    if title is None:
        logger.warning("Tradable Prem: gid %s not found in sheet.", config.TRADABLE_PREM_GID)
        return None

    resp = service.spreadsheets().values().get(
        spreadsheetId=config.TRADABLE_PREM_SHEET_ID,
        range=title,
        valueRenderOption="FORMATTED_VALUE",
        dateTimeRenderOption="FORMATTED_STRING",
    ).execute()
    return resp.get("values", [])


def fetch_tradable_prem(verbose: bool = False) -> Optional[pd.DataFrame]:
    """Live pull → compute premiums → write the tidy cache CSV. Returns the
    tidy DataFrame, or None if the sheet could not be read."""
    grid = _read_grid()
    if not grid:
        return None

    prow, date_col, cols = _locate_columns(grid)
    if verbose:
        print(f"  product-label row: {prow}   date col: {date_col}")
        for key, m in cols.items():
            print(f"  {key:<12} -> " + ", ".join(f"{p}@{c}" for p, c in m.items()))

    data = grid[prow + 1:]
    dates = [_parse_date(r[date_col] if date_col < len(r) else None) for r in data]

    def series(block_key: str, product: str) -> list:
        c = cols.get(block_key, {}).get(product)
        if c is None:
            return [None] * len(data)
        raw = [_num(r[c]) if c < len(r) else None for r in data]
        return _drop_outliers(raw, f"{block_key}/{product}")

    records = []
    for product in PRODUCTS:
        trad = series("TRADABLE", product)
        for region in REGIONS:
            reut = series(region, product)
            for d, t, rv in zip(dates, trad, reut):
                if d is None or t is None or rv is None:
                    continue
                records.append({
                    "region": region, "product": product,
                    "date": d, "premium": t - rv,
                })

    tidy = pd.DataFrame.from_records(
        records, columns=["region", "product", "date", "premium"]
    )
    if tidy.empty:
        logger.warning("Tradable Prem: parsed 0 premium rows — check column mapping.")
        return None

    tidy = tidy.sort_values(["region", "product", "date"])
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tidy.to_csv(CACHE_PATH, index=False)
    logger.info("Tradable Prem: %d rows across %d series written to %s.",
                len(tidy), tidy.groupby(["region", "product"]).ngroups, CACHE_PATH)
    return tidy


# ── Load for dashboard ────────────────────────────────────────────────────────

def _to_chart_df(series: pd.Series) -> pd.DataFrame:
    """date-indexed Series → standard seasonal-chart DataFrame (matches
    gsheet_data._to_chart_df so make_chart() can consume it directly)."""
    df = series.dropna().reset_index()
    df.columns = ["date", "spread_value"]
    df["year"] = df["date"].dt.year
    df["day_of_year"] = df["date"].dt.dayofyear
    df["month_day"] = df["date"].dt.strftime("%m-%d")
    return df


def load_tradable_prem() -> tuple[dict, list]:
    """Read the cache → ({'TP_<region>_<product>': chart_df}, [years])."""
    if not CACHE_PATH.exists():
        logger.info("Tradable Prem: no cache at %s.", CACHE_PATH)
        return {}, []
    df = pd.read_csv(CACHE_PATH, parse_dates=["date"])
    out, years = {}, set()
    for (region, product), g in df.groupby(["region", "product"]):
        cdf = _to_chart_df(g.set_index("date")["premium"])
        if cdf.empty:
            continue
        out[f"TP_{region}_{product}"] = cdf
        years |= set(cdf["year"].unique().tolist())
    return out, sorted(years)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("Fetching Tradable Premium data...")
    result = fetch_tradable_prem(verbose=True)
    if result is None:
        print("No data (missing credential or unreadable sheet).")
    else:
        print(f"\nWrote {len(result)} rows to {CACHE_PATH}")
        for (region, product), g in result.groupby(["region", "product"]):
            last = g.dropna(subset=["premium"]).tail(1)
            if not last.empty:
                row = last.iloc[0]
                print(f"  {region} {product:<5} "
                      f"{row['date'].date()}  premium {row['premium']:.1f}  ({len(g)} rows)")
