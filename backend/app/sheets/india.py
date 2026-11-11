"""India-Parity workbook → Product Spreads / Outright Prices · India, and Parity · Ind/China (India half).

- Local prices tab (gid 998039497): port of legacy/scripts/gsheet_data.py — 6 outrights and 15 pairwise spreads,
  USD/MT with an INR/MT local value (USD × the sheet's FX), parsed with pandas.read_csv exactly as the legacy did.
- Dashboard Data tab (gid 1151879540): port of legacy parity_data.fetch_india_parity (forward-curve snapshots).
- Calculation tab (gid 741962089): new — the daily import-parity table on the right of the sheet (H:AA).
"""
from __future__ import annotations

import csv
import io
import logging
import re

import pandas as pd

logger = logging.getLogger(__name__)


def grid_to_csv(grid: list[list[str]]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    for row in grid:
        w.writerow(["" if c is None else c for c in row])
    return buf.getvalue()


# ── Local prices (legacy gsheet_data.py) ────────────────────────────────────────────────────────────────────

PRICE_COLUMNS = {
    "Olein":          "INDIA_OLEIN",
    "Soya Oil Ref":   "INDIA_SOYA_OIL_REF",
    "Gnut Oil Raw":   "INDIA_GNUT_OIL_RAW",
    "Rapeseed Oil":   "INDIA_RAPESEED_OIL",
    "Cotton Oil Ref": "INDIA_COTTON_OIL_REF",
    "Rice Bran Oil":  "INDIA_RICE_BRAN_OIL",
}
INDIA_TITLES = {
    "INDIA_OLEIN":          "Local India Olein",
    "INDIA_SOYA_OIL_REF":   "Local India Soya Oil Ref",
    "INDIA_GNUT_OIL_RAW":   "Local India Groundnut Oil Raw",
    "INDIA_RAPESEED_OIL":   "Local India Rapeseed Oil",
    "INDIA_COTTON_OIL_REF": "Local India Cotton Oil Ref",
    "INDIA_RICE_BRAN_OIL":  "Local India Rice Bran Oil",
}
INDIA_OUTRIGHT_KEYS = list(PRICE_COLUMNS.values())
INDIA_SPREAD_DEFS: list[tuple[str, str, str]] = []
for _i, _a in enumerate(INDIA_OUTRIGHT_KEYS):
    for _b in INDIA_OUTRIGHT_KEYS[_i + 1:]:
        INDIA_SPREAD_DEFS.append((_a, _b, f"{_a}_vs_{_b}"))
for _a, _b, _k in INDIA_SPREAD_DEFS:
    INDIA_TITLES[_k] = f"{INDIA_TITLES[_a]} - {INDIA_TITLES[_b]}"


def local_prices(csv_text: str) -> dict[str, tuple[pd.Series, pd.Series]]:
    """{key: (usd, inr)} — legacy fetch_india_data on the tab's CSV export."""
    df = pd.read_csv(io.StringIO(csv_text))
    needed = ["Date", "FX"] + list(PRICE_COLUMNS)
    missing = [c for c in needed if c not in df.columns]
    if missing:
        raise ValueError(f"India local prices: missing columns {missing}")
    df["Date"] = pd.to_datetime(df["Date"], dayfirst=True, errors="coerce")
    df = df.dropna(subset=["Date"]).sort_values("Date").set_index("Date")
    df["FX"] = pd.to_numeric(df["FX"], errors="coerce")
    for col in PRICE_COLUMNS:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    if df.empty or df["FX"].dropna().empty:
        raise ValueError("India local prices: no valid data rows")
    out: dict[str, tuple[pd.Series, pd.Series]] = {}
    for col, key in PRICE_COLUMNS.items():
        out[key] = (df[col], df[col] * df["FX"])
    for a, b, k in INDIA_SPREAD_DEFS:
        ca = next(s for s, kk in PRICE_COLUMNS.items() if kk == a)
        cb = next(s for s, kk in PRICE_COLUMNS.items() if kk == b)
        out[k] = (df[ca] - df[cb], (df[ca] - df[cb]) * df["FX"])
    return out


# ── Dashboard Data snapshot (legacy parity_data.fetch_india_parity) ─────────────────────────────────────────

INDIA_PARITY_INSTRUMENTS = {
    "Olein Eq - CSBO": "INDIA_PARITY_OLEIN_CSBO", "Olein Eq - SBO": "INDIA_PARITY_OLEIN_CSBO",
    "Olein Eq - CSFO (Black Sea)": "INDIA_PARITY_OLEIN_CSFO", "Olein Eq - SFO (Black Sea)": "INDIA_PARITY_OLEIN_CSFO",
    "CSBO - CSFO": "INDIA_PARITY_CSBO_CSFO",
}
INDIA_PARITY_TITLES = {
    "INDIA_PARITY_OLEIN_CSBO": "India: Palm Olein Eq vs CSBO",
    "INDIA_PARITY_OLEIN_CSFO": "India: Palm Olein Eq vs CSFO (Black Sea)",
    "INDIA_PARITY_CSBO_CSFO":  "India: CSBO vs CSFO (Black Sea)",
}
INDIA_PARITY_KEYS = list(dict.fromkeys(INDIA_PARITY_INSTRUMENTS.values()))


def _row_records(df: pd.DataFrame, header_row: int, x_cols: list, x_labels: list) -> list:
    """legacy parity_data._row_records: 5 rows after the header; non-numeric cells drop out (a gap, not a 0)."""
    records = []
    for r in range(header_row + 1, min(header_row + 6, len(df))):
        row = df.iloc[r]
        label = row[0]
        if pd.isna(label):
            continue
        vals = [pd.to_numeric(row[c], errors="coerce") for c in x_cols]
        xs = [lbl for lbl, v in zip(x_labels, vals) if pd.notna(v)]
        ys = [float(v) for v in vals if pd.notna(v)]
        if not xs:
            continue
        records.append({"label": str(label).strip(), "x": xs, "y": ys})
    return records


def parity_snapshot(csv_text: str) -> list[dict]:
    """[{key, title, period, records}] — daily and weekly blocks per instrument."""
    df = pd.read_csv(io.StringIO(csv_text), header=None)
    header_rows = [r for r in range(1, len(df))
                   if str(df.iloc[r].get(1, "")).strip() == "Date" and str(df.iloc[r].get(2, "")).strip() == "Nearest"]
    if len(header_rows) < 2:
        raise ValueError(f"India parity: expected 2 header rows (daily/weekly), found {len(header_rows)}")
    result: dict[str, dict] = {k: {} for k in INDIA_PARITY_KEYS}
    for block, hr in zip(("daily", "weekly"), header_rows[:2]):
        group_row, header = df.iloc[hr - 1], df.iloc[hr]
        starts = [c for c in range(3, len(df.columns)) if pd.notna(group_row[c])]
        for i, start in enumerate(starts):
            end = starts[i + 1] if i + 1 < len(starts) else len(df.columns)
            key = INDIA_PARITY_INSTRUMENTS.get(str(group_row[start]).strip())
            if key is None:
                continue
            cols = list(range(start, end))
            result[key][block] = _row_records(df, hr, cols, [str(header[c]).strip() for c in cols])
    out = []
    for key in INDIA_PARITY_KEYS:
        for block in ("daily", "weekly"):
            out.append({"key": key, "title": INDIA_PARITY_TITLES[key], "period": block,
                        "records": result[key].get(block, [])})
    return out


# ── Calculation tab: daily import-parity table (new) ────────────────────────────────────────────────────────

# (product row label, sub-header) → (series key, title, kind) ; kind "spread" = parity / margin, "price" = outright
IMPORT_COLUMNS: dict[tuple[str, str], tuple[str, str, str]] = {
    ("CPO", "CNF"):              ("INDIA_IMP_CPO_CNF", "India CPO CNF offer", "price"),
    ("CPO", "Ref Parity"):       ("INDIA_IMP_CPO_REF", "India CPO — refining parity", "spread"),
    ("CPO", "HS"):               ("INDIA_IMP_CPO_HS_PRICE", "India CPO high-seas price", "price"),
    ("CPO", "Parity"):           ("INDIA_IMP_CPO_HS", "India CPO — high-seas parity", "spread"),
    ("RBD OLEIN", "CNF"):        ("INDIA_IMP_OLEIN_CNF", "India RBD Olein CNF offer", "price"),
    ("RBD OLEIN", "Ref Parity"): ("INDIA_IMP_OLEIN_REF", "India RBD Olein — refining parity", "spread"),
    ("CDSBO", "CNF"):            ("INDIA_IMP_CDSBO_CNF", "India CDSBO CNF offer", "price"),
    ("CDSBO", "Ref Parity"):     ("INDIA_IMP_CDSBO_REF", "India CDSBO — refining parity", "spread"),
    ("CDSBO", "HS"):             ("INDIA_IMP_CDSBO_HS_PRICE", "India CDSBO high-seas price", "price"),
    ("CDSBO", "Parity"):         ("INDIA_IMP_CDSBO_HS", "India CDSBO — high-seas parity", "spread"),
    ("CSFO", "CIF"):             ("INDIA_IMP_CSFO_CIF", "India CSFO CIF offer", "price"),
    ("CSFO", "Ref Parity"):      ("INDIA_IMP_CSFO_REF", "India CSFO — refining parity", "spread"),
    ("CSFO", "HS"):              ("INDIA_IMP_CSFO_HS_PRICE", "India CSFO high-seas price", "price"),
    ("CSFO", "Parity"):          ("INDIA_IMP_CSFO_HS", "India CSFO — high-seas parity", "spread"),
    ("PFAD", "CNF"):             ("INDIA_IMP_PFAD_CNF", "India PFAD CNF offer", "price"),
    ("PFAD", "Parity"):          ("INDIA_IMP_PFAD", "India PFAD — parity", "spread"),
    ("DUTY PAID (DP)", "CPO"):   ("INDIA_DP_CPO", "India CPO duty-paid", "price"),
    ("DUTY PAID (DP)", "CDSBO"): ("INDIA_DP_CDSBO", "India CDSBO duty-paid", "price"),
    ("DP", "CDSBO-CPO"):         ("INDIA_DP_CDSBO_CPO", "India duty-paid CDSBO − CPO", "spread"),
}
_DMY = re.compile(r"^\d{1,2}/\d{1,2}/\d{4}$")


def _num(v) -> float | None:
    s = str(v or "").strip().replace(",", "")
    if not s or s in {"-", "#DIV/0!", "#N/A", "#REF!", "#VALUE!"}:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def import_parity(grid: list[list[str]]) -> dict[str, pd.Series]:
    """Locate the 'Date' column of the right-hand table (product row = the 'Date' row, sub-headers below it) and
    read every dated row under it."""
    for r, row in enumerate(grid[:30]):
        dcol = next((c for c in range(1, len(row)) if str(row[c]).strip() == "Date"), None)
        if dcol is not None and r + 1 < len(grid):
            break
    else:
        raise ValueError("India import parity: 'Date' header not found")
    prod_row, sub_row = grid[r], grid[r + 1]
    width = max(len(prod_row), len(sub_row))
    cols: dict[int, tuple[str, str, str]] = {}
    current = ""
    for c in range(dcol + 1, width):
        p = str(prod_row[c]).strip() if c < len(prod_row) else ""
        if p:
            current = p.upper()
        sub = str(sub_row[c]).strip() if c < len(sub_row) else ""
        spec = IMPORT_COLUMNS.get((current, sub))
        if spec:
            cols[c] = spec
    if not cols:
        raise ValueError("India import parity: no known columns under the 'Date' header")
    data: dict[str, dict] = {spec[0]: {} for spec in cols.values()}
    for row in grid[r + 2:]:
        d = str(row[dcol]).strip() if dcol < len(row) else ""
        if not _DMY.match(d):
            continue
        ts = pd.to_datetime(d, dayfirst=True, errors="coerce")
        if pd.isna(ts):
            continue
        for c, (key, _t, _k) in cols.items():
            v = _num(row[c]) if c < len(row) else None
            if v is not None:
                data[key][ts] = v
    return {k: pd.Series(v, dtype=float).sort_index() for k, v in data.items() if v}


def series_specs() -> list[dict]:
    specs = []
    for k in INDIA_OUTRIGHT_KEYS:
        specs.append({"key": k, "module": "outright_prices", "group": "India Local", "title": INDIA_TITLES[k],
                      "unit": "USD/MT", "local_unit": "INR/MT"})
    for _a, _b, k in INDIA_SPREAD_DEFS:
        specs.append({"key": k, "module": "product_spreads", "group": "India Local", "title": INDIA_TITLES[k],
                      "unit": "USD/MT", "local_unit": "INR/MT"})
    for key, title, kind in IMPORT_COLUMNS.values():
        if kind == "spread":
            specs.append({"key": key, "module": "product_spreads", "group": "India Import Parity", "title": title,
                          "unit": "USD/MT"})
        else:
            specs.append({"key": key, "module": "outright_prices", "group": "India Import", "title": title,
                          "unit": "USD/MT"})
    return specs
