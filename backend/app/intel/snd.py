"""Supply & Demand (SND) — port of legacy scripts/snd_data.py.

The legacy read the UOB Malindo Excel plus public Google-Sheet CSV exports. The sheets are no longer public, so
each source is uploaded (CSV download of the tab, or an Excel sheet) and kept as its raw cell grid
(intel.snd_sources); the parsers below work on those grids exactly as the legacy worked on the CSV/Excel reads.
The year comes from the upload instead of being hard-coded to 2026.
"""
from __future__ import annotations

import csv
import io
import json

from datetime import date, datetime

import pandas as pd
from sqlalchemy import text

from app.core.db import get_engine
from app.core.errors import ApiError

ROLES = ("malaysia_history", "malaysia", "indonesia", "india", "exports")
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

# ── Row maps (legacy constants, verbatim) ───────────────────────────────────
MY_ITEMS = {"Opening Stock": 2, "Production": 3, "Imports": 8, "Exports": 11, "Local Disappearance": 13,
            "End Stock": 15}                                     # UOB Excel, Sheet1 (0-based rows)
MY_YEAR_ITEMS = {"Opening Stock": 4, "Production": 5, "Imports": 7, "Local Disappearance": 11, "Exports": 13,
                 "End Stock": 18}                                # Malaysia year sheet, '000 MT
ID_ITEM_LABELS = {
    "Opening Stock": ["beginning stock", "opening stock"],
    "Production": ["production"],
    "Imports": ["imports"],
    "Domestic Disappearance": ["domestic disappearance"],
    "Total Palm Oil Exports": ["total palm oil exports"],
    "Ending Stock": ["ending stock", "closing stock"],
}
INDIA_GRAPH_ITEMS = {
    "Consumption": "consumption", "Production": "domestic production", "Beg Total Stock": "beg total stock",
    "Total Imports": "total imports", "End Total Stock": "end total stock", "Stock Change": "stock change",
    "Stock-to-Use Ratio": "stock-to-use ratio",
}


# ── Upload → grid ───────────────────────────────────────────────────────────

def _cell(v) -> str:
    if v is None:
        return ""
    if isinstance(v, (datetime, date)):
        return v.date().isoformat() if isinstance(v, datetime) else v.isoformat()
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def grid_from_upload(filename: str, data: bytes, sheet: str | None, role: str) -> tuple[list[list[str]], str | None]:
    name = filename.lower()
    if name.endswith(".csv"):
        text_ = data.decode("utf-8-sig", errors="replace")
        return [list(r) for r in csv.reader(io.StringIO(text_))], None
    if name.endswith((".xlsx", ".xlsm")):
        from openpyxl import load_workbook
        try:
            wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        except Exception as exc:  # noqa: BLE001 - corrupt or non-Excel file
            raise ApiError(422, "bad_file", "The file could not be read as an Excel workbook.") from exc
        wanted = sheet or ("Sheet1" if role == "malaysia_history" and "Sheet1" in wb.sheetnames else wb.sheetnames[0])
        if wanted not in wb.sheetnames:
            raise ApiError(422, "bad_sheet", f"Sheet {wanted!r} not found. Sheets: {', '.join(wb.sheetnames)}")
        ws = wb[wanted]
        rows = [[_cell(v) for v in r] for r in ws.iter_rows(values_only=True)]
        wb.close()
        return rows, wanted
    raise ApiError(422, "bad_file_type", "Upload a .csv (Google Sheets: File → Download → CSV) or an .xlsx file.")


def save_source(role: str, year: int, filename: str, sheet: str | None, grid: list[list[str]], user_id) -> dict:
    if role not in ROLES:
        raise ApiError(422, "bad_role", f"role must be one of {', '.join(ROLES)}")
    if role == "malaysia_history":
        year = 0
    elif not 2000 <= year <= 2100:
        raise ApiError(422, "bad_year", "year must be between 2000 and 2100")
    if len(grid) < 5:
        raise ApiError(422, "bad_file", "The sheet has too few rows to be an SND sheet.")
    width = max(len(r) for r in grid)
    grid = [r + [""] * (width - len(r)) for r in grid]
    with get_engine().begin() as c:
        c.execute(text(
            "INSERT INTO intel.snd_sources (role, year, filename, sheet, grid, uploaded_by) "
            "VALUES (:r, :y, :f, :s, CAST(:g AS jsonb), :u) ON CONFLICT (role, year) DO UPDATE SET "
            "filename = EXCLUDED.filename, sheet = EXCLUDED.sheet, grid = EXCLUDED.grid, "
            "uploaded_by = EXCLUDED.uploaded_by, uploaded_at = now()"),
            {"r": role, "y": year, "f": filename[:255], "s": sheet, "g": json.dumps(grid), "u": user_id})
    parsed = summary()
    return {"role": role, "year": year, "rows": len(grid), "columns": width, "parsed": parsed}


def delete_source(role: str, year: int) -> bool:
    with get_engine().begin() as c:
        return bool(c.execute(text("DELETE FROM intel.snd_sources WHERE role = :r AND year = :y"),
                              {"r": role, "y": year}).rowcount)


def list_sources() -> list[dict]:
    with get_engine().connect() as c:
        rows = c.execute(text(
            "SELECT s.role, s.year, s.filename, s.sheet, jsonb_array_length(s.grid) AS rows, s.uploaded_at, "
            "u.email AS uploaded_by FROM intel.snd_sources s LEFT JOIN iam.users u ON u.id = s.uploaded_by "
            "ORDER BY array_position(ARRAY['malaysia_history','malaysia','indonesia','india','exports'], "
            "s.role::text), s.year")).mappings().all()
    return [dict(r) for r in rows]


def _grids() -> dict[tuple[str, int], list[list[str]]]:
    with get_engine().connect() as c:
        rows = c.execute(text("SELECT role, year, grid FROM intel.snd_sources")).all()
    return {(r.role, r.year): r.grid for r in rows}


# ── Parsing (legacy logic on grids) ─────────────────────────────────────────

def _num(x: str) -> float | None:
    v = pd.to_numeric(str(x).replace(",", "").replace("(", "-").replace(")", ""), errors="coerce")
    return None if pd.isna(v) else float(v)


def _col(grid, r, c) -> str:
    return grid[r][c] if r < len(grid) and c < len(grid[r]) else ""


def parse_malaysia_history(grid) -> dict[str, dict[str, float]]:
    """Legacy _parse_malaysia: dates in row 1 (cols 1+), item rows from MY_ITEMS; values already MT."""
    out: dict[str, dict[str, float]] = {}
    dates = {c: pd.to_datetime(_col(grid, 1, c), errors="coerce") for c in range(1, len(grid[1]))}
    dates = {c: d for c, d in dates.items() if pd.notna(d)}
    for item, r in MY_ITEMS.items():
        series = {}
        for c, d in dates.items():
            v = pd.to_numeric(_col(grid, r, c), errors="coerce")
            if pd.notna(v):
                series[pd.Timestamp(d.year, d.month, 1).date().isoformat()] = float(v)
        out[item] = series
    return out


def parse_malaysia_year(grid, year: int) -> dict[str, dict[str, float]]:
    """Legacy _parse_malaysia_2026: Jan–Dec in cols 1–12, values '000 MT → × 1000 = MT."""
    out = {}
    for item, r in MY_YEAR_ITEMS.items():
        series = {}
        for m in range(1, 13):
            v = pd.to_numeric(str(_col(grid, r, m)).replace(",", ""), errors="coerce")
            if pd.notna(v):
                series[date(year, m, 1).isoformat()] = float(v) * 1000
        out[item] = series
    return out


def parse_indonesia_year(grid, year: int) -> dict[str, dict[str, float]]:
    """Legacy _parse_indonesia_year: label substring aliases (last duplicate label wins), cols 1–12."""
    label_to_row = {}
    for i, row in enumerate(grid):
        cell = str(row[0]).strip().lower() if row else ""
        if cell:
            label_to_row[cell] = i
    out = {}
    for item, aliases in ID_ITEM_LABELS.items():
        r = next((idx for alias in aliases for label, idx in label_to_row.items() if alias in label), None)
        if r is None:
            continue
        series = {date(year, m, 1).isoformat(): v for m in range(1, 13) if (v := _num(_col(grid, r, m))) is not None}
        if series:
            out[item] = series
    return out


def parse_india_year(grid, year: int) -> dict[str, dict[str, float]]:
    """Legacy load_snd_india: first label occurrence, startswith prefixes, months in cols 2–13 (col 1 = Source)."""
    label_to_row: dict[str, int] = {}
    for i, row in enumerate(grid):
        cell = str(row[0]).strip().lower() if row else ""
        if cell and cell != "nan":
            label_to_row.setdefault(cell, i)
    out = {}
    for item, prefix in INDIA_GRAPH_ITEMS.items():
        r = next((idx for label, idx in label_to_row.items() if label.startswith(prefix)), None)
        if r is None:
            continue
        series = {date(year, m, 1).isoformat(): v for m in range(1, 13)
                  if (v := _num(_col(grid, r, m + 1))) is not None}
        if series:
            out[item] = series
    return out


def _table(grid, ncols: int, header_row: int, body: list[int], blank_first_header: bool = True) -> dict:
    cut = [[(r[c] if c < len(r) else "") for c in range(ncols)] for r in grid]
    hdr = cut[header_row] if header_row < len(cut) else [""] * ncols
    headers = ([""] + hdr[1:]) if blank_first_header else hdr
    rows = [cut[i] for i in body if i < len(cut)]
    return {"headers": headers, "rows": rows}


def table_for(role: str, grid) -> dict:
    """Legacy fetch_*_2026_table slicing, verbatim."""
    if role == "malaysia":
        return _table(grid, 21, 2, list(range(3, 24)))
    if role == "indonesia":
        return _table(grid, 21, 2, list(range(3, 34)))
    if role == "india":
        return _table(grid, 18, 3, list(range(0, 3)) + list(range(5, 42)))
    if role == "exports":
        return _table(grid, 20, 3, list(range(4, 47)), blank_first_header=False)
    raise ValueError(role)


# ── Payloads ────────────────────────────────────────────────────────────────

def _merge(series_list: list[dict[str, dict[str, float]]]) -> dict[str, dict[str, float]]:
    """Concatenate per item; the first source wins on a duplicate month (legacy drop_duplicates keep='first')."""
    out: dict[str, dict[str, float]] = {}
    for parsed in series_list:
        for item, ser in parsed.items():
            tgt = out.setdefault(item, {})
            for d, v in ser.items():
                tgt.setdefault(d, v)
    return out


def _countries(grids) -> list[dict]:
    my_parts = []
    if ("malaysia_history", 0) in grids:
        my_parts.append(parse_malaysia_history(grids[("malaysia_history", 0)]))
    my_parts += [parse_malaysia_year(g, y) for (r, y), g in sorted(grids.items()) if r == "malaysia"]
    id_parts = [parse_indonesia_year(g, y) for (r, y), g in sorted(grids.items()) if r == "indonesia"]
    in_parts = [parse_india_year(g, y) for (r, y), g in sorted(grids.items()) if r == "india"]
    my = _merge(my_parts)
    return [
        {"name": "Malaysia", "unit": "MT", "items": [(k, my[k]) for k in MY_ITEMS if my.get(k)]},
        {"name": "Indonesia", "unit": "'000 MT",
         "items": [(k, v) for k in ID_ITEM_LABELS if (v := _merge(id_parts).get(k))]},
        {"name": "India", "unit": "'000 MT",
         "items": [(k, v) for k in INDIA_GRAPH_ITEMS if (v := _merge(in_parts).get(k))]},
    ]


def graphs(years: list[int]) -> dict:
    grids = _grids()
    avail: set[int] = set()
    out = []
    for c in _countries(grids):
        items = []
        for name, ser in c["items"]:
            by_year: dict[int, list] = {}
            for d, v in ser.items():
                dt = date.fromisoformat(d)
                avail.add(dt.year)
                by_year.setdefault(dt.year, [None] * 12)[dt.month - 1] = v
            unit = "%" if (c["name"] == "India" and "Ratio" in name) else c["unit"]
            traces = [{"year": y, "x": MONTHS, "y": by_year[y]} for y in sorted(by_year) if not years or y in years]
            items.append({"name": name, "unit": unit, "traces": traces})
        out.append({"name": c["name"], "items": items, "has_source": bool(c["items"])})
    return {"countries": out, "years": sorted(avail)}


def summary() -> dict:
    g = graphs([])
    return {c["name"]: len(c["items"]) for c in g["countries"]}


def bmd_curve(year: int) -> dict:
    """Legacy fetch_bmd_curve on market.price_daily: latest FCPO c1..c12 settlement (fallback last), c1 = this
    month if the latest trade date is on/before the 15th else next month; months within `year` only. MYR/MT."""
    rics = [f"FCPOc{i}" for i in range(1, 13)]
    with get_engine().connect() as c:
        latest = c.execute(text("SELECT max(trade_date) FROM market.price_daily WHERE ric = ANY(:r)"),
                           {"r": rics}).scalar()
        if latest is None:
            return {"as_of": None, "months": {}}
        rows = c.execute(text("SELECT ric, coalesce(settlement_price, last_price)::float8 AS p FROM market.price_daily "
                              "WHERE ric = ANY(:r) AND trade_date = :d"), {"r": rics, "d": latest}).all()
    prices = {r.ric: r.p for r in rows if r.p is not None}
    c1 = date(latest.year, latest.month, 1) if latest.day <= 15 else (
        date(latest.year + 1, 1, 1) if latest.month == 12 else date(latest.year, latest.month + 1, 1))
    months = {}
    for i in range(1, 13):
        p = prices.get(f"FCPOc{i}")
        if p is None:
            continue
        cal = (pd.Timestamp(c1) + pd.DateOffset(months=i - 1)).date()
        if cal.year == year:
            months[cal.month] = p
    return {"as_of": latest.isoformat(), "months": months}


TABLE_TITLES = {"malaysia": "Malaysia", "indonesia": "Indonesia", "india": "India"}


def tables() -> dict:
    """Tabular sub-view: latest year of Malaysia, Indonesia (both with the BMD row) and India."""
    grids = _grids()
    out = []
    for role in ("malaysia", "indonesia", "india"):
        years = sorted(y for (r, y) in grids if r == role)
        if not years:
            out.append({"code": role, "title": TABLE_TITLES[role], "year": None, "missing": True})
            continue
        y = years[-1]
        t = table_for(role, grids[(role, y)])
        bmd = bmd_curve(y) if role in ("malaysia", "indonesia") else None
        out.append({"code": role, "title": f"{TABLE_TITLES[role]} {y}", "year": y, **t, "bmd": bmd,
                    "missing": False})
    return {"tables": out}


def exports_table() -> dict:
    grids = _grids()
    years = sorted(y for (r, y) in grids if r == "exports")
    if not years:
        return {"table": None}
    y = years[-1]
    return {"table": {"code": "exports", "title": f"Malindo {y} Exports — By Destination", "year": y,
                      **table_for("exports", grids[("exports", y)])}}


def latest_end_stock(country: str = "Malaysia") -> list[dict]:
    """News → SND Snapshot (legacy _build_snd_snapshot): latest value per Malaysia item."""
    out = []
    for c in _countries(_grids()):
        if c["name"] != country:
            continue
        for name, ser in c["items"]:
            if ser:
                d = max(ser)
                out.append({"item": name, "value": ser[d], "unit": c["unit"], "period": d[:7]})
    return out


