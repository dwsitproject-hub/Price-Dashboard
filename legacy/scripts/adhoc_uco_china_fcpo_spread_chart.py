"""
Ad-hoc spread chart: UCO FOB China - FCPOc3, USD/MT, 2025+.

UCO FOB China (prompt): LSEG "Argus" feed, data/processed/argus_data.csv,
ARGUS_OUT_UCO_CHINA (already USD/t).
FCPOc3: raw LSEG pull via process.py's load_prices -> to_wide -> convert_currencies.

Spread computed on the intersection of dates (inner join).

Run: python scripts/adhoc_uco_china_fcpo_spread_chart.py
Output: output/adhoc_uco_china_fcpo_spread.png
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

ARGUS_CSV = ROOT / "data" / "processed" / "argus_data.csv"

START_DATE = "2025-01-01"


def load_argus_series(series_key: str) -> pd.Series:
    df = pd.read_csv(ARGUS_CSV)
    sub = df[df["series_key"] == series_key].copy()
    sub["date"] = pd.to_datetime(sub["date"])
    sub = sub.sort_values("date").drop_duplicates(subset="date", keep="last")
    return sub.set_index("date")["value"]


def load_lseg_usdmt() -> pd.DataFrame:
    long_df = process.load_prices()
    wide = process.to_wide(long_df)
    wide = process.convert_currencies(wide)
    return wide


def main():
    lseg_usdmt = load_lseg_usdmt()

    uco_china = load_argus_series("ARGUS_OUT_UCO_CHINA")
    fcpoc3    = lseg_usdmt["FCPOc3"].dropna()

    spread = (uco_china - fcpoc3).dropna()
    spread = spread[spread.index >= START_DATE]

    print(f"UCO FOB China - FCPOc3: {spread.index.min().date()} -> "
          f"{spread.index.max().date()}, n={len(spread)}, "
          f"range {spread.min():.0f}-{spread.max():.0f} USD/MT, "
          f"mean {spread.mean():.0f} USD/MT")

    fig, ax = plt.subplots(figsize=(13, 6.5))
    ax.plot(spread.index, spread.values, color="#9467bd", linewidth=1.3)

    lo, hi = spread.min(), spread.max()
    pad = (hi - lo) * 0.05
    ax.set_ylim(lo - pad, hi + pad)

    ax.axhline(0, color="#999999", linewidth=0.8)
    ax.axhline(spread.mean(), color="#999999", linewidth=0.8, linestyle="--",
               label=f"Mean: {spread.mean():.0f}")
    ax.set_ylabel("USD/MT")
    ax.set_title("UCO FOB China - FCPOc3 (USD/MT, 2025+)")
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=1))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b'%y"))
    ax.grid(True, color="#e8e8e8", linewidth=0.7)
    plt.setp(ax.get_xticklabels(), rotation=45, ha="right")
    ax.legend(loc="upper left", fontsize=9)
    fig.tight_layout()

    out_path = OUT_DIR / "adhoc_uco_china_fcpo_spread.png"
    fig.savefig(out_path, dpi=150)
    print(f"\nSaved: {out_path}")


if __name__ == "__main__":
    main()
