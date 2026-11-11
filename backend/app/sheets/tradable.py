"""KPN Market Price Report → "Premiums" tab → Basis / Hedge Ratios · Tradable Prem.

Port of legacy/scripts/tradable_prem_data.py: the cell parser, the outlier rule and the header locator are copied
unchanged; premium = Tradable − raw Reuters price, for Malaysia (Reuters Msia) and Indonesia (Reuters Indo).
"""
from __future__ import annotations

import logging
import re
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

PRODUCTS = ["CPO", "RPO", "ROL", "RPS", "PFAD"]          # legacy config.TRADABLE_PREM_PRODUCTS
REGIONS = ["MY", "ID"]
REGION_LABEL = {"MY": "Malaysia", "ID": "Indonesia"}

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


def compute(grid: list) -> dict[str, pd.Series]:
    """fetch_tradable_prem without the cache: {TP_<region>_<product>: date-indexed premium}."""
    prow, date_col, cols = _locate_columns(grid)
    data = grid[prow + 1:]
    dates = [_parse_date(r[date_col] if date_col < len(r) else None) for r in data]

    def series(block_key: str, product: str) -> list:
        c = cols.get(block_key, {}).get(product)
        if c is None:
            return [None] * len(data)
        raw = [_num(r[c]) if c < len(r) else None for r in data]
        return _drop_outliers(raw, f"{block_key}/{product}")

    out: dict[str, pd.Series] = {}
    for product in PRODUCTS:
        trad = series("TRADABLE", product)
        for region in REGIONS:
            reut = series(region, product)
            pts = {}
            for d, t, rv in zip(dates, trad, reut):
                if d is None or t is None or rv is None:
                    continue
                pts[d] = t - rv            # duplicate dates: the later row wins, as in the legacy groupby/cache
            if pts:
                out[f"TP_{region}_{product}"] = pd.Series(pts, dtype=float).sort_index()
    return out


def series_specs() -> list[dict]:
    return [{"key": f"TP_{r}_{p}", "module": "basis_premiums", "section": "tradable",
             "group": f"Tradable Prem · {REGION_LABEL[r]}", "title": f"{p} Tradable Premium — {REGION_LABEL[r]}",
             "unit": "USD/MT"} for r in REGIONS for p in PRODUCTS]
