"""
Parity tab -> Ind/China sub-section: forward-curve snapshot data.

Pulls two small, hand-maintained Google Sheets that each give a short rolling
snapshot table (Today + 4 prior days, Today + 4 prior weeks) of forward-curve
values for palm-complex parity spreads:

  - China ("Palm Olein VS CSBO" tab): Label, Date, Nearest, Spot, M1-M4.
  - India (same workbook that feeds gsheet_data.py's India Local tab, a
    different gid): 3 instruments -- Olein Eq - CSBO, Olein Eq - CSFO
    (Black Sea), CSBO - CSFO -- each with its own Date/Nearest/M1.. block.
    This sheet has NO numeric Spot column (only two date columns), so the
    India x-axis starts at M1. Olein Eq - CSBO runs M1-M6; the other two
    only go to M4 -- each instrument's chart just uses whatever maturities
    its block actually has.

Both sheets are public (CSV export works with no auth) -- same pattern as
gsheet_data.py, no service-account key needed. "#DIV/0!" cells (thin-
liquidity months with no quote) are coerced to NaN and dropped from that
row's x/y, so the line has a gap there instead of a fake 0.

Public API
----------
  fetch_china_parity() -> Optional[dict]
      {"daily": [...], "weekly": [...]}
  fetch_india_parity() -> Optional[dict]
      {instrument_key: {"daily": [...], "weekly": [...]}}

Each list is [{"label": str, "x": [...], "y": [...]}, ...], ordered
Today, D-1..D-4 (or Today, W-1..W-4), Today first -- ready for a
forward-curve-style chart (x = category, one line per row).
"""

import logging
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

CHINA_PARITY_URL = (
    "https://docs.google.com/spreadsheets/d/"
    "16r7vkke3Kc-TVB4vKsJI01ndTRxIeiaO4i6FoHYfhE4/export?format=csv&gid=466961326"
)
INDIA_PARITY_URL = (
    "https://docs.google.com/spreadsheets/d/"
    "1tY63YWHUm8LHEKUJAtObl4zsIiAyeVmZ/export?format=csv&gid=1151879540"
)

CHINA_PARITY_KEY   = "CHINA_PARITY_OLEIN_CSBO"
CHINA_PARITY_TITLE = "China: Palm Olein vs CSBO"

INDIA_PARITY_INSTRUMENTS = {
    # 2026-09-28: sheet renamed "Olein Eq - CSBO"/"Olein Eq - CSFO (Black Sea)"
    # to "Olein Eq - SBO"/"Olein Eq - SFO (Black Sea)" (dropped the "C") --
    # keep both spellings mapped so a future revert (or partial re-rename)
    # doesn't silently drop the chart again.
    "Olein Eq - CSBO":             "INDIA_PARITY_OLEIN_CSBO",
    "Olein Eq - SBO":              "INDIA_PARITY_OLEIN_CSBO",
    "Olein Eq - CSFO (Black Sea)": "INDIA_PARITY_OLEIN_CSFO",
    "Olein Eq - SFO (Black Sea)":  "INDIA_PARITY_OLEIN_CSFO",
    "CSBO - CSFO":                 "INDIA_PARITY_CSBO_CSFO",
}
INDIA_PARITY_TITLES = {
    "INDIA_PARITY_OLEIN_CSBO": "India: Palm Olein Eq vs CSBO",
    "INDIA_PARITY_OLEIN_CSFO": "India: Palm Olein Eq vs CSFO (Black Sea)",
    "INDIA_PARITY_CSBO_CSFO":  "India: CSBO vs CSFO (Black Sea)",
}
INDIA_PARITY_KEYS = list(dict.fromkeys(INDIA_PARITY_INSTRUMENTS.values()))  # dedupe aliases, keep order

_MATURITY_TOKENS = ("Spot", "M1", "M2", "M3", "M4", "M5", "M6")


def _num(v):
    return pd.to_numeric(v, errors="coerce")


def _row_records(df: pd.DataFrame, header_row: int, x_cols: list, x_labels: list) -> list:
    """5 data rows right after `header_row`: row label in col 0, values in
    `x_cols`. Non-numeric cells (#DIV/0!, blank) drop out of that row's x/y
    entirely -- a gap in the line, not a fake 0."""
    records = []
    for r in range(header_row + 1, min(header_row + 6, len(df))):
        row = df.iloc[r]
        label = row[0]
        if pd.isna(label):
            continue
        vals = [_num(row[c]) for c in x_cols]
        xs = [lbl for lbl, v in zip(x_labels, vals) if pd.notna(v)]
        ys = [v for v in vals if pd.notna(v)]
        if not xs:
            continue
        records.append({"label": str(label).strip(), "x": xs, "y": ys})
    return records


def fetch_china_parity() -> Optional[dict]:
    """China 'Palm Olein VS CSBO' tab -> {"daily": [...], "weekly": [...]}."""
    try:
        df = pd.read_csv(CHINA_PARITY_URL, header=None)
    except Exception as exc:
        logger.warning("China Parity sheet fetch failed: %s", exc)
        return None

    header_rows = [
        r for r in range(len(df))
        if str(df.iloc[r].get(1, "")).strip() == "Date"
        and str(df.iloc[r].get(2, "")).strip() == "Nearest"
        and str(df.iloc[r].get(3, "")).strip() == "Spot"
    ]
    if len(header_rows) < 2:
        logger.warning("China Parity sheet: expected 2 header rows (daily/weekly), found %d",
                       len(header_rows))
        return None

    result = {}
    for block_name, hr in zip(("daily", "weekly"), header_rows[:2]):
        header = df.iloc[hr]
        x_cols = [c for c in range(3, len(header)) if str(header[c]).strip() in _MATURITY_TOKENS]
        x_labels = [str(header[c]).strip() for c in x_cols]
        result[block_name] = _row_records(df, hr, x_cols, x_labels)

    logger.info("China Parity: %d daily rows, %d weekly rows.",
               len(result.get("daily", [])), len(result.get("weekly", [])))
    return result


def fetch_india_parity() -> Optional[dict]:
    """India workbook, Parity gid -> {instrument_key: {"daily": [...], "weekly": [...]}}."""
    try:
        df = pd.read_csv(INDIA_PARITY_URL, header=None)
    except Exception as exc:
        logger.warning("India Parity sheet fetch failed: %s", exc)
        return None

    header_rows = [
        r for r in range(1, len(df))
        if str(df.iloc[r].get(1, "")).strip() == "Date"
        and str(df.iloc[r].get(2, "")).strip() == "Nearest"
    ]
    if len(header_rows) < 2:
        logger.warning("India Parity sheet: expected 2 header rows (daily/weekly), found %d",
                       len(header_rows))
        return None

    result = {key: {} for key in INDIA_PARITY_KEYS}
    for block_name, hr in zip(("daily", "weekly"), header_rows[:2]):
        group_row = df.iloc[hr - 1]
        header = df.iloc[hr]
        # Each instrument's column block starts at a non-null group-row cell
        # (merged-cell CSV export leaves only the first cell of a merge
        # populated) and runs up to the next non-null one.
        starts = [c for c in range(3, len(df.columns)) if pd.notna(group_row[c])]
        for i, start in enumerate(starts):
            end = starts[i + 1] if i + 1 < len(starts) else len(df.columns)
            name = str(group_row[start]).strip()
            key = INDIA_PARITY_INSTRUMENTS.get(name)
            if key is None:
                continue
            x_cols = list(range(start, end))
            x_labels = [str(header[c]).strip() for c in x_cols]
            result[key][block_name] = _row_records(df, hr, x_cols, x_labels)

    loaded = sum(1 for v in result.values() if v.get("daily") or v.get("weekly"))
    logger.info("India Parity: %d/%d instruments loaded.", loaded, len(INDIA_PARITY_KEYS))
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    cn = fetch_china_parity()
    print("--- China Parity ---")
    if cn:
        for block, records in cn.items():
            print(f"  {block}: {len(records)} rows")
            for rec in records:
                print(f"    {rec['label']:>6}: {dict(zip(rec['x'], [round(v, 2) for v in rec['y']]))}")
    else:
        print("  FAILED")

    print()
    ind = fetch_india_parity()
    print("--- India Parity ---")
    if ind:
        for key, blocks in ind.items():
            print(f"  {INDIA_PARITY_TITLES[key]}  [{key}]")
            for block, records in blocks.items():
                print(f"    {block}: {len(records)} rows")
                for rec in records:
                    print(f"      {rec['label']:>6}: {dict(zip(rec['x'], [round(v, 2) for v in rec['y']]))}")
    else:
        print("  FAILED")
