"""
Rolling correlations of Crush C3 spread vs Soybean Oil, Meal, and Soybeans (c3).
Plots 30D, 60D, 90D rolling correlations with the spread on a mirror y-axis.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
import config

PROC_DIR = ROOT / "data" / "processed"
RAW_FILE = ROOT / "data" / "raw" / "all_instruments_raw.csv"

WINDOWS = [30, 60, 90]
COLORS  = {30: "#1f77b4", 60: "#ff7f0e", 90: "#2ca02c"}

PAIRS = [
    ("BOc3",  "Soybean Oil C3"),
    ("SMc3",  "Soybean Meal C3"),
    ("Sc3",   "Soybean C3"),
]


def load_data() -> pd.DataFrame:
    # Load crush spread
    crush_path = PROC_DIR / "CRUSH_c3.csv"
    crush = pd.read_csv(crush_path, parse_dates=["date"])
    crush = crush.set_index("date").rename(columns={"spread_value": "CRUSH_c3"})

    # Load raw outright prices for BO, SM, S (c3)
    raw = pd.read_csv(RAW_FILE, parse_dates=["Trade Date"])
    raw = raw.rename(columns={"Trade Date": "date"})
    raw["date"] = pd.to_datetime(raw["date"]).dt.normalize()

    needed = {"BOc3", "SMc3", "Sc3"}
    raw = raw[raw["RIC"].isin(needed)].copy()
    raw["Last"] = pd.to_numeric(raw.get("Last"), errors="coerce")
    raw["Settlement Price"] = pd.to_numeric(raw.get("Settlement Price"), errors="coerce")
    raw["price"] = np.where(raw["Last"].notna(), raw["Last"], raw["Settlement Price"])
    raw = raw.drop_duplicates(subset=["date", "RIC"])

    wide = raw.pivot(index="date", columns="RIC", values="price").sort_index().ffill()

    # Merge
    df = crush.join(wide, how="inner").sort_index()
    df = df[df.index >= "2019-01-01"]
    return df


def plot_corr(ax, df, ric, label):
    crush = df["CRUSH_c3"]
    commodity = df[ric]
    spread = crush - commodity / 100 if ric == "Sc3" else crush  # just show crush on mirror

    # Rolling correlations
    for w in WINDOWS:
        corr = crush.rolling(w).corr(commodity)
        latest = corr.dropna().iloc[-1]
        ax.plot(df.index, corr, color=COLORS[w], linewidth=1.2,
                label=f"{w}D (latest: {latest:.3f})")
        # Highlight latest value
        last_idx = corr.dropna().index[-1]
        ax.scatter(last_idx, latest, color=COLORS[w], s=50, zorder=5, edgecolors="black", linewidths=0.5)
        ax.annotate(f"{latest:.3f}", (last_idx, latest),
                    textcoords="offset points", xytext=(8, 0),
                    fontsize=8, fontweight="bold", color=COLORS[w])

    ax.set_ylabel("Rolling Correlation", fontsize=10)
    ax.axhline(0, color="gray", linewidth=0.5, linestyle="--")
    ax.set_ylim(-1.05, 1.05)
    ax.legend(loc="upper left", fontsize=8, framealpha=0.9)
    ax.set_title(f"Crush C3 vs {label} — Rolling Correlation", fontsize=12, fontweight="bold")

    # Mirror y-axis: plot the spread (Crush - commodity price on comparable basis)
    ax2 = ax.twinx()
    # Plot both series for visual reference
    ax2.plot(df.index, crush, color="#d62728", linewidth=0.8, alpha=0.4, label="Crush C3")
    ax2.plot(df.index, commodity, color="#9467bd", linewidth=0.8, alpha=0.4, label=f"{label}")
    ax2.set_ylabel(f"Price Level", fontsize=10, color="#666")
    ax2.tick_params(axis="y", labelcolor="#666")
    ax2.legend(loc="upper right", fontsize=8, framealpha=0.9)

    ax.xaxis.set_major_locator(mdates.YearLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    ax.grid(True, alpha=0.3)
    ax.set_zorder(ax2.get_zorder() + 1)
    ax.patch.set_visible(False)


def main():
    df = load_data()
    print(f"Data: {len(df)} rows, {df.index.min().date()} to {df.index.max().date()}")

    fig, axes = plt.subplots(3, 1, figsize=(16, 14), constrained_layout=True)
    fig.suptitle("Crush Spread C3 — Rolling Correlations (from 2019)",
                 fontsize=14, fontweight="bold", y=1.01)

    for ax, (ric, label) in zip(axes, PAIRS):
        plot_corr(ax, df, ric, label)

    out_path = ROOT / "crush_correlations.png"
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    print(f"Saved to {out_path}")
    plt.show()


if __name__ == "__main__":
    main()
