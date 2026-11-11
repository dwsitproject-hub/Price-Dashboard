"""CFTC Commitments of Traders — Disaggregated, Futures + Options Combined (legacy scripts/cftc_data.py).

The legacy parsed hand-saved copies of cftc.gov's weekly *_lof.htm pages. This reads the CFTC's yearly
history files instead (com_disagg_txt_<year>.zip, refreshed by the CFTC every week): same report, same
numbers, but full history and no manual step. Metrics and the snapshot are the legacy formulas.
"""
from __future__ import annotations

import csv
import io
import zipfile
from datetime import date

import httpx
import pandas as pd
from sqlalchemy import text

from app.core.config import get_settings
from app.core.db import get_engine
from app.intel.jobs import register

HISTORY_URL = "https://www.cftc.gov/files/dea/history/com_disagg_txt_{year}.zip"

# Legacy RELEVANT_PRODUCTS (code → label), in display order.
RELEVANT_PRODUCTS: dict[str, str] = {
    "037021": "CME Malaysian Crude Palm Oil",
    "007601": "CBOT Soybean Oil (BO)",
    "026603": "CBOT Soybean Meal (SM)",
    "005602": "CBOT Soybeans (S)",
    "135731": "ICE Canola / Rapeseed",
    "002602": "CBOT Corn",
    "022651": "NYMEX ULSD (Diesel / Gasoil proxy)",
    "067651": "NYMEX WTI Crude Oil",
    "06765T": "NYMEX Brent Crude (financial)",
    "111659": "NYMEX RBOB Gasoline",
    "025651": "NYMEX Ethanol",
    "023651": "NYMEX Henry Hub Natural Gas",
}

# Legacy _COLS ← column names in the CFTC disaggregated history file.
COLUMNS = {
    "open_interest": "Open_Interest_All",
    "pm_long": "Prod_Merc_Positions_Long_All", "pm_short": "Prod_Merc_Positions_Short_All",
    "swap_long": "Swap_Positions_Long_All", "swap_short": "Swap__Positions_Short_All",
    "swap_spread": "Swap__Positions_Spread_All",
    "mm_long": "M_Money_Positions_Long_All", "mm_short": "M_Money_Positions_Short_All",
    "mm_spread": "M_Money_Positions_Spread_All",
    "other_long": "Other_Rept_Positions_Long_All", "other_short": "Other_Rept_Positions_Short_All",
    "other_spread": "Other_Rept_Positions_Spread_All",
    "nonrept_long": "NonRept_Positions_Long_All", "nonrept_short": "NonRept_Positions_Short_All",
}
CATEGORIES = [("mm", "Managed Money"), ("swap", "Swap Dealers"), ("pm", "Producer/Merchant")]


def parse_history_file(raw: bytes) -> list[dict]:
    """Rows of the tracked contracts from one com_disagg_txt_<year>.zip (or its c_year.txt)."""
    if raw[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            raw = z.read(z.namelist()[0])
    rows = []
    for rec in csv.DictReader(io.StringIO(raw.decode("latin-1"))):
        code = (rec.get("CFTC_Contract_Market_Code") or "").strip()
        if code not in RELEVANT_PRODUCTS:
            continue
        row = {"cftc_code": code, "market_name": (rec.get("Market_and_Exchange_Names") or "").strip()[:200],
               "report_date": date.fromisoformat(rec["Report_Date_as_YYYY-MM-DD"].strip()[:10])}
        for k, col in COLUMNS.items():
            v = (rec.get(col) or "").strip().replace(",", "")
            row[k] = int(float(v)) if v not in ("", ".") else None
        rows.append(row)
    return rows


def _upsert(rows: list[dict]) -> int:
    if not rows:
        return 0
    cols = ["cftc_code", "report_date", "market_name", *COLUMNS]
    sql = (f"INSERT INTO intel.cftc_positions ({', '.join(cols)}) VALUES ({', '.join(':' + c for c in cols)}) "
           f"ON CONFLICT (cftc_code, report_date) DO UPDATE SET "
           + ", ".join(f"{c} = EXCLUDED.{c}" for c in cols[2:]) + ", updated_at = now()")
    with get_engine().begin() as c:
        c.execute(text(sql), rows)
    return len(rows)


def _download(year: int) -> bytes:
    r = httpx.get(HISTORY_URL.format(year=year), timeout=120, follow_redirects=True,
                  headers={"User-Agent": "AASTAR-Price-Dashboard/2.0"})
    r.raise_for_status()
    return r.content


@register("cftc_refresh")
def refresh(params: dict) -> dict:
    """Current year every time; earlier years (CFTC_HISTORY_YEARS) once, when they are not in the table yet."""
    this_year = date.today().year
    first = this_year - max(get_settings().cftc_history_years, 1) + 1
    with get_engine().connect() as c:
        have = set(c.execute(text("SELECT DISTINCT extract(year FROM report_date)::int FROM intel.cftc_positions"))
                   .scalars().all())
    years = [y for y in range(first, this_year) if y not in have] + [this_year]
    if params.get("years"):
        years = [int(y) for y in params["years"]]
    loaded = {}
    for y in years:
        try:
            rows = parse_history_file(_download(y))
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404 and y == this_year:   # new year before the CFTC's first report
                loaded[str(y)] = 0
                continue
            raise
        loaded[str(y)] = _upsert(rows)
    return {"rows_by_year": loaded}


# ── Payload ─────────────────────────────────────────────────────────────────

def _frame() -> pd.DataFrame:
    with get_engine().connect() as c:
        df = pd.read_sql(text("SELECT * FROM intel.cftc_positions ORDER BY cftc_code, report_date"), c)
    if df.empty:
        return df
    oi = df["open_interest"].astype("float").replace(0, pd.NA)
    for k in ("mm", "swap", "pm", "other"):
        df[f"{k}_net"] = df[f"{k}_long"].astype("float") - df[f"{k}_short"].astype("float")
    for k in ("mm", "swap", "pm"):
        df[f"{k}_net_pct_oi"] = (df[f"{k}_net"] / oi * 100).astype("float")
    return df


def _f(v) -> float | None:
    return None if v is None or pd.isna(v) else round(float(v), 4)


def payload() -> dict:
    df = _frame()
    if df.empty:
        return {"as_of": None, "weeks": 0, "products": [], "categories": CATEGORIES}
    products = []
    for code, label in RELEVANT_PRODUCTS.items():
        g = df[df["cftc_code"] == code].sort_values("report_date")
        if g.empty:
            continue
        last = g.iloc[-1]
        prev = g.iloc[-2] if len(g) > 1 else None
        snap = {"code": code, "label": label, "market_name": last["market_name"],
                "report_date": last["report_date"].isoformat(), "open_interest": _f(last["open_interest"])}
        for k, _ in CATEGORIES:
            snap[f"{k}_net"] = _f(last[f"{k}_net"])
            snap[f"{k}_net_pct_oi"] = _f(last[f"{k}_net_pct_oi"])
            # Legacy: change vs the previous available report, whatever the gap.
            snap[f"wow_{k}_net"] = _f(last[f"{k}_net"] - prev[f"{k}_net"]) if prev is not None else None
        snap["history"] = {"dates": [d.isoformat() for d in g["report_date"]],
                           **{f"{k}_net": [_f(v) for v in g[f"{k}_net"]] for k, _ in CATEGORIES}}
        products.append(snap)
    return {"as_of": max(p["report_date"] for p in products), "weeks": int(df["report_date"].nunique()),
            "products": products, "categories": CATEGORIES}
