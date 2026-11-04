"""
Ad-hoc comparison chart: Refined POME Indonesia FOB, UCOME FOB ARA, and FCPOc3
— all in USD/MT, 2025-onwards, single y-axis with autoscaling so the
day-to-day variation across all series is visible (no forced zero baseline).

Refined POME Indonesia FOB comes from the raw Argus Excel plugin dump
(Biofuels/Prices/RPOME.xlsx, column "RED Refined POME ... oil fob Indonesia
prompt ... USD/t, fob") — the dashboard's own Argus-tab POME series
(ARGUS_OUT_POME_PROMPT) was found to be wrong (~2000 USD/MT vs RPOME's
~1250 USD/MT for the same September 2026 dates), so this ad-hoc chart uses
RPOME.xlsx directly instead of going through argus_data.py.

UCOME comes from the live LSEG "Argus" feed (data/processed/argus_data.csv,
already USD/MT). FCPOc3 comes from the raw LSEG pull, run through process.py's
normal load_prices -> to_wide -> convert_currencies pipeline (MYR -> USD/MT
via MYR=).

Run: python scripts/adhoc_pome_ucome_fcpo_chart.py
Output: output/adhoc_pome_ucome_fcpo_malaysia.png
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

ARGUS_CSV  = ROOT / "data" / "processed" / "argus_data.csv"
RPOME_XLSX = ROOT.parent / "Biofuels" / "Prices" / "RPOME.xlsx"

START_DATE = "2025-01-01"

SERIES = {
    "Refined POME Indonesia FOB": ("rpome", None),
    "UCOME FOB ARA":              ("argus", "ARGUS_OUT_UCOME_M1"),
    "FCPOc3":                     ("lseg",  "FCPOc3"),
}


def load_argus_series(series_key: str) -> pd.Series:
    df = pd.read_csv(ARGUS_CSV)
    sub = df[df["series_key"] == series_key].copy()
    sub["date"] = pd.to_datetime(sub["date"])
    sub = sub.sort_values("date").drop_duplicates(subset="date", keep="last")
    return sub.set_index("date")["value"]


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

    data = {}
    for label, (source, key) in SERIES.items():
        if source == "argus":
            s = load_argus_series(key)
        elif source == "rpome":
            s = load_rpome_series()
        else:
            s = lseg_usdmt[key].dropna()
        s = s[s.index >= START_DATE]
        data[label] = s
        print(f"{label:28s} ({str(key):>20s}): {s.index.min().date()} -> "
              f"{s.index.max().date()}, n={len(s)}, "
              f"range {s.min():.0f}-{s.max():.0f} USD/MT")

    fig, ax = plt.subplots(figsize=(13, 6.5))
    colors = {
        "Refined POME Indonesia FOB": "#d62728",
        "UCOME FOB ARA":              "#1f77b4",
        "FCPOc3":                     "#2ca02c",
    }

    for label, s in data.items():
        ax.plot(s.index, s.values, label=label, color=colors[label], linewidth=1.3)

    # Autoscale y-axis to the actual data range (with a small padding) instead
    # of defaulting to a zero baseline, so the real variation is visible.
    all_vals = pd.concat([s for s in data.values()])
    lo, hi = all_vals.min(), all_vals.max()
    pad = (hi - lo) * 0.05
    ax.set_ylim(lo - pad, hi + pad)

    ax.set_ylabel("USD/MT")
    ax.set_title("Refined POME Indonesia FOB vs UCOME FOB ARA vs FCPOc3 (USD/MT, 2025+)")
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=1))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b'%y"))
    ax.grid(True, color="#e8e8e8", linewidth=0.7)
    plt.setp(ax.get_xticklabels(), rotation=45, ha="right")
    ax.legend(loc="upper left", fontsize=9)
    fig.tight_layout()

    out_path = OUT_DIR / "adhoc_pome_ucome_fcpo_malaysia.png"
    fig.savefig(out_path, dpi=150)
    print(f"\nSaved: {out_path}")


if __name__ == "__main__":
    main()
