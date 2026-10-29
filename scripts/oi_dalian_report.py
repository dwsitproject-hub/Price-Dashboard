"""
oi_dalian_report.py
===================
OI analysis for Dalian: DCP (Palm) vs DBY (Soybean Oil).
Both priced in CNY — no FX conversion needed for the spread.

Uses liquid month (highest OI) for each product.
Analyzes price spread, OI dynamics, signal cross-correlation,
and whether buildup in one product explains spread movement.

Horizons: 1D, 2D, 5D, 15D
Outputs: PNGs + markdown report to data/oi/
"""

import warnings
from pathlib import Path
from datetime import date

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy import stats

warnings.filterwarnings("ignore", category=FutureWarning)

ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / "data" / "oi"
RAW_FILE = DATA_DIR / "dcp_dby_oi_raw.csv"

NOISE = 0.1
SIGNALS = ["LBU", "SBU", "SC", "LU", "Neutral"]
SIG_COLORS = {
    "LBU": "#2ecc71", "SBU": "#e74c3c",
    "SC": "#3498db", "LU": "#e67e22", "Neutral": "#95a5a6",
}
HORIZONS = [1, 2, 5, 15]

REPORT = []


def rprint(line=""):
    print(line)
    REPORT.append(line)


# ═══════════════════════════════════════════════════════════════════════════════
# DATA PREP
# ═══════════════════════════════════════════════════════════════════════════════

def load_data():
    df = pd.read_csv(RAW_FILE)
    df["date"] = pd.to_datetime(df["Trade Date"])
    df["price"] = pd.to_numeric(df["Last"], errors="coerce").fillna(
        pd.to_numeric(df["Settlement Price"], errors="coerce")
    )
    df["oi"] = pd.to_numeric(df["Open Interest"], errors="coerce")
    df["volume"] = pd.to_numeric(df["Volume"], errors="coerce")
    df = df[["date", "RIC", "price", "oi", "volume"]].dropna(subset=["price"])

    df["product"] = df["RIC"].str.extract(r"^([A-Z]+)")
    df["cn"] = df["RIC"].str.extract(r"c(\d)").astype(int)
    df = df.sort_values(["RIC", "date"]).reset_index(drop=True)

    # day-on-day changes per RIC
    df["price_prev"] = df.groupby("RIC")["price"].shift(1)
    df["oi_prev"] = df.groupby("RIC")["oi"].shift(1)
    df["price_chg_pct"] = (df["price"] - df["price_prev"]) / df["price_prev"] * 100
    df["oi_chg"] = df["oi"] - df["oi_prev"]
    df["oi_chg_pct"] = df["oi_chg"] / df["oi_prev"] * 100
    df["signal"] = df.apply(_classify, axis=1)

    return df


def _classify(row):
    p, o = row["price_chg_pct"], row["oi_chg_pct"]
    if pd.isna(p) or pd.isna(o):
        return np.nan
    if abs(p) < NOISE:
        return "Neutral"
    if p > 0 and o > 0:
        return "LBU"
    if p < 0 and o > 0:
        return "SBU"
    if p > 0 and o <= 0:
        return "SC"
    return "LU"


def get_liquid_month(df, product):
    """For each day, return the contract with highest OI."""
    sub = df[(df["product"] == product) & df["oi"].notna()].copy()
    idx = sub.groupby("date")["oi"].idxmax()
    liq = sub.loc[idx, ["date", "cn", "price", "oi", "signal"]].copy()
    liq = liq.rename(columns={
        "cn": f"{product.lower()}_cn",
        "price": f"{product.lower()}_price",
        "oi": f"{product.lower()}_oi",
        "signal": f"{product.lower()}_signal",
    })
    return liq


def get_curve_oi(df, product):
    """Sum OI across all contracts per day."""
    sub = df[df["product"] == product].groupby("date")["oi"].sum().reset_index()
    sub = sub.rename(columns={"oi": f"{product.lower()}_curve_oi"})
    return sub


# ═══════════════════════════════════════════════════════════════════════════════
# MODULE 1: Liquid Month Identification & Spread Construction
# ═══════════════════════════════════════════════════════════════════════════════

def module_1(df):
    rprint("\n" + "=" * 70)
    rprint("## Module 1: Liquid Month & DCP-DBY Spread Construction")
    rprint("=" * 70)

    dcp_liq = get_liquid_month(df, "DCP")
    dby_liq = get_liquid_month(df, "DBY")

    spread = dcp_liq.merge(dby_liq, on="date", how="inner").sort_values("date")
    spread["spread"] = spread["dcp_price"] - spread["dby_price"]

    # active month stats
    rprint("\n### Active Contract Distribution\n")
    for prod, col in [("DCP", "dcp_cn"), ("DBY", "dby_cn")]:
        counts = spread[col].value_counts().sort_index()
        rprint(f"\n**{prod} (Highest OI Month)**\n")
        rprint("| Contract | Days | % |")
        rprint("|----------|------|---|")
        for cn, cnt in counts.items():
            rprint(f"| c{cn} | {cnt} | {cnt / len(spread) * 100:.1f}% |")

    # chart: active months over time
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(16, 5), sharex=True)
    ax1.scatter(spread["date"], spread["dcp_cn"], s=1, alpha=0.3, c="steelblue")
    ax1.set_ylabel("DCP Active")
    ax1.set_yticks(range(1, 9))
    ax1.set_title("Liquid Month (Highest OI) Over Time")
    ax2.scatter(spread["date"], spread["dby_cn"], s=1, alpha=0.3, c="darkorange")
    ax2.set_ylabel("DBY Active")
    ax2.set_yticks(range(1, 9))
    plt.tight_layout()
    plt.savefig(DATA_DIR / "oi_dalian_active_months.png", dpi=150)
    plt.close()

    # spread time series
    fig, ax = plt.subplots(figsize=(16, 5))
    ax.plot(spread["date"], spread["spread"], lw=0.8, color="navy")
    ax.set_ylabel("DCP - DBY (CNY/MT)")
    ax.set_title("DCP - DBY Spread (Liquid Month Prices)")
    ax.axhline(0, color="black", lw=0.5)
    plt.tight_layout()
    plt.savefig(DATA_DIR / "oi_dalian_spread_ts.png", dpi=150)
    plt.close()

    rprint(f"\nSpread stats: mean={spread['spread'].mean():.1f}, "
           f"std={spread['spread'].std():.1f}, "
           f"min={spread['spread'].min():.1f}, max={spread['spread'].max():.1f}")

    return spread


# ═══════════════════════════════════════════════════════════════════════════════
# MODULE 2: Signal Predictive Power on Spread
# ═══════════════════════════════════════════════════════════════════════════════

def module_2(df, spread):
    rprint("\n" + "=" * 70)
    rprint("## Module 2: Individual Product Signals vs Spread Movement")
    rprint("=" * 70)

    # enrich spread with OI changes
    dcp_liq_full = df[(df["product"] == "DCP") & df["oi"].notna()].copy()
    dby_liq_full = df[(df["product"] == "DBY") & df["oi"].notna()].copy()

    # get day-on-day OI change for liquid month
    # re-derive from spread's active contract
    sp = spread.copy()
    sp["spread_chg"] = sp["spread"].diff()

    # forward spread returns
    for h in HORIZONS:
        sp[f"spread_fwd_{h}d"] = sp["spread"].shift(-h) - sp["spread"]

    # merge OI changes from full data for each product's liquid month
    for prod, cn_col in [("DCP", "dcp_cn"), ("DBY", "dby_cn")]:
        prod_data = df[df["product"] == prod][["date", "cn", "oi_chg", "oi_chg_pct", "signal", "price_chg_pct"]].copy()
        merged = sp[["date", cn_col]].merge(
            prod_data, left_on=["date", cn_col], right_on=["date", "cn"], how="left"
        )
        sp[f"{prod.lower()}_oi_chg"] = merged["oi_chg"].values
        sp[f"{prod.lower()}_oi_chg_pct"] = merged["oi_chg_pct"].values
        sp[f"{prod.lower()}_signal"] = merged["signal"].values
        sp[f"{prod.lower()}_price_chg_pct"] = merged["price_chg_pct"].values

    # --- Part A: DCP signal vs spread forward returns ---
    rprint("\n### DCP Liquid Month Signal vs Forward Spread Change\n")
    rprint("| Signal | Count | Avg 1D | Avg 2D | Avg 5D | Avg 15D | Hit 5D(+) |")
    rprint("|--------|-------|--------|--------|--------|---------|-----------|")

    for prod in ["dcp", "dby"]:
        if prod == "dby":
            rprint(f"\n### DBY Liquid Month Signal vs Forward Spread Change\n")
            rprint("| Signal | Count | Avg 1D | Avg 2D | Avg 5D | Avg 15D | Hit 5D(+) |")
            rprint("|--------|-------|--------|--------|--------|---------|-----------|")

        for sig in SIGNALS:
            mask = sp[f"{prod}_signal"] == sig
            sub = sp[mask]
            if len(sub) < 20:
                continue
            vals = {h: sub[f"spread_fwd_{h}d"].dropna() for h in HORIZONS}
            # for DCP: LBU means DCP going up -> spread should widen (DCP-DBY)
            # for DBY: LBU means DBY going up -> spread should narrow
            hit_5d = (vals[5] > 0).mean() * 100 if len(vals[5]) > 0 else 0
            rprint(
                f"| {sig:<8} | {len(sub):>5} "
                f"| {vals[1].mean():>+6.1f} | {vals[2].mean():>+6.1f} "
                f"| {vals[5].mean():>+6.1f} | {vals[15].mean():>+6.1f} "
                f"| {hit_5d:>8.1f}% |"
            )

    # --- Chart: signal returns by product ---
    fig, axes = plt.subplots(1, 2, figsize=(16, 6))
    for ax, prod, title in zip(axes, ["dcp", "dby"], ["DCP Signal", "DBY Signal"]):
        means = {}
        for sig in SIGNALS:
            mask = sp[f"{prod}_signal"] == sig
            means[sig] = [sp.loc[mask, f"spread_fwd_{h}d"].mean() for h in HORIZONS]
        x = np.arange(len(HORIZONS))
        w = 0.15
        for i, sig in enumerate(SIGNALS):
            ax.bar(x + i * w, means[sig], w, label=sig, color=SIG_COLORS[sig])
        ax.set_xticks(x + w * 2)
        ax.set_xticklabels([f"{h}D" for h in HORIZONS])
        ax.set_title(f"{title} -> Avg Spread Fwd Change (CNY)")
        ax.set_ylabel("Avg Spread Change")
        ax.axhline(0, color="black", lw=0.5)
        ax.legend(fontsize=8)
    plt.tight_layout()
    plt.savefig(DATA_DIR / "oi_dalian_signal_spread.png", dpi=150)
    plt.close()

    return sp


# ═══════════════════════════════════════════════════════════════════════════════
# MODULE 3: Joint OI Regime & Cross-Product Dynamics
# ═══════════════════════════════════════════════════════════════════════════════

def module_3(sp):
    rprint("\n" + "=" * 70)
    rprint("## Module 3: Joint OI Regime (DCP vs DBY)")
    rprint("=" * 70)

    # Classify joint regime
    def _joint(row):
        d = row["dcp_oi_chg"]
        b = row["dby_oi_chg"]
        if pd.isna(d) or pd.isna(b):
            return np.nan
        if d > 0 and b > 0:
            return "Both Building"
        if d > 0 and b <= 0:
            return "DCP Build / DBY Liq"
        if d <= 0 and b > 0:
            return "DCP Liq / DBY Build"
        return "Both Liquidating"

    sp["regime"] = sp.apply(_joint, axis=1)
    regimes = ["Both Building", "DCP Build / DBY Liq", "DCP Liq / DBY Build", "Both Liquidating"]
    regime_colors = {
        "Both Building": "#2ecc71", "DCP Build / DBY Liq": "#e74c3c",
        "DCP Liq / DBY Build": "#3498db", "Both Liquidating": "#e67e22",
    }

    # --- Regime summary ---
    rprint("\n### Joint OI Regime vs Spread Movement\n")
    rprint("| Regime | Count | Avg Spread Chg | Avg 1D | Avg 2D | Avg 5D | Avg 15D | Hit 5D(+) |")
    rprint("|--------|-------|----------------|--------|--------|--------|---------|-----------|")

    regime_fwd5 = []
    for reg in regimes:
        mask = sp["regime"] == reg
        sub = sp[mask]
        if len(sub) == 0:
            regime_fwd5.append(0)
            continue
        avg_chg = sub["spread_chg"].mean()
        avgs = {h: sub[f"spread_fwd_{h}d"].mean() for h in HORIZONS}
        hit5 = (sub["spread_fwd_5d"] > 0).mean() * 100
        regime_fwd5.append(avgs[5])
        rprint(
            f"| {reg:<25} | {len(sub):>5} | {avg_chg:>+14.1f} "
            f"| {avgs[1]:>+6.1f} | {avgs[2]:>+6.1f} "
            f"| {avgs[5]:>+6.1f} | {avgs[15]:>+6.1f} | {hit5:>8.1f}% |"
        )

    # chart: regime forward returns
    fig, ax = plt.subplots(figsize=(10, 5))
    bars = ax.bar(range(len(regimes)), regime_fwd5,
                  color=[regime_colors[r] for r in regimes])
    ax.set_xticks(range(len(regimes)))
    ax.set_xticklabels(regimes, fontsize=8, rotation=15)
    ax.set_ylabel("Avg 5D Forward Spread Change (CNY)")
    ax.set_title("DCP-DBY Spread: 5D Forward by Joint OI Regime")
    ax.axhline(0, color="black", lw=0.5)
    for bar, val in zip(bars, regime_fwd5):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height(),
                f"{val:+.1f}", ha="center", va="bottom", fontsize=9)
    plt.tight_layout()
    plt.savefig(DATA_DIR / "oi_dalian_regime_returns.png", dpi=150)
    plt.close()

    # --- Spread timeseries colored by regime ---
    recent = sp[sp["date"] >= "2023-01-01"].copy()
    fig, ax = plt.subplots(figsize=(16, 6))
    for reg in regimes:
        mask = recent["regime"] == reg
        ax.scatter(recent.loc[mask, "date"], recent.loc[mask, "spread"],
                   c=regime_colors[reg], s=3, alpha=0.6, label=reg)
    ax.plot(recent["date"], recent["spread"], color="black", lw=0.3, alpha=0.3)
    ax.set_ylabel("DCP - DBY (CNY/MT)")
    ax.set_title("DCP-DBY Spread with Joint OI Regime (2023+)")
    ax.legend(fontsize=8, markerscale=3)
    plt.tight_layout()
    plt.savefig(DATA_DIR / "oi_dalian_spread_regime_ts.png", dpi=150)
    plt.close()

    return sp


# ═══════════════════════════════════════════════════════════════════════════════
# MODULE 4: Cross-Signal Autocorrelation & Predictive Patterns
# ═══════════════════════════════════════════════════════════════════════════════

def module_4(sp):
    rprint("\n" + "=" * 70)
    rprint("## Module 4: Cross-Product Signal Patterns & Autocorrelation")
    rprint("=" * 70)

    # --- Part A: Does LBU in DCP predict spread widening at different lags? ---
    rprint("\n### Part A: DCP LBU -> Spread Response at Lags\n")
    rprint("Does a bullish buildup in DCP today explain spread widening 1-15 days later?\n")
    rprint("| Lag | Corr(DCP OI chg, Spread chg) | Corr(DBY OI chg, Spread chg) | Corr(DCP price chg, Spread chg) | Corr(DBY price chg, Spread chg) |")
    rprint("|-----|------------------------------|------------------------------|--------------------------------|--------------------------------|")

    valid = sp.dropna(subset=["dcp_oi_chg", "dby_oi_chg", "spread_chg"]).copy()
    lag_dcp_oi, lag_dby_oi, lag_dcp_p, lag_dby_p = [], [], [], []

    for lag in range(0, 16):
        shifted_spread = valid["spread_chg"].shift(-lag)
        dc = valid["dcp_oi_chg"].corr(shifted_spread)
        bc = valid["dby_oi_chg"].corr(shifted_spread)
        dp = valid["dcp_price_chg_pct"].corr(shifted_spread)
        bp = valid["dby_price_chg_pct"].corr(shifted_spread)
        lag_dcp_oi.append(dc)
        lag_dby_oi.append(bc)
        lag_dcp_p.append(dp)
        lag_dby_p.append(bp)
        if lag in [0, 1, 2, 5, 10, 15]:
            rprint(f"| {lag:>3} | {dc:>+28.4f} | {bc:>+28.4f} | {dp:>+30.4f} | {bp:>+30.4f} |")

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 5))
    lags = list(range(0, 16))
    ax1.plot(lags, lag_dcp_oi, "o-", label="DCP OI chg", color="#e74c3c")
    ax1.plot(lags, lag_dby_oi, "s-", label="DBY OI chg", color="#3498db")
    ax1.set_xlabel("Lag (days)")
    ax1.set_ylabel("Correlation")
    ax1.set_title("OI Change -> Future Spread Change")
    ax1.axhline(0, color="black", lw=0.5)
    ax1.legend()

    ax2.plot(lags, lag_dcp_p, "o-", label="DCP price chg%", color="#e74c3c")
    ax2.plot(lags, lag_dby_p, "s-", label="DBY price chg%", color="#3498db")
    ax2.set_xlabel("Lag (days)")
    ax2.set_ylabel("Correlation")
    ax2.set_title("Price Change -> Future Spread Change")
    ax2.axhline(0, color="black", lw=0.5)
    ax2.legend()

    plt.tight_layout()
    plt.savefig(DATA_DIR / "oi_dalian_lag_correlation.png", dpi=150)
    plt.close()

    # --- Part B: Signal sequences & streaks ---
    rprint("\n### Part B: Regime Streak Persistence\n")
    rprint("If a regime persists for N consecutive days, does the spread keep moving?\n")

    regimes = ["Both Building", "DCP Build / DBY Liq", "DCP Liq / DBY Build", "Both Liquidating"]
    regime_colors = {
        "Both Building": "#2ecc71", "DCP Build / DBY Liq": "#e74c3c",
        "DCP Liq / DBY Build": "#3498db", "Both Liquidating": "#e67e22",
    }

    sp_valid = sp.dropna(subset=["regime"]).copy()
    sp_valid["regime_grp"] = (sp_valid["regime"] != sp_valid["regime"].shift(1)).cumsum()
    sp_valid["streak_len"] = sp_valid.groupby("regime_grp").cumcount() + 1

    rprint("| Regime | Streak | Count | Avg Cum Spread Chg | Avg Fwd 5D |")
    rprint("|--------|--------|-------|--------------------|------------|")

    fig, ax = plt.subplots(figsize=(12, 6))
    for reg in regimes:
        xs, ys = [], []
        for slen in range(1, 11):
            mask = (sp_valid["regime"] == reg) & (sp_valid["streak_len"] == slen)
            sub = sp_valid[mask]
            if len(sub) < 15:
                continue

            # cumulative spread change from streak start
            cum_chgs = []
            for idx_row in sub.index:
                grp = sp_valid.loc[idx_row, "regime_grp"]
                grp_data = sp_valid[sp_valid["regime_grp"] == grp]
                cum_chg = grp_data.loc[:idx_row, "spread_chg"].sum()
                cum_chgs.append(cum_chg)
            avg_cum = np.mean(cum_chgs)
            fwd5 = sub["spread_fwd_5d"].mean()
            xs.append(slen)
            ys.append(avg_cum)

            rprint(
                f"| {reg:<25} | {slen:>6} | {len(sub):>5} "
                f"| {avg_cum:>+18.1f} | {fwd5:>+10.1f} |"
            )
        if xs:
            ax.plot(xs, ys, "o-", label=reg, color=regime_colors[reg])

    ax.set_xlabel("Consecutive Days in Regime")
    ax.set_ylabel("Avg Cumulative Spread Change (CNY)")
    ax.set_title("DCP-DBY: Cumulative Spread Change by Regime Streak")
    ax.axhline(0, color="black", lw=0.5)
    ax.legend(fontsize=8)
    plt.tight_layout()
    plt.savefig(DATA_DIR / "oi_dalian_streak_cumulative.png", dpi=150)
    plt.close()

    # --- Part C: Cross-signal sequences ---
    rprint("\n### Part C: Cross-Signal Sequences (DCP signal -> DBY signal -> spread)\n")
    rprint("Does DCP LBU today followed by DBY SBU tomorrow predict spread widening?\n")

    sp_valid["dcp_signal_lag1"] = sp_valid["dcp_signal"].shift(1)
    sp_valid["dby_signal_lag1"] = sp_valid["dby_signal"].shift(1)

    rprint("| DCP(t-1) | DBY(t-1) | DCP(t) | DBY(t) | N | Avg 2D Spread | Avg 5D Spread | Hit 5D(+) |")
    rprint("|----------|----------|--------|--------|---|---------------|---------------|-----------|")

    # focus on the interesting combos
    combos = [
        ("LBU", "SBU"),   # DCP bull, DBY bear -> spread widens?
        ("SBU", "LBU"),   # DCP bear, DBY bull -> spread narrows?
        ("LBU", "LBU"),   # both bull -> who wins?
        ("SBU", "SBU"),   # both bear -> who falls more?
        ("SC", "LBU"),    # DCP short covering, DBY long buildup
        ("LBU", "LU"),    # DCP building, DBY unwinding
    ]

    rows_out = []
    for dcp_sig, dby_sig in combos:
        # today's combo
        mask_today = (sp_valid["dcp_signal"] == dcp_sig) & (sp_valid["dby_signal"] == dby_sig)
        sub = sp_valid[mask_today]
        if len(sub) >= 20:
            a2 = sub["spread_fwd_2d"].mean()
            a5 = sub["spread_fwd_5d"].mean()
            h5 = (sub["spread_fwd_5d"] > 0).mean() * 100
            rows_out.append((dcp_sig, dby_sig, len(sub), a2, a5, h5))
            rprint(
                f"| {'':>8} | {'':>8} | {dcp_sig:<6} | {dby_sig:<6} "
                f"| {len(sub):>3} | {a2:>+13.1f} | {a5:>+13.1f} | {h5:>8.1f}% |"
            )

    # also: 2-day sequence (yesterday DCP LBU, today DCP LBU + DBY SBU)
    rprint("\n**2-Day DCP Streak + DBY Counter-Signal:**\n")
    rprint("| DCP(t-1) | DCP(t) | DBY(t) | N | Avg 5D Spread | Hit 5D(+) |")
    rprint("|----------|--------|--------|---|---------------|-----------|")

    for dcp_prev, dcp_now, dby_now in [
        ("LBU", "LBU", "SBU"),
        ("LBU", "LBU", "LU"),
        ("SBU", "SBU", "LBU"),
        ("SBU", "SBU", "SC"),
        ("LBU", "LBU", "LBU"),
        ("SBU", "SBU", "SBU"),
    ]:
        mask = (
            (sp_valid["dcp_signal_lag1"] == dcp_prev) &
            (sp_valid["dcp_signal"] == dcp_now) &
            (sp_valid["dby_signal"] == dby_now)
        )
        sub = sp_valid[mask]
        if len(sub) >= 15:
            a5 = sub["spread_fwd_5d"].mean()
            h5 = (sub["spread_fwd_5d"] > 0).mean() * 100
            rprint(
                f"| {dcp_prev:<8} | {dcp_now:<6} | {dby_now:<6} "
                f"| {len(sub):>3} | {a5:>+13.1f} | {h5:>8.1f}% |"
            )


# ═══════════════════════════════════════════════════════════════════════════════
# MODULE 5: Curve OI & Absolute Price Level Analysis
# ═══════════════════════════════════════════════════════════════════════════════

def module_5(df, sp):
    rprint("\n" + "=" * 70)
    rprint("## Module 5: Curve OI, Absolute Price Levels & Spread")
    rprint("=" * 70)

    dcp_curve = get_curve_oi(df, "DCP")
    dby_curve = get_curve_oi(df, "DBY")

    merged = sp.merge(dcp_curve, on="date", how="left").merge(dby_curve, on="date", how="left")
    merged = merged.sort_values("date")

    # curve OI changes
    merged["dcp_curve_oi_chg"] = merged["dcp_curve_oi"].diff()
    merged["dby_curve_oi_chg"] = merged["dby_curve_oi"].diff()
    merged["dcp_curve_oi_chg_pct"] = merged["dcp_curve_oi"].pct_change() * 100
    merged["dby_curve_oi_chg_pct"] = merged["dby_curve_oi"].pct_change() * 100

    # OI ratio: DCP curve / DBY curve
    merged["oi_ratio"] = merged["dcp_curve_oi"] / merged["dby_curve_oi"]
    merged["oi_ratio_chg"] = merged["oi_ratio"].diff()

    # --- Curve OI vs spread ---
    rprint("\n### Curve OI Changes vs Spread Forward Returns\n")

    # quintile binning: DCP curve OI change
    for label, col in [("DCP Curve OI Chg%", "dcp_curve_oi_chg_pct"),
                        ("DBY Curve OI Chg%", "dby_curve_oi_chg_pct"),
                        ("OI Ratio Change", "oi_ratio_chg")]:
        valid = merged.dropna(subset=[col, "spread_fwd_5d"])
        if len(valid) < 50:
            continue
        try:
            valid["q"] = pd.qcut(valid[col], 5, labels=["Q1(low)", "Q2", "Q3", "Q4", "Q5(high)"], duplicates="drop")
        except ValueError:
            continue

        rprint(f"\n**{label} Quintiles vs 5D Forward Spread Change:**\n")
        rprint("| Quintile | Range | Avg 1D | Avg 5D | Avg 15D | Hit 5D(+) | N |")
        rprint("|----------|-------|--------|--------|---------|-----------|---|")
        for q in valid["q"].cat.categories:
            qd = valid[valid["q"] == q]
            lo, hi = qd[col].min(), qd[col].max()
            a1 = qd["spread_fwd_1d"].mean()
            a5 = qd["spread_fwd_5d"].mean()
            a15 = qd["spread_fwd_15d"].mean()
            h5 = (qd["spread_fwd_5d"] > 0).mean() * 100
            rprint(
                f"| {str(q):<8} | {lo:>+6.2f} to {hi:>+6.2f} "
                f"| {a1:>+6.1f} | {a5:>+6.1f} | {a15:>+7.1f} | {h5:>8.1f}% | {len(qd)} |"
            )

    # --- Chart: curve OI time series + spread ---
    fig, axes = plt.subplots(3, 1, figsize=(16, 12), sharex=True)

    ax1 = axes[0]
    ax1.plot(merged["date"], merged["dcp_curve_oi"] / 1e6, label="DCP Curve OI", color="#e74c3c")
    ax1.plot(merged["date"], merged["dby_curve_oi"] / 1e6, label="DBY Curve OI", color="#3498db")
    ax1.set_ylabel("Curve OI (millions)")
    ax1.set_title("Total Curve OI: DCP vs DBY")
    ax1.legend()

    ax2 = axes[1]
    ax2.plot(merged["date"], merged["oi_ratio"], color="purple", lw=0.8)
    ax2.set_ylabel("DCP/DBY OI Ratio")
    ax2.set_title("Curve OI Ratio (DCP / DBY)")

    ax3 = axes[2]
    ax3.plot(merged["date"], merged["spread"], color="navy", lw=0.8)
    ax3.set_ylabel("DCP - DBY (CNY)")
    ax3.set_title("DCP-DBY Price Spread")
    ax3.axhline(0, color="black", lw=0.5)

    plt.tight_layout()
    plt.savefig(DATA_DIR / "oi_dalian_curve_oi_spread.png", dpi=150)
    plt.close()

    # --- Absolute price level bins ---
    rprint("\n### Absolute Price Level vs Spread Behavior\n")

    # bin DCP price into terciles
    valid = merged.dropna(subset=["dcp_price", "spread_fwd_5d"]).copy()
    valid["dcp_tercile"] = pd.qcut(valid["dcp_price"], 3, labels=["Low", "Mid", "High"])

    rprint("**DCP Price Level Terciles:**\n")
    rprint("| DCP Level | Range | Avg Spread | Avg 5D Fwd | Spread Std | N |")
    rprint("|-----------|-------|------------|------------|------------|---|")
    for t in ["Low", "Mid", "High"]:
        td = valid[valid["dcp_tercile"] == t]
        rprint(
            f"| {t:<9} | {td['dcp_price'].min():.0f}-{td['dcp_price'].max():.0f} "
            f"| {td['spread'].mean():>+10.1f} | {td['spread_fwd_5d'].mean():>+10.1f} "
            f"| {td['spread'].std():>10.1f} | {len(td)} |"
        )

    # rolling correlation: OI ratio vs spread
    corr_60 = merged["oi_ratio"].rolling(60).corr(merged["spread"])
    rprint(f"\nRolling 60D correlation (OI Ratio vs Spread): avg = {corr_60.mean():.3f}")

    # scatter: OI ratio vs spread
    fig, ax = plt.subplots(figsize=(8, 6))
    valid2 = merged.dropna(subset=["oi_ratio", "spread"])
    ax.scatter(valid2["oi_ratio"], valid2["spread"], s=2, alpha=0.2, c="steelblue")
    ax.set_xlabel("DCP/DBY Curve OI Ratio")
    ax.set_ylabel("DCP-DBY Spread (CNY)")
    ax.set_title("OI Ratio vs Price Spread")
    # regression
    slope, intercept, r, p, se = stats.linregress(valid2["oi_ratio"], valid2["spread"])
    x_line = np.linspace(valid2["oi_ratio"].min(), valid2["oi_ratio"].max(), 50)
    ax.plot(x_line, slope * x_line + intercept, "r-", lw=2, label=f"R={r:.3f}, p={p:.1e}")
    ax.legend()
    plt.tight_layout()
    plt.savefig(DATA_DIR / "oi_dalian_oi_ratio_vs_spread.png", dpi=150)
    plt.close()


# ═══════════════════════════════════════════════════════════════════════════════
# REPORT
# ═══════════════════════════════════════════════════════════════════════════════

def save_report():
    header = [
        f"# OI Analysis: Dalian DCP (Palm) vs DBY (Soybean Oil)",
        f"**Generated:** {date.today().isoformat()}",
        f"**Data Range:** 2015-01-01 to 2026-03-26",
        f"**Contracts:** DCP c1-c8, DBY c1-c8",
        f"**Spread:** DCP liquid month - DBY liquid month (CNY/MT)",
        "",
    ]
    path = DATA_DIR / "OI Analysis (DCP vs DBY Dalian).md"
    with open(path, "w", encoding="utf-8") as f:
        for line in header + REPORT:
            f.write(line + "\n")
    print(f"\nReport saved to: {path}")


def main():
    print("Loading DCP/DBY data...")
    df = load_data()
    print(f"  {len(df):,} rows ({df['RIC'].nunique()} RICs)")

    spread = module_1(df)
    sp = module_2(df, spread)
    sp = module_3(sp)
    module_4(sp)
    module_5(df, sp)
    save_report()
    print("\nDone. All charts saved to data/oi/")


if __name__ == "__main__":
    main()
