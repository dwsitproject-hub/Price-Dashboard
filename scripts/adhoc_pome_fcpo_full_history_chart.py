"""
Ad-hoc comparison chart: Refined POME Indonesia FOB vs FCPOc3, USD/MT,
full available history for each leg (no date clipping).

Refined POME Indonesia FOB comes from the raw Argus Excel plugin dump
(Biofuels/Prices/RPOME.xlsx, column "RED Refined POME ... oil fob Indonesia
prompt ... USD/t, fob") — only available from 2025-10-15 onwards.

FCPOc3 comes from the raw LSEG pull, run through process.py's normal
load_prices -> to_wide -> convert_currencies pipeline (MYR -> USD/MT via
MYR=) — available from 2015-01-02 onwards.

Each series is plotted over its own full history (not clipped to the
intersection), so FCPOc3 shows the full ~11yr history while RPOME only
appears on the recent ~1yr segment where Argus data exists.

Run: python scripts/adhoc_pome_fcpo_full_history_chart.py
Output: output/adhoc_pome_fcpo_full_history.png
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

    data = {
        "Refined POME Indonesia FOB": rpome,
        "FCPOc3":                     fcpoc3,
    }

    for label, s in data.items():
        print(f"{label:28s}: {s.index.min().date()} -> {s.index.max().date()}, "
              f"n={len(s)}, range {s.min():.0f}-{s.max():.0f} USD/MT")

    fig, ax = plt.subplots(figsize=(13, 6.5))
    colors = {
        "Refined POME Indonesia FOB": "#d62728",
        "FCPOc3":                     "#2ca02c",
    }

    for label, s in data.items():
        ax.plot(s.index, s.values, label=label, color=colors[label], linewidth=1.1)

    all_vals = pd.concat([s for s in data.values()])
    lo, hi = all_vals.min(), all_vals.max()
    pad = (hi - lo) * 0.05
    ax.set_ylim(lo - pad, hi + pad)

    ax.set_ylabel("USD/MT")
    ax.set_title("Refined POME Indonesia FOB vs FCPOc3 (USD/MT, full history)")
    ax.xaxis.set_major_locator(mdates.YearLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    ax.grid(True, color="#e8e8e8", linewidth=0.7)
    plt.setp(ax.get_xticklabels(), rotation=0, ha="center")
    ax.legend(loc="upper left", fontsize=9)
    fig.tight_layout()

    out_path = OUT_DIR / "adhoc_pome_fcpo_full_history.png"
    fig.savefig(out_path, dpi=150)
    print(f"\nSaved: {out_path}")


if __name__ == "__main__":
    main()
