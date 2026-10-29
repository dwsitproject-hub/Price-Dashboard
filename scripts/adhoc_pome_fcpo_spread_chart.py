"""
Ad-hoc spread chart: Refined POME Indonesia FOB - FCPOc3, USD/MT, 2025+.

Refined POME Indonesia FOB: Biofuels/Prices/RPOME.xlsx (raw Argus plugin dump).
FCPOc3: raw LSEG pull via process.py's load_prices -> to_wide -> convert_currencies.

Spread computed on the intersection of dates (inner join) — starts once RPOME
data is available (2025-10-15).

Run: python scripts/adhoc_pome_fcpo_spread_chart.py
Output: output/adhoc_pome_fcpo_spread.png
"""
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import process

OUT_DIR = ROOT / "output"
OUT_DIR.mkdir(exist_ok=True)

RPOME_XLSX = ROOT.parent / "Biofuels" / "Prices" / "RPOME.xlsx"

START_DATE = "2025-01-01"


def load_rpome_series() -> pd.Series:
    """RED Refined POME oil fob Indonesia prompt (Singapore close, USD/t) —
    raw Argus Excel plugin dump, column index 1 of Sheet1."""
    raw = pd.read_excel(RPOME_XLSX, sheet_name="Sheet1", header=None)
    df = raw.iloc[2:, [0, 1]].copy()
    df.columns = ["date", "value"]
    df["date"] = pd.to_datetime(df["date"])
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df = df.dropna(subset=["value"]).sort_values("date")
    df = df.drop_duplicates(subset="date", keep="last")
    return df.set_index("date")["value"]


def load_lseg_usdmt() -> pd.DataFrame:
    long_df = process.load_prices()
    wide = process.to_wide(long_df)
    wide = process.convert_currencies(wide)
    return wide


def main():
    lseg_usdmt = load_lseg_usdmt()

    rpome  = load_rpome_series()
    fcpoc3 = lseg_usdmt["FCPOc3"].dropna()

    spread = (rpome - fcpoc3).dropna()
    spread = spread[spread.index >= START_DATE]

    print(f"Refined POME Indonesia FOB - FCPOc3: {spread.index.min().date()} -> "
          f"{spread.index.max().date()}, n={len(spread)}, "
          f"range {spread.min():.0f}-{spread.max():.0f} USD/MT, "
          f"mean {spread.mean():.0f} USD/MT")

    fig, ax = plt.subplots(figsize=(13, 6.5))
    ax.plot(spread.index, spread.values, color="#d62728", linewidth=1.3)

    lo, hi = spread.min(), spread.max()
    pad = (hi - lo) * 0.05
    ax.set_ylim(lo - pad, hi + pad)

    ax.axhline(0, color="#999999", linewidth=0.8)
    ax.axhline(spread.mean(), color="#999999", linewidth=0.8, linestyle="--",
               label=f"Mean: {spread.mean():.0f}")
    ax.set_ylabel("USD/MT")
    ax.set_title("Refined POME Indonesia FOB - FCPOc3 (USD/MT, 2025+)")
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=1))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b'%y"))
    ax.grid(True, color="#e8e8e8", linewidth=0.7)
    plt.setp(ax.get_xticklabels(), rotation=45, ha="right")
    ax.legend(loc="upper left", fontsize=9)
    fig.tight_layout()

    out_path = OUT_DIR / "adhoc_pome_fcpo_spread.png"
    fig.savefig(out_path, dpi=150)
    print(f"\nSaved: {out_path}")


if __name__ == "__main__":
    main()
