"""
Supply & Demand (SND) data loader.
Reads Malaysia historical data from the UOB Malindo Excel file (Sheet1)
and Malaysia 2026 data from a public Google Sheet.
Returns monthly time-series DataFrames ready for seasonal plotting.
"""

import logging
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent
SND_FILE = ROOT / "data" / "SND" / "UOBMalindoSnd_MY-ID_SND.xlsx"

MY_2026_URL = (
    "https://docs.google.com/spreadsheets/d/"
    "1MNiqNOtouT6uICw51kUVlIEesVWT5aYV/export?format=csv&gid=1379875238"
)
ID_2026_URL = (
    "https://docs.google.com/spreadsheets/d/"
    "1MNiqNOtouT6uICw51kUVlIEesVWT5aYV/export?format=csv&gid=886687037"
)
EXPORTS_2026_URL = (
    "https://docs.google.com/spreadsheets/d/"
    "1MNiqNOtouT6uICw51kUVlIEesVWT5aYV/export?format=csv&gid=1030349199"
)

ID_2024_URL = (
    "https://docs.google.com/spreadsheets/d/"
    "1MNiqNOtouT6uICw51kUVlIEesVWT5aYV/export?format=csv&gid=1625093867"
)
ID_2025_URL = (
    "https://docs.google.com/spreadsheets/d/"
    "1MNiqNOtouT6uICw51kUVlIEesVWT5aYV/export?format=csv&gid=1754627103"
)

ID_URLS = {2024: ID_2024_URL, 2025: ID_2025_URL, 2026: ID_2026_URL}

# ── India SND (2026) ─────────────────────────────────────────────────────────
INDIA_URL = (
    "https://docs.google.com/spreadsheets/d/"
    "1rBn3G3mxvTuNid8PHbopsumsJRY0io6Q/export?format=csv&gid=248085802"
)

# India graph series: canonical name → column-A label prefix (case-insensitive
# startswith). These are the rows the user wants plotted in the Graphs sub-tab.
INDIA_GRAPH_ITEMS: dict[str, str] = {
    "Consumption":        "consumption",
    "Production":         "domestic production",
    "Beg Total Stock":    "beg total stock",
    "Total Imports":      "total imports",
    "End Total Stock":    "end total stock",
    "Stock Change":       "stock change",
    "Stock-to-Use Ratio": "stock-to-use ratio",
}

# Label aliases for Indonesia rows — handles slight wording differences across years
ID_ITEM_LABELS: dict[str, list[str]] = {
    "Opening Stock":          ["beginning stock", "opening stock"],
    "Production":             ["production"],
    "Imports":                ["imports"],
    "Domestic Disappearance": ["domestic disappearance"],
    "Total Palm Oil Exports": ["total palm oil exports"],
    "Ending Stock":           ["ending stock", "closing stock"],
}

# ── Malaysia row map – historical Excel (pandas 0-based index) ──────────────
MY_ITEMS = {
    "Opening Stock":       2,
    "Production":          3,
    "Imports":             8,
    "Exports":            11,
    "Local Disappearance":13,
    "End Stock":          15,
}

# ── Malaysia row map – 2026 Google Sheet (pandas 0-based index) ─────────────
# Values in the sheet are in '000 MT — multiply by 1000 for actual MT.
MY_2026_ITEMS = {
    "Opening Stock":        4,   # "Beginning Stock"
    "Production":           5,
    "Imports":              7,
    "Local Disappearance": 11,   # "Domestic Consumption"
    "Exports":             13,
    "End Stock":           18,
}


def _parse_malaysia() -> dict[str, pd.DataFrame] | None:
    """
    Parse Sheet1. Date headers in row index 1 (actual datetimes).
    Returns {item_name: DataFrame[date, value, year, month]}.
    """
    try:
        df = pd.read_excel(SND_FILE, sheet_name="Sheet1", header=None)
    except Exception as exc:
        logger.warning("SND Sheet1 read failed: %s", exc)
        return None

    # Row 1 has datetime column headers
    date_row = df.iloc[1, 1:]
    dates = pd.to_datetime(date_row, errors="coerce")
    valid_mask = dates.notna()
    valid_cols = date_row.index[valid_mask]
    valid_dates = dates[valid_mask]

    result = {}
    for item_name, row_idx in MY_ITEMS.items():
        values = pd.to_numeric(df.iloc[row_idx, valid_cols], errors="coerce")
        series = pd.DataFrame({
            "date": valid_dates.values,
            "value": values.values,
        }).dropna()
        series["year"] = series["date"].dt.year
        series["month"] = series["date"].dt.month
        result[item_name] = series.reset_index(drop=True)

    return result


def _parse_malaysia_2026() -> dict[str, pd.DataFrame] | None:
    """
    Fetch Malaysia 2026 SND from public Google Sheet (CSV export).
    Month headers in row 2, data columns 1-12 (Jan-Dec).
    Values are in '000 MT — multiply by 1000.
    Returns {item_name: DataFrame[date, value, year, month]}.
    """
    try:
        df = pd.read_csv(MY_2026_URL, header=None)
    except Exception as exc:
        logger.warning("Malaysia 2026 Google Sheet fetch failed: %s", exc)
        return None

    # Monthly columns are indices 1–12 (Jan–Dec)
    month_cols = list(range(1, 13))
    dates = [pd.Timestamp(year=2026, month=m, day=1) for m in range(1, 13)]

    result = {}
    for item_name, row_idx in MY_2026_ITEMS.items():
        raw_vals = df.iloc[row_idx, month_cols]
        # Values are comma-formatted strings like '3,052' — strip commas
        values = raw_vals.apply(
            lambda x: pd.to_numeric(str(x).replace(",", ""), errors="coerce")
        ) * 1000  # '000 MT → MT

        series = pd.DataFrame({
            "date": dates,
            "value": values.values,
        }).dropna()
        series["year"] = series["date"].dt.year
        series["month"] = series["date"].dt.month
        result[item_name] = series.reset_index(drop=True)

    return result


def fetch_malaysia_2026_table() -> pd.DataFrame | None:
    """
    Fetch the full Malaysia 2026 SND sheet as a display-ready DataFrame.
    Returns all rows (section headers, data, totals, ratios) and all columns
    (Jan-Dec + Q1-Q4, H1, H2, Annual) for rendering as a static table.
    """
    try:
        df = pd.read_csv(MY_2026_URL, header=None)
    except Exception as exc:
        logger.warning("Malaysia 2026 table fetch failed: %s", exc)
        return None

    # Keep columns 0–20 (label + Jan–Dec + Q1–Q4 + H1 + H2 + Annual)
    df = df.iloc[:, :21]

    # Row 2 has the column headers (Jan, Feb, ..., Q1, Q2, ..., Annual)
    headers = [""] + df.iloc[2, 1:].fillna("").astype(str).tolist()
    df.columns = headers

    # Keep rows 3–23 (content rows: skip title/unit rows 0-2 and trailing rows 24-25)
    table = df.iloc[3:24].reset_index(drop=True)

    # Clean up: replace NaN with empty string for display
    table = table.fillna("")

    return table


def fetch_indonesia_2026_table() -> pd.DataFrame | None:
    """
    Fetch the full Indonesia 2026 SND sheet as a display-ready DataFrame.
    Returns all rows (section headers, data, totals, ratios) and all columns
    (Jan-Dec + Q1-Q4, H1, H2, Annual, YoY%) for rendering as a static table.
    """
    try:
        df = pd.read_csv(ID_2026_URL, header=None)
    except Exception as exc:
        logger.warning("Indonesia 2026 table fetch failed: %s", exc)
        return None

    # Keep columns 0–20
    df = df.iloc[:, :21]

    # Row 2 has the column headers
    headers = [""] + df.iloc[2, 1:].fillna("").astype(str).tolist()
    df.columns = headers

    # Keep rows 3–33 (content rows: skip title/unit rows 0-2 and trailing rows 34-35)
    table = df.iloc[3:34].reset_index(drop=True)

    # Clean up: replace NaN with empty string for display
    table = table.fillna("")

    return table


def fetch_india_2026_table() -> pd.DataFrame | None:
    """
    Fetch the India Edible-Oil S&D 2026 sheet as a display-ready DataFrame.
    Uses columns A–R (label, Source, Jan–Dec, Annual, 2025, Chg, YoY%) and the
    content rows Consumption … Stock-to-Use Ratio (sheet rows 6–42), rendered
    with the exact numbers/format from the sheet.
    """
    try:
        df = pd.read_csv(INDIA_URL, header=None)
    except Exception as exc:
        logger.warning("India 2026 table fetch failed: %s", exc)
        return None

    # Columns A–R (0–17): label + Source + Jan–Dec + Annual + 2025 + Chg + YoY%
    df = df.iloc[:, :18]

    # Row index 3 (sheet row 4) holds the column headers (Source, Jan…Dec, …)
    headers = [""] + df.iloc[3, 1:].fillna("").astype(str).tolist()
    df.columns = headers

    # Render sheet rows 1–42 verbatim: keep the title/units lines (idx 0–2) as
    # literal rows, then the data body (idx 5–41, Consumption … Stock-to-Use
    # Ratio). The header row itself (idx 3) becomes the styled header above;
    # idx 4 is a blank spacer.
    table = pd.concat([df.iloc[0:3], df.iloc[5:42]]).reset_index(drop=True)

    # Clean up: replace NaN with empty string for display
    table = table.fillna("")

    return table


def load_snd_india() -> dict[str, pd.DataFrame] | None:
    """
    Load the India SND monthly series requested for the Graphs sub-tab.
    Returns {item_name: DataFrame[date, value, year, month]} for 2026 (the only
    year with monthly data in the sheet). Values are kept in the sheet's native
    '000 MT (ratios in %). Returns None on failure / no rows matched.
    """
    try:
        df = pd.read_csv(INDIA_URL, header=None)
    except Exception as exc:
        logger.warning("India SND Google Sheet fetch failed: %s", exc)
        return None

    # Build {lowercase_label: first_row_index} from column A
    label_to_row: dict[str, int] = {}
    for i, row in df.iterrows():
        cell = str(row.iloc[0]).strip().lower()
        if cell and cell != "nan":
            label_to_row.setdefault(cell, i)

    month_cols = list(range(2, 14))  # C–N = Jan–Dec
    dates = [pd.Timestamp(year=2026, month=m, day=1) for m in range(1, 13)]

    result = {}
    for item_name, prefix in INDIA_GRAPH_ITEMS.items():
        row_idx = next((idx for label, idx in label_to_row.items()
                        if label.startswith(prefix)), None)
        if row_idx is None:
            continue
        raw_vals = df.iloc[row_idx, month_cols]
        values = raw_vals.apply(
            lambda x: pd.to_numeric(
                str(x).replace(",", "").replace("(", "-").replace(")", ""),
                errors="coerce")
        )
        series = pd.DataFrame({"date": dates, "value": values.values})
        series = series.dropna(subset=["value"])
        if series.empty:
            continue
        series["year"] = series["date"].dt.year
        series["month"] = series["date"].dt.month
        result[item_name] = series.reset_index(drop=True)

    if not result:
        return None

    ordered = {k: result[k] for k in INDIA_GRAPH_ITEMS if k in result}
    print(f"SND India loaded: {len(ordered)} items")
    return ordered


def fetch_exports_2026_table() -> pd.DataFrame | None:
    """
    Fetch the Malindo 2026 Exports sheet as a display-ready DataFrame.
    Returns all destination rows, totals, and MY/ID split data.
    """
    try:
        df = pd.read_csv(EXPORTS_2026_URL, header=None)
    except Exception as exc:
        logger.warning("Exports 2026 table fetch failed: %s", exc)
        return None

    # Keep all 20 columns
    df = df.iloc[:, :20]

    # Row 3 has the column headers
    headers = df.iloc[3].fillna("").astype(str).tolist()
    df.columns = headers

    # Keep rows 4–46 (data + totals + MY/ID split + note; skip title/header rows 0-3 and trailing 47-48)
    table = df.iloc[4:47].reset_index(drop=True)

    # Clean up
    table = table.fillna("")

    return table


def fetch_bmd_curve() -> dict[int, float]:
    """
    Get latest FCPO settlement prices (MYR/MT) mapped to calendar months.
    Uses the c1-c12 rolling logic:
      - If today <= 15th: c1 = current month
      - If today >  15th: c1 = next month
    Returns {month_number: settlement_price} for months within 2026.
    """
    raw_file = ROOT / "data" / "raw" / "all_instruments_raw.csv"
    if not raw_file.exists():
        logger.warning("Raw data file not found: %s", raw_file)
        return {}

    df = pd.read_csv(raw_file)
    # Filter to FCPO c1-c12
    fcpo_rics = {f"FCPOc{i}" for i in range(1, 13)}
    df = df[df["RIC"].isin(fcpo_rics)].copy()
    if df.empty:
        return {}

    df["Trade Date"] = pd.to_datetime(df["Trade Date"])

    # Get the latest trade date available
    latest_date = df["Trade Date"].max()

    # Determine c1 calendar month
    if latest_date.day <= 15:
        c1_month_start = latest_date.replace(day=1)
    else:
        # Next month
        if latest_date.month == 12:
            c1_month_start = pd.Timestamp(year=latest_date.year + 1, month=1, day=1)
        else:
            c1_month_start = pd.Timestamp(year=latest_date.year, month=latest_date.month + 1, day=1)

    # Get latest settlement price for each RIC
    latest = df[df["Trade Date"] == latest_date]
    prices = {}
    for i in range(1, 13):
        ric = f"FCPOc{i}"
        row = latest[latest["RIC"] == ric]
        if row.empty:
            continue
        # Use Settlement Price, fallback to Last
        sett = pd.to_numeric(row.iloc[0].get("Settlement Price"), errors="coerce")
        last = pd.to_numeric(row.iloc[0].get("Last"), errors="coerce")
        price = sett if pd.notna(sett) else last
        if pd.isna(price):
            continue

        # Map contract number to calendar month
        # c1 -> c1_month_start, c2 -> c1_month_start + 1 month, etc.
        cal_date = c1_month_start + pd.DateOffset(months=i - 1)
        if cal_date.year == 2026:
            prices[cal_date.month] = price

    return prices


def fetch_bmd_as_of() -> str:
    """Return the latest FCPO trade date from all_instruments_raw.csv as a display string."""
    raw_file = ROOT / "data" / "raw" / "all_instruments_raw.csv"
    if not raw_file.exists():
        return ""
    try:
        df = pd.read_csv(raw_file)
        df = df[df["RIC"].str.startswith("FCPO", na=False)]
        latest = pd.to_datetime(df["Trade Date"], errors="coerce").max()
        if pd.isna(latest):
            return ""
        return latest.strftime("%d %b %Y")
    except Exception:
        return ""


def _parse_indonesia_year(year: int, url: str) -> dict[str, pd.DataFrame] | None:
    """
    Fetch one year's Indonesia SND sheet and extract monthly values for each item.
    Row 0 is the header (Category, Jan … Dec, Q1 …). Monthly values are in cols 1–12.
    Matches rows by label (case-insensitive partial match) to handle wording differences.
    Returns {canonical_item_name: DataFrame[date, value, year, month]} or None on failure.
    """
    try:
        df = pd.read_csv(url, header=None)
    except Exception as exc:
        logger.warning("Indonesia %d Google Sheet fetch failed: %s", year, exc)
        return None

    # Build {lowercase_label: row_index} from column 0
    label_to_row: dict[str, int] = {}
    for i, row in df.iterrows():
        cell = str(row.iloc[0]).strip().lower()
        if cell:
            label_to_row[cell] = i

    month_cols = list(range(1, 13))
    dates = [pd.Timestamp(year=year, month=m, day=1) for m in range(1, 13)]

    result = {}
    for item_name, aliases in ID_ITEM_LABELS.items():
        row_idx = None
        for alias in aliases:
            for label, idx in label_to_row.items():
                if alias in label:
                    row_idx = idx
                    break
            if row_idx is not None:
                break

        if row_idx is None:
            continue  # row absent in this year's sheet — skip silently

        raw_vals = df.iloc[row_idx, month_cols]
        values = raw_vals.apply(
            lambda x: pd.to_numeric(str(x).replace(",", "").replace("(", "-").replace(")", ""),
                                    errors="coerce")
        )
        series = pd.DataFrame({"date": dates, "value": values.values})
        series = series.dropna(subset=["value"])
        if series.empty:
            continue
        series["year"] = year
        series["month"] = series["date"].dt.month
        result[item_name] = series.reset_index(drop=True)

    return result if result else None


def load_snd_indonesia() -> dict[str, pd.DataFrame] | None:
    """
    Load Indonesia SND data for 2024, 2025, 2026 from Google Sheets.
    Returns {item_name: DataFrame[date, value, year, month]} merged across all years.
    Items absent from a given year are simply omitted for that year.
    """
    combined: dict[str, list[pd.DataFrame]] = {}
    for year, url in ID_URLS.items():
        year_data = _parse_indonesia_year(year, url)
        if year_data is None:
            continue
        for item_name, df in year_data.items():
            combined.setdefault(item_name, []).append(df)

    if not combined:
        return None

    result = {}
    for item_name, dfs in combined.items():
        merged = pd.concat(dfs, ignore_index=True)
        merged = merged.drop_duplicates(subset="date").sort_values("date").reset_index(drop=True)
        result[item_name] = merged

    # Preserve display order from ID_ITEM_LABELS
    ordered = {k: result[k] for k in ID_ITEM_LABELS if k in result}
    print(f"SND Indonesia loaded: {len(ordered)} items across years {list(ID_URLS.keys())}")
    return ordered


def load_snd() -> dict[str, pd.DataFrame] | None:
    """
    Load Malaysia SND data (historical + 2026).
    Returns {item_name: DataFrame[date, value, year, month]} or None on failure.
    """
    if not SND_FILE.exists():
        logger.warning("SND file not found: %s", SND_FILE)
        return None

    my_hist = _parse_malaysia()
    if my_hist is None:
        return None

    my_2026 = _parse_malaysia_2026()

    # Merge historical + 2026 for each item
    result = {}
    for item_name, hist_df in my_hist.items():
        if my_2026 and item_name in my_2026:
            combined = pd.concat([hist_df, my_2026[item_name]], ignore_index=True)
            combined = combined.drop_duplicates(subset="date").sort_values("date").reset_index(drop=True)
            result[item_name] = combined
        else:
            result[item_name] = hist_df

    print(f"SND loaded: Malaysia={len(result)} items")
    return result


if __name__ == "__main__":
    result = load_snd()
    if result:
        print("\n--- Malaysia ---")
        for name, df in result.items():
            latest = df.iloc[-1]
            print(f"  {name}: {len(df)} months, latest={latest['date'].strftime('%Y-%m')} val={latest['value']:,.0f} MT")

    id_result = load_snd_indonesia()
    if id_result:
        print("\n--- Indonesia ---")
        for name, df in id_result.items():
            years = sorted(df["year"].unique())
            print(f"  {name}: {len(df)} months, years={years}")

    as_of = fetch_bmd_as_of()
    print(f"\nBMD prices as of: {as_of or '(not found)'}")
