"""China-Parity workbook → Parity · Ind/China (China half) and Product Spreads · China.

- Dashboard Data tab (gid 466961326): forward-curve snapshot tables. The legacy reader
  (parity_data.fetch_china_parity) took only the first two "Date/Nearest/Spot" header rows and titled them
  "Palm Olein vs CSBO"; the tab now holds two instruments (POBO and the South China palm olein import margin),
  so every block is read and titled from the instrument line above it.
- Cross-oil Spread, Palm Import Parity and Soyoil Import Parity tabs: daily history since 2020/2022.
"""
from __future__ import annotations

import re

import pandas as pd

_SNAPSHOT_LABEL = re.compile(r"^(Today|[DW]-\d+)$")
_MATURITIES = ("Spot", "M1", "M2", "M3", "M4", "M5", "M6")


def _num(v) -> float | None:
    s = str(v or "").strip().replace(",", "")
    if not s or s.startswith("#") or s == "-":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _cell(row: list, c: int) -> str:
    return str(row[c]).strip() if c < len(row) and row[c] is not None else ""


def parity_snapshot(grid: list[list[str]]) -> list[dict]:
    """[{key, title, period, records}] for every Label/Date/Nearest/Spot block of the Dashboard Data tab."""
    out: list[dict] = []
    title = ""
    for r, row in enumerate(grid):
        c0 = _cell(row, 0)
        is_header = c0 == "Label" and _cell(row, 1) == "Date" and _cell(row, 2) == "Nearest"
        if c0 and not is_header and not _SNAPSHOT_LABEL.match(c0) and c0 not in ("Daily", "Weekly"):
            title = c0
        if not is_header:
            continue
        x_cols = [c for c in range(3, len(row)) if _cell(row, c) in _MATURITIES]
        records = []
        for rr in grid[r + 1:r + 6]:
            label = _cell(rr, 0)
            if not _SNAPSHOT_LABEL.match(label):
                break
            xs, ys = [], []
            for c in x_cols:
                v = _num(rr[c]) if c < len(rr) else None
                if v is not None:
                    xs.append(_cell(row, c))
                    ys.append(v)
            if xs:
                records.append({"label": label, "x": xs, "y": ys})
        if not records:
            continue
        period = "weekly" if any(rec["label"].startswith("W-") for rec in records) else "daily"
        name = title or "China"
        key = "CHINA_PARITY_" + re.sub(r"[^A-Z0-9]+", "_", name.upper()).strip("_")[:40]
        out.append({"key": key, "title": f"China: {name}", "period": period, "records": records})
    return out


def cross_oil(grid: list[list[str]]) -> dict[str, pd.Series]:
    """date, Palm Main, SBO Main, RSO Main, POBO, PORSO, BORSO (CNY/t, DCE/ZCE main contracts)."""
    head = [h.strip() for h in grid[0]]
    want = {"POBO": "CHINA_DCE_POBO", "PORSO": "CHINA_DCE_PORSO", "BORSO": "CHINA_DCE_BORSO"}
    out: dict[str, dict] = {k: {} for k in want.values()}
    idx = {name: head.index(name) for name in want if name in head}
    for row in grid[1:]:
        d = pd.to_datetime(_cell(row, 0), format="%Y-%m-%d", errors="coerce")
        if pd.isna(d):
            continue
        for name, c in idx.items():
            v = _num(row[c]) if c < len(row) else None
            if v is not None:
                out[want[name]][d] = v
    return {k: pd.Series(v, dtype=float).sort_index() for k, v in out.items() if v}


def _two_row_header(grid: list[list[str]]) -> dict[tuple[str, str], int]:
    """Units row (forward-filled across merged cells) + names row → {(name, unit): column}."""
    units, names = grid[0], grid[1]
    out: dict[tuple[str, str], int] = {}
    unit = ""
    for c in range(len(names)):
        u = _cell(units, c)
        if u:
            unit = u.upper().replace("/MT", "/T")
        out.setdefault((_cell(names, c), unit), c)
    return out


def _dated_rows(grid: list[list[str]]):
    for row in grid[2:]:
        d = pd.to_datetime(_cell(row, 0), dayfirst=True, errors="coerce")
        if not pd.isna(d):
            yield d, row


REGIONS = (("North", "North China"), ("East", "East China"), ("South", "South China"))


def palm_import(grid: list[list[str]]) -> dict[str, tuple[pd.Series, pd.Series | None]]:
    """Import margin and basis by region: USD/t value with the CNY/t twin as the local value."""
    cols = _two_row_header(grid)
    pairs = {}
    for short, region in REGIONS:
        pairs[f"CHINA_PALM_IMPORT_MARGIN_{short.upper()}"] = (f"Import Margin - {region}",)
        pairs[f"CHINA_PALM_BASIS_{short.upper()}"] = (f"Basis - {region}",)
    out: dict[str, tuple[pd.Series, pd.Series | None]] = {}
    rows = list(_dated_rows(grid))
    for key, (name,) in pairs.items():
        cu, cc = cols.get((name, "USD/T")), cols.get((name, "CNY/T"))
        if cu is None:
            continue
        usd = {d: v for d, row in rows if (v := _num(row[cu] if cu < len(row) else None)) is not None}
        cny = {d: v for d, row in rows if cc is not None and (v := _num(row[cc] if cc < len(row) else None)) is not None}
        if usd:
            out[key] = (pd.Series(usd, dtype=float).sort_index(),
                        pd.Series(cny, dtype=float).sort_index() if cny else None)
    return out


def soyoil_import(grid: list[list[str]]) -> dict[str, pd.Series]:
    names = [_cell(grid[1], c) for c in range(len(grid[1]))]
    want = {"Brazil Import Margin": "CHINA_SBO_IMPORT_MARGIN_BRAZIL", "Arg Import Margin": "CHINA_SBO_IMPORT_MARGIN_ARG"}
    out: dict[str, dict] = {k: {} for k in want.values()}
    for d, row in _dated_rows(grid):
        for name, key in want.items():
            if name in names:
                c = names.index(name)
                v = _num(row[c]) if c < len(row) else None
                if v is not None:
                    out[key][d] = v
    return {k: pd.Series(v, dtype=float).sort_index() for k, v in out.items() if v}


def series_specs() -> list[dict]:
    g_dce, g_imp, g_basis = "China — DCE cross-oil", "China — import margins", "China — palm basis"
    specs = [
        {"key": "CHINA_DCE_POBO", "group": g_dce, "title": "China DCE POBO (palm − soyoil, main contracts)",
         "unit": "CNY/MT"},
        {"key": "CHINA_DCE_PORSO", "group": g_dce, "title": "China DCE/ZCE PORSO (palm − rapeseed oil, main)",
         "unit": "CNY/MT"},
        {"key": "CHINA_DCE_BORSO", "group": g_dce, "title": "China DCE/ZCE BORSO (soyoil − rapeseed oil, main)",
         "unit": "CNY/MT"},
    ]
    for short, region in REGIONS:
        specs.append({"key": f"CHINA_PALM_IMPORT_MARGIN_{short.upper()}", "group": g_imp,
                      "title": f"China palm import margin — {region}", "unit": "USD/MT", "local_unit": "CNY/MT"})
    specs += [
        {"key": "CHINA_SBO_IMPORT_MARGIN_BRAZIL", "group": g_imp, "title": "China soyoil import margin — Brazil",
         "unit": "USD/MT"},
        {"key": "CHINA_SBO_IMPORT_MARGIN_ARG", "group": g_imp, "title": "China soyoil import margin — Argentina",
         "unit": "USD/MT"},
    ]
    for short, region in REGIONS:
        specs.append({"key": f"CHINA_PALM_BASIS_{short.upper()}", "group": g_basis,
                      "title": f"China palm spot basis vs DCE — {region}", "unit": "USD/MT", "local_unit": "CNY/MT"})
    for s in specs:
        s["module"] = "product_spreads"
    return specs
