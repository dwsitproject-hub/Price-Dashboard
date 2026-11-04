"""
Fetch India local edible-oil prices from a public Google Sheet (CSV export).
Returns full time-series DataFrames in both USD/MT and INR/MT for seasonal charts.
"""

import logging
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

GSHEET_CSV_URL = (
    "https://docs.google.com/spreadsheets/d/"
    "1tY63YWHUm8LHEKUJAtObl4zsIiAyeVmZ/export?format=csv&gid=998039497"
)

# Source columns (USD/MT) → internal keys
PRICE_COLUMNS = {
    "Olein":          "INDIA_OLEIN",
    "Soya Oil Ref":   "INDIA_SOYA_OIL_REF",
    "Gnut Oil Raw":   "INDIA_GNUT_OIL_RAW",
    "Rapeseed Oil":   "INDIA_RAPESEED_OIL",
    "Cotton Oil Ref": "INDIA_COTTON_OIL_REF",
    "Rice Bran Oil":  "INDIA_RICE_BRAN_OIL",
}

# Display titles
INDIA_TITLES = {
    "INDIA_OLEIN":          "Local India Olein",
    "INDIA_SOYA_OIL_REF":   "Local India Soya Oil Ref",
    "INDIA_GNUT_OIL_RAW":   "Local India Grunt Oil Raw",
    "INDIA_RAPESEED_OIL":   "Local India Rapeseed Oil",
    "INDIA_COTTON_OIL_REF": "Local India Cotton Oil Ref",
    "INDIA_RICE_BRAN_OIL":  "Local India Rice Bran Oil",
}

INDIA_OUTRIGHT_KEYS = list(PRICE_COLUMNS.values())

# All 15 pairwise spreads (A - B)
INDIA_SPREAD_DEFS: list[tuple[str, str, str]] = []
for _i, _key_a in enumerate(INDIA_OUTRIGHT_KEYS):
    for _key_b in INDIA_OUTRIGHT_KEYS[_i + 1:]:
        spread_key = f"{_key_a}_vs_{_key_b}"
        INDIA_SPREAD_DEFS.append((_key_a, _key_b, spread_key))

INDIA_SPREAD_KEYS = [s[2] for s in INDIA_SPREAD_DEFS]

# Build spread titles from outright titles
for _ka, _kb, _sk in INDIA_SPREAD_DEFS:
    INDIA_TITLES[_sk] = f"{INDIA_TITLES[_ka]} - {INDIA_TITLES[_kb]}"


def _to_chart_df(series: pd.Series) -> pd.DataFrame:
    """Convert a date-indexed Series into the dashboard's standard chart DataFrame."""
    df = series.dropna().reset_index()
    df.columns = ["date", "spread_value"]
    df["year"] = df["date"].dt.year
    df["day_of_year"] = df["date"].dt.dayofyear
    df["month_day"] = df["date"].dt.strftime("%m-%d")
    return df


def fetch_india_data() -> Optional[tuple[dict, dict, dict, dict]]:
    """
    Fetch full history from Google Sheet.
    Returns (usd_outrights, inr_outrights, usd_spreads, inr_spreads)
    where each is {key: DataFrame} with standard chart columns.
    Returns None on failure.
    """
    try:
        df = pd.read_csv(GSHEET_CSV_URL)
    except Exception as exc:
        logger.warning("Google Sheet fetch failed: %s", exc)
        return None

    needed = ["Date", "FX"] + list(PRICE_COLUMNS.keys())
    missing = [c for c in needed if c not in df.columns]
    if missing:
        logger.warning("Google Sheet missing columns: %s", missing)
        return None

    df["Date"] = pd.to_datetime(df["Date"], dayfirst=True, errors="coerce")
    df = df.dropna(subset=["Date"]).sort_values("Date").set_index("Date")

    # Coerce numeric
    df["FX"] = pd.to_numeric(df["FX"], errors="coerce")
    for src_col in PRICE_COLUMNS:
        df[src_col] = pd.to_numeric(df[src_col], errors="coerce")

    if df.empty or df["FX"].dropna().empty:
        logger.warning("Google Sheet has no valid data rows.")
        return None

    # Build outright series (USD and INR)
    usd_outrights, inr_outrights = {}, {}
    for src_col, key in PRICE_COLUMNS.items():
        usd_series = df[src_col]
        inr_series = df[src_col] * df["FX"]
        usd_outrights[key] = _to_chart_df(usd_series)
        inr_outrights[key] = _to_chart_df(inr_series)

    # Build spread series (USD and INR)
    usd_spreads, inr_spreads = {}, {}
    for key_a, key_b, spread_key in INDIA_SPREAD_DEFS:
        src_a = [s for s, k in PRICE_COLUMNS.items() if k == key_a][0]
        src_b = [s for s, k in PRICE_COLUMNS.items() if k == key_b][0]
        usd_spreads[spread_key] = _to_chart_df(df[src_a] - df[src_b])
        inr_spreads[spread_key] = _to_chart_df((df[src_a] - df[src_b]) * df["FX"])

    loaded = sum(1 for d in usd_outrights.values() if not d.empty)
    logger.info("India Local: %d outrights, %d spreads loaded.", loaded, len(usd_spreads))
    return usd_outrights, inr_outrights, usd_spreads, inr_spreads


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = fetch_india_data()
    if result:
        usd_out, inr_out, usd_spr, inr_spr = result
        print("--- India Local Outrights ---")
        for key, df in usd_out.items():
            latest = df.iloc[-1]["spread_value"] if not df.empty else None
            inr_latest = inr_out[key].iloc[-1]["spread_value"] if not inr_out[key].empty else None
            print(f"  {INDIA_TITLES[key]}: USD {latest:,.0f}  |  INR {inr_latest:,.0f}  ({len(df)} rows)")
        print(f"\n--- India Local Spreads: {len(usd_spr)} pairs ---")
    else:
        print("Failed to fetch India data.")
