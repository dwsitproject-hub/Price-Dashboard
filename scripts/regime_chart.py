"""
Multi-instrument $/TN comparison chart with a Feb-26 -> Mar-1 regime band.

Plots 7 LSEG series normalised to USD/tonne over Feb 2026 -> latest:
  FCPOc3, BOc3, LGOc3, COc3(=CLc3 WTI), LCOc3, Local Malaysia Olein, Local Indonesia Olein

Conversions reuse the logic in scripts/process.py / config.py.
"""
import os

import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

# ── Paths ────────────────────────────────────────────────────────────────────
HERE     = os.path.dirname(os.path.abspath(__file__))
ROOT     = os.path.dirname(HERE)
RAW_FILE = os.path.join(ROOT, "data", "raw", "all_instruments_raw.csv")
OUT_DIR  = os.path.join(ROOT, "output")
OUT_PNG  = os.path.join(OUT_DIR, "regime_chart_feb_jun_2026.png")

# ── Conversion constants (from config.py) ────────────────────────────────────
CENTSLB_CONV = 22.0462   # US cents/lb -> USD/MT
BBL_CONV     = 6.2898    # USD/barrel  -> USD/MT (WTI & Brent)

# RIC -> display label, in plot order
SERIES = [
    ("FCPOc3",       "FCPOc3 (MDEX Palm)"),
    ("BOc3",         "BOc3 (CBOT SoyOil)"),
    ("LGOc3",        "LGOc3 (ICE GasOil)"),
    ("CLc3",         "COc3 (NYMEX WTI)"),
    ("LCOc3",        "LCOc3 (ICE Brent)"),
    ("POL-MYRBD-M2", "Local Malaysia Olein"),
    ("POL-IDRBD-M2", "Local Indonesia Olein"),
]
PLOT_RICS = [ric for ric, _ in SERIES]
FX_RICS   = {"MYR="}

START      = pd.Timestamp("2026-02-01")
BAND_START = pd.Timestamp("2026-02-26")
BAND_END   = pd.Timestamp("2026-03-01")


def load_wide() -> pd.DataFrame:
    """Load raw CSV, resolve one price per (date, RIC), return wide frame."""
    df = pd.read_csv(RAW_FILE, parse_dates=["Trade Date"])
    df = df.rename(columns={"Trade Date": "date"})
    df["date"] = pd.to_datetime(df["date"]).dt.normalize()

    keep = set(PLOT_RICS) | FX_RICS
    df = df[df["RIC"].isin(keep)].copy()

    for c in ["Last", "Settlement Price", "Bid", "Ask"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    is_fx = df["RIC"].isin(FX_RICS)
    # FX -> mid; everything else -> Last, fallback Settlement
    df["price"] = df["Last"].fillna(df["Settlement Price"])
    df.loc[is_fx, "price"] = (df.loc[is_fx, "Bid"] + df.loc[is_fx, "Ask"]) / 2

    df = df.drop_duplicates(subset=["date", "RIC"])
    wide = df.pivot(index="date", columns="RIC", values="price").sort_index()
    return wide


def to_usd_per_tonne(wide: pd.DataFrame) -> pd.DataFrame:
    """Convert each plot column to USD/MT."""
    out = pd.DataFrame(index=wide.index)
    myr = wide["MYR="] if "MYR=" in wide.columns else None

    out["FCPOc3"]       = wide["FCPOc3"] / myr                 # MYR/MT -> USD/MT
    out["BOc3"]         = wide["BOc3"] * CENTSLB_CONV          # cents/lb -> USD/MT
    out["LGOc3"]        = wide["LGOc3"]                        # already USD/MT
    out["CLc3"]         = wide["CLc3"] * BBL_CONV              # USD/bbl -> USD/MT
    out["LCOc3"]        = wide["LCOc3"] * BBL_CONV             # USD/bbl -> USD/MT
    out["POL-MYRBD-M2"] = wide["POL-MYRBD-M2"]                 # FOB USD/MT
    out["POL-IDRBD-M2"] = wide["POL-IDRBD-M2"]                 # FOB USD/MT
    return out


def main():
    wide = load_wide()
    px = to_usd_per_tonne(wide)
    px = px[px.index >= START]

    # forward-fill thin cash series for continuous lines
    px = px.ffill()

    print(f"Date range: {px.index.min().date()} -> {px.index.max().date()}  ({len(px)} rows)")
    print("Last values ($/TN):")
    for ric, label in SERIES:
        v = px[ric].dropna()
        print(f"  {label:28s} {v.iloc[-1]:8.1f}" if len(v) else f"  {label:28s}   (no data)")

    # ── Plot ────────────────────────────────────────────────────────────────
    fig, ax = plt.subplots(figsize=(14, 8))

    for ric, label in SERIES:
        ax.plot(px.index, px[ric], label=label, linewidth=1.6)

    # Full-height regime band Feb 26 -> Mar 1
    ax.axvspan(BAND_START, BAND_END, color="grey", alpha=0.20, zorder=0)
    ymax = ax.get_ylim()[1]
    ax.text(BAND_START + (BAND_END - BAND_START) / 2, ymax * 0.985,
            "Regime\nFeb 26 - Mar 1", ha="center", va="top",
            fontsize=9, color="dimgrey", fontweight="bold")

    ax.set_title("Palm Complex & Crude — $/TN  (Feb 2026 → latest)", fontsize=15, fontweight="bold")
    ax.set_ylabel("$/TN  (USD per tonne)")
    ax.set_xlabel("Date")
    ax.grid(True, alpha=0.3)
    ax.legend(loc="upper left", ncol=2, framealpha=0.9, fontsize=9)
    ax.xaxis.set_major_locator(mdates.MonthLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
    ax.xaxis.set_minor_locator(mdates.WeekdayLocator(byweekday=mdates.MO))
    fig.autofmt_xdate()
    fig.tight_layout()

    os.makedirs(OUT_DIR, exist_ok=True)
    fig.savefig(OUT_PNG, dpi=150)
    print(f"\nSaved: {OUT_PNG}")

    try:
        os.startfile(OUT_PNG)  # noqa: Windows-only
    except Exception as e:
        print(f"(could not auto-open: {e})")


if __name__ == "__main__":
    main()
