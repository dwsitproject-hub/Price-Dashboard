"""
Ad-hoc spread chart: UCOME FOB ARA - Refined POME Indonesia FOB, and
Refined POME Indonesia FOB - FCPOc3, both in USD/MT, 2025-onwards.

Same data sources as adhoc_pome_ucome_fcpo_chart.py:
- Refined POME Indonesia FOB: Biofuels/Prices/RPOME.xlsx (raw Argus plugin dump)
- UCOME FOB ARA: LSEG Argus feed, data/processed/argus_data.csv, ARGUS_OUT_UCOME_M1
- FCPOc3: raw LSEG pull via process.py's load_prices -> to_wide -> convert_currencies

Spreads are computed on the intersection of dates across both legs (inner join),
so each spread only starts once RPOME data is available (2025-10-15).

Run: python scripts/adhoc_pome_spreads_chart.py
Output: output/adhoc_pome_spreads.png
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

    rpome  = load_rpome_series()
    ucome  = load_argus_series("ARGUS_OUT_UCOME_M1")
    fcpoc3 = lseg_usdmt["FCPOc3"].dropna()

    for label, s in [("Refined POME Indonesia FOB", rpome),
                      ("UCOME FOB ARA", ucome),
                      ("FCPOc3", fcpoc3)]:
        s = s[s.index >= START_DATE]
        print(f"{label:28s}: {s.index.min().date()} -> {s.index.max().date()}, n={len(s)}")

    ucome_rpome  = (ucome - rpome).dropna()
    ucome_rpome  = ucome_rpome[ucome_rpome.index >= START_DATE]

    rpome_fcpo   = (rpome - fcpoc3).dropna()
    rpome_fcpo   = rpome_fcpo[rpome_fcpo.index >= START_DATE]

    spreads = {
        "UCOME FOB ARA - Refined POME Indonesia FOB": ucome_rpome,
        "Refined POME Indonesia FOB - FCPOc3":        rpome_fcpo,
    }

    for label, s in spreads.items():
        print(f"{label:45s}: {s.index.min().date()} -> {s.index.max().date()}, "
              f"n={len(s)}, range {s.min():.0f}-{s.max():.0f} USD/MT")

    fig, ax = plt.subplots(figsize=(13, 6.5))
    colors = {
        "UCOME FOB ARA - Refined POME Indonesia FOB": "#1f77b4",
        "Refined POME Indonesia FOB - FCPOc3":        "#d62728",
    }

    for label, s in spreads.items():
        ax.plot(s.index, s.values, label=label, color=colors[label], linewidth=1.3)

    all_vals = pd.concat([s for s in spreads.values()])
    lo, hi = all_vals.min(), all_vals.max()
    pad = (hi - lo) * 0.05
    ax.set_ylim(lo - pad, hi + pad)

    ax.axhline(0, color="#999999", linewidth=0.8)
    ax.set_ylabel("USD/MT")
    ax.set_title("UCOME - Refined POME Indonesia FOB, and Refined POME Indonesia FOB - FCPOc3 (USD/MT, 2025+)")
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=1))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b'%y"))
    ax.grid(True, color="#e8e8e8", linewidth=0.7)
    plt.setp(ax.get_xticklabels(), rotation=45, ha="right")
    ax.legend(loc="upper left", fontsize=9)
    fig.tight_layout()

    out_path = OUT_DIR / "adhoc_pome_spreads.png"
    fig.savefig(out_path, dpi=150)
    print(f"\nSaved: {out_path}")


if __name__ == "__main__":
    main()
