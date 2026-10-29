"""
oi_analysis_report.py
=====================
Comprehensive OI analysis for BMD (FCPO) and Dalian (DCP) palm oil futures.

Modules:
  1. Signal Predictive Power + Sequence Patterns
  2. Total Curve OI vs Price Pressure
  3. Near-Expiry OI Migration & Calendar Spreads (FCPO)
  4. DCP-BMD Spread Analysis (joint OI regimes, streaks, lags)

Outputs: PNGs + markdown report to data/oi/
"""

import warnings
from pathlib import Path
from datetime import date
from itertools import product as iterproduct
from collections import Counter

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from scipy import stats

warnings.filterwarnings("ignore", category=FutureWarning)

ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / "data" / "oi"
RAW_FILE = DATA_DIR / "fcpo_dcp_oi_raw.csv"

NOISE = 0.1  # price change % threshold for Neutral
SIGNALS = ["LBU", "SBU", "SC", "LU", "Neutral"]
SIG_COLORS = {
    "LBU": "#2ecc71", "SBU": "#e74c3c",
    "SC": "#3498db", "LU": "#e67e22", "Neutral": "#95a5a6",
}
HORIZONS = [1, 5, 10, 20]

REPORT_LINES = []  # accumulate markdown lines


def rprint(line=""):
    """Print to console and accumulate for report."""
    print(line)
    REPORT_LINES.append(line)


# ═══════════════════════════════════════════════════════════════════════════════
# DATA PREPARATION
# ═══════════════════════════════════════════════════════════════════════════════

def load_and_prepare():
    """Load raw CSV, resolve prices, apply FX, classify signals."""
    df = pd.read_csv(RAW_FILE)
    df["date"] = pd.to_datetime(df["Trade Date"])
    df["price"] = df["Last"].fillna(df["Settlement Price"])
    df["oi"] = pd.to_numeric(df["Open Interest"], errors="coerce")
    df["volume"] = pd.to_numeric(df["Volume"], errors="coerce")
    df = df[["date", "RIC", "price", "oi", "volume"]].dropna(subset=["price"])

    # --- FX rates (from main raw CSV which has Bid/Ask) ---
    main_raw = ROOT / "data" / "raw" / "all_instruments_raw.csv"
    fx_df = pd.read_csv(main_raw)
    fx_df["date"] = pd.to_datetime(fx_df["Trade Date"])
    fx_df = fx_df[fx_df["RIC"].isin(["MYR=", "CNY="])].copy()
    fx_df["mid"] = (pd.to_numeric(fx_df["Bid"], errors="coerce") +
                    pd.to_numeric(fx_df["Ask"], errors="coerce")) / 2

    fx_myr = fx_df[fx_df["RIC"] == "MYR="][["date", "mid"]].rename(columns={"mid": "myr"})
    fx_cny = fx_df[fx_df["RIC"] == "CNY="][["date", "mid"]].rename(columns={"mid": "cny"})

    # build daily FX table, forward-fill
    all_dates = pd.DataFrame({"date": pd.date_range(df["date"].min(), df["date"].max())})
    fx = all_dates.merge(fx_myr, on="date", how="left").merge(fx_cny, on="date", how="left")
    fx = fx.ffill()

    # --- Split futures ---
    fcpo_rics = [f"FCPOc{i}" for i in range(1, 9)]
    dcp_rics = [f"DCPc{i}" for i in range(1, 9)]

    futures = df[df["RIC"].isin(fcpo_rics + dcp_rics)].copy()
    futures = futures.merge(fx, on="date", how="left")

    # convert to USD/MT
    is_fcpo = futures["RIC"].str.startswith("FCPO")
    is_dcp = futures["RIC"].str.startswith("DCP")
    futures["price_usd"] = np.nan
    futures.loc[is_fcpo, "price_usd"] = futures.loc[is_fcpo, "price"] / futures.loc[is_fcpo, "myr"]
    futures.loc[is_dcp, "price_usd"] = futures.loc[is_dcp, "price"] / futures.loc[is_dcp, "cny"]

    # keep local price too (for FCPO calendar spreads in MYR)
    futures["price_local"] = futures["price"]

    # --- Day-on-day changes & signal classification ---
    futures = futures.sort_values(["RIC", "date"]).reset_index(drop=True)
    for col in ["price_local", "oi"]:
        futures[f"{col}_prev"] = futures.groupby("RIC")[col].shift(1)
    futures["price_chg_pct"] = (
        (futures["price_local"] - futures["price_local_prev"]) / futures["price_local_prev"] * 100
    )
    futures["oi_chg"] = futures["oi"] - futures["oi_prev"]
    futures["oi_chg_pct"] = futures["oi_chg"] / futures["oi_prev"] * 100

    futures["signal"] = futures.apply(_classify, axis=1)
    futures["product"] = futures["RIC"].str.extract(r"^([A-Z]+)")

    # contract number
    futures["cn"] = futures["RIC"].str.extract(r"c(\d)").astype(int)

    return futures, fx


def _classify(row):
    p = row["price_chg_pct"]
    o = row["oi_chg_pct"]
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


# ═══════════════════════════════════════════════════════════════════════════════
# MODULE 1: Signal Predictive Power + Sequence Patterns
# ═══════════════════════════════════════════════════════════════════════════════

def module_1(futures):
    rprint("\n" + "=" * 70)
    rprint("## Module 1: Signal Predictive Power + Sequence Patterns")
    rprint("=" * 70)

    # --- Part A: single-day signal forward returns ---
    rprint("\n### Part A: Single-Day Signal Forward Returns\n")

    for prod_name in ["FCPO", "DCP"]:
        rprint(f"\n#### {prod_name}\n")
        sub = futures[futures["product"] == prod_name].copy()

        # compute forward returns per RIC
        for h in HORIZONS:
            sub[f"fwd_{h}d"] = sub.groupby("RIC")["price_local"].transform(
                lambda s: s.shift(-h) / s - 1
            ) * 100

        rows = []
        for sig in SIGNALS:
            mask = sub["signal"] == sig
            if mask.sum() < 10:
                continue
            row = {"signal": sig, "count": int(mask.sum())}
            for h in HORIZONS:
                vals = sub.loc[mask, f"fwd_{h}d"].dropna()
                row[f"avg_{h}d"] = vals.mean()
                row[f"med_{h}d"] = vals.median()
                row[f"hit_{h}d"] = (vals > 0).mean() * 100
                if len(vals) > 2:
                    t, p = stats.ttest_1samp(vals, 0)
                    row[f"pval_{h}d"] = p
                else:
                    row[f"pval_{h}d"] = np.nan
            rows.append(row)

        tbl = pd.DataFrame(rows)
        if tbl.empty:
            rprint("No data.")
            continue

        # print summary
        rprint(f"| Signal | Count | Avg 1D% | Hit 1D% | Avg 5D% | Hit 5D% | Avg 10D% | Hit 10D% | Avg 20D% | Hit 20D% |")
        rprint(f"|--------|-------|---------|---------|---------|---------|----------|----------|----------|----------|")
        for _, r in tbl.iterrows():
            rprint(
                f"| {r['signal']:<6} | {r['count']:>5} "
                f"| {r.get('avg_1d', 0):>+7.3f} | {r.get('hit_1d', 0):>6.1f}% "
                f"| {r.get('avg_5d', 0):>+7.3f} | {r.get('hit_5d', 0):>6.1f}% "
                f"| {r.get('avg_10d', 0):>+7.3f} | {r.get('hit_10d', 0):>6.1f}% "
                f"| {r.get('avg_20d', 0):>+7.3f} | {r.get('hit_20d', 0):>6.1f}% |"
            )

    # --- Chart: signal returns ---
    fig, axes = plt.subplots(1, 2, figsize=(16, 6), sharey=True)
    for ax, prod_name in zip(axes, ["FCPO", "DCP"]):
        sub = futures[futures["product"] == prod_name].copy()
        for h in HORIZONS:
            sub[f"fwd_{h}d"] = sub.groupby("RIC")["price_local"].transform(
                lambda s: s.shift(-h) / s - 1
            ) * 100
        means = {}
        for sig in SIGNALS:
            mask = sub["signal"] == sig
            means[sig] = [sub.loc[mask, f"fwd_{h}d"].mean() for h in HORIZONS]
        x = np.arange(len(HORIZONS))
        w = 0.15
        for i, sig in enumerate(SIGNALS):
            ax.bar(x + i * w, means[sig], w, label=sig, color=SIG_COLORS[sig])
        ax.set_xticks(x + w * 2)
        ax.set_xticklabels([f"{h}D" for h in HORIZONS])
        ax.set_title(f"{prod_name} - Avg Forward Return by Signal")
        ax.set_ylabel("Avg Return (%)")
        ax.axhline(0, color="black", lw=0.5)
        ax.legend(fontsize=8)
    plt.tight_layout()
    plt.savefig(DATA_DIR / "oi_m1_signal_returns.png", dpi=150)
    plt.close()

    # --- Chart: hit rate heatmap ---
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))
    for ax, prod_name in zip(axes, ["FCPO", "DCP"]):
        sub = futures[futures["product"] == prod_name].copy()
        for h in HORIZONS:
            sub[f"fwd_{h}d"] = sub.groupby("RIC")["price_local"].transform(
                lambda s: s.shift(-h) / s - 1
            ) * 100
        mat = []
        for sig in SIGNALS:
            mask = sub["signal"] == sig
            row = []
            for h in HORIZONS:
                vals = sub.loc[mask, f"fwd_{h}d"].dropna()
                row.append((vals > 0).mean() * 100 if len(vals) > 0 else 50)
            mat.append(row)
        mat = np.array(mat)
        im = ax.imshow(mat, cmap="RdYlGn", vmin=40, vmax=60, aspect="auto")
        ax.set_xticks(range(len(HORIZONS)))
        ax.set_xticklabels([f"{h}D" for h in HORIZONS])
        ax.set_yticks(range(len(SIGNALS)))
        ax.set_yticklabels(SIGNALS)
        for i in range(len(SIGNALS)):
            for j in range(len(HORIZONS)):
                ax.text(j, i, f"{mat[i, j]:.1f}%", ha="center", va="center", fontsize=9)
        ax.set_title(f"{prod_name} - Hit Rate (%)")
        plt.colorbar(im, ax=ax, shrink=0.8)
    plt.tight_layout()
    plt.savefig(DATA_DIR / "oi_m1_hit_rates.png", dpi=150)
    plt.close()

    # --- Part B: Sequence patterns ---
    rprint("\n### Part B: Signal Sequence Patterns\n")

    # Transition matrix (use c3 as representative)
    for prod_name in ["FCPO", "DCP"]:
        ric = f"{prod_name}c3"
        sub = futures[(futures["RIC"] == ric) & futures["signal"].notna()].copy()
        sub = sub.sort_values("date")
        sub["next_signal"] = sub["signal"].shift(-1)
        valid = sub.dropna(subset=["next_signal"])

        rprint(f"\n#### {prod_name} c3 Transition Matrix (P(next | current))\n")
        sigs_present = [s for s in SIGNALS if s in valid["signal"].values]
        rprint("| Current \\ Next | " + " | ".join(sigs_present) + " |")
        rprint("|" + "---|" * (len(sigs_present) + 1))

        trans_mat = np.zeros((len(sigs_present), len(sigs_present)))
        for i, s_from in enumerate(sigs_present):
            row_data = valid[valid["signal"] == s_from]
            total = len(row_data)
            vals = []
            for j, s_to in enumerate(sigs_present):
                cnt = (row_data["next_signal"] == s_to).sum()
                pct = cnt / total * 100 if total > 0 else 0
                trans_mat[i, j] = pct
                vals.append(f"{pct:5.1f}%")
            rprint(f"| {s_from:<14} | " + " | ".join(vals) + " |")

    # Transition matrix chart
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))
    for ax, prod_name in zip(axes, ["FCPO", "DCP"]):
        ric = f"{prod_name}c3"
        sub = futures[(futures["RIC"] == ric) & futures["signal"].notna()].sort_values("date")
        sub["next_signal"] = sub["signal"].shift(-1)
        valid = sub.dropna(subset=["next_signal"])
        sigs = [s for s in SIGNALS if s in valid["signal"].values]
        mat = np.zeros((len(sigs), len(sigs)))
        for i, sf in enumerate(sigs):
            rd = valid[valid["signal"] == sf]
            t = len(rd)
            for j, st in enumerate(sigs):
                mat[i, j] = (rd["next_signal"] == st).sum() / t * 100 if t > 0 else 0
        im = ax.imshow(mat, cmap="YlOrRd", aspect="auto")
        ax.set_xticks(range(len(sigs)))
        ax.set_xticklabels(sigs, fontsize=8, rotation=45)
        ax.set_yticks(range(len(sigs)))
        ax.set_yticklabels(sigs, fontsize=8)
        for i in range(len(sigs)):
            for j in range(len(sigs)):
                ax.text(j, i, f"{mat[i, j]:.0f}%", ha="center", va="center", fontsize=8)
        ax.set_title(f"{prod_name} c3 Transition P(%)")
        ax.set_xlabel("Next Signal")
        ax.set_ylabel("Current Signal")
        plt.colorbar(im, ax=ax, shrink=0.8)
    plt.tight_layout()
    plt.savefig(DATA_DIR / "oi_m1_transition_matrix.png", dpi=150)
    plt.close()

    # Streak analysis: avg next-day return by streak length
    rprint("\n#### Streak Analysis: Avg Next-Day Return by Consecutive Signal Days\n")

    fig, axes = plt.subplots(1, 2, figsize=(14, 5))
    for ax, prod_name in zip(axes, ["FCPO", "DCP"]):
        ric = f"{prod_name}c3"
        sub = futures[(futures["RIC"] == ric) & futures["signal"].notna()].sort_values("date").copy()
        sub["fwd_1d"] = sub["price_local"].shift(-1) / sub["price_local"] - 1
        sub["fwd_5d"] = sub["price_local"].shift(-5) / sub["price_local"] - 1

        # compute streak lengths
        sub["streak_grp"] = (sub["signal"] != sub["signal"].shift(1)).cumsum()
        sub["streak_len"] = sub.groupby("streak_grp").cumcount() + 1

        rprint(f"\n**{prod_name} c3**\n")
        rprint("| Signal | Streak | Count | Avg 1D% | Avg 5D% | Hit 1D% |")
        rprint("|--------|--------|-------|---------|---------|---------|")

        for sig in ["LBU", "SBU", "SC", "LU"]:
            xs, ys_1d = [], []
            for slen in range(1, 8):
                mask = (sub["signal"] == sig) & (sub["streak_len"] == slen)
                vals = sub.loc[mask, "fwd_1d"].dropna() * 100
                vals5 = sub.loc[mask, "fwd_5d"].dropna() * 100
                if len(vals) >= 15:
                    xs.append(slen)
                    ys_1d.append(vals.mean())
                    hit = (vals > 0).mean() * 100
                    rprint(
                        f"| {sig:<6} | {slen:>6} | {len(vals):>5} "
                        f"| {vals.mean():>+7.3f} | {vals5.mean():>+7.3f} | {hit:>6.1f}% |"
                    )
            if xs:
                ax.plot(xs, ys_1d, marker="o", label=sig, color=SIG_COLORS[sig])

        ax.set_xlabel("Consecutive Days of Same Signal")
        ax.set_ylabel("Avg Next-Day Return (%)")
        ax.set_title(f"{prod_name} c3 - Streak vs Next-Day Return")
        ax.axhline(0, color="black", lw=0.5)
        ax.legend(fontsize=8)

    plt.tight_layout()
    plt.savefig(DATA_DIR / "oi_m1_streak_returns.png", dpi=150)
    plt.close()

    # Top sequence patterns (3-day and 4-day)
    rprint("\n#### Top Signal Sequences (3-day and 4-day patterns, c3)\n")
    for prod_name in ["FCPO", "DCP"]:
        ric = f"{prod_name}c3"
        sub = futures[(futures["RIC"] == ric) & futures["signal"].notna()].sort_values("date").copy()
        sub["fwd_1d"] = (sub["price_local"].shift(-1) / sub["price_local"] - 1) * 100
        sub["fwd_5d"] = (sub["price_local"].shift(-5) / sub["price_local"] - 1) * 100
        sigs_arr = sub["signal"].values
        fwd1 = sub["fwd_1d"].values
        fwd5 = sub["fwd_5d"].values

        rprint(f"\n**{prod_name} c3 - Top 10 Sequences by Abs(Avg 5D Return)**\n")
        rprint("| Pattern | N | Avg 1D% | Avg 5D% | Hit 5D% |")
        rprint("|---------|---|---------|---------|---------|")

        all_pats = []
        for window in [3, 4]:
            for i in range(len(sigs_arr) - window):
                pat = tuple(sigs_arr[i:i + window])
                if any(pd.isna(s) for s in pat):
                    continue
                nxt_1d = fwd1[i + window - 1]
                nxt_5d = fwd5[i + window - 1]
                if not np.isnan(nxt_5d):
                    all_pats.append((pat, nxt_1d, nxt_5d))

        # aggregate
        pat_stats = {}
        for pat, r1, r5 in all_pats:
            key = " > ".join(pat)
            if key not in pat_stats:
                pat_stats[key] = {"r1": [], "r5": []}
            pat_stats[key]["r1"].append(r1)
            pat_stats[key]["r5"].append(r5)

        rows = []
        for key, v in pat_stats.items():
            if len(v["r5"]) >= 30:
                rows.append({
                    "pattern": key,
                    "n": len(v["r5"]),
                    "avg_1d": np.mean(v["r1"]),
                    "avg_5d": np.mean(v["r5"]),
                    "hit_5d": np.mean([x > 0 for x in v["r5"]]) * 100,
                })
        if rows:
            pat_df = pd.DataFrame(rows).sort_values("avg_5d", key=abs, ascending=False).head(10)
            for _, r in pat_df.iterrows():
                rprint(
                    f"| {r['pattern']:<30} | {r['n']:>3} "
                    f"| {r['avg_1d']:>+7.3f} | {r['avg_5d']:>+7.3f} | {r['hit_5d']:>5.1f}% |"
                )


# ═══════════════════════════════════════════════════════════════════════════════
# MODULE 2: Total Curve OI vs Price Pressure
# ═══════════════════════════════════════════════════════════════════════════════

def module_2(futures):
    rprint("\n" + "=" * 70)
    rprint("## Module 2: Total Curve OI vs Front-Month Price Pressure")
    rprint("=" * 70)

    fig_ts, axes_ts = plt.subplots(2, 1, figsize=(16, 10), sharex=True)
    fig_q, axes_q = plt.subplots(1, 2, figsize=(14, 5))

    for idx, prod_name in enumerate(["FCPO", "DCP"]):
        sub = futures[futures["product"] == prod_name].copy()

        # total curve OI per day
        curve_oi = sub.groupby("date")["oi"].sum().reset_index().rename(columns={"oi": "total_oi"})
        curve_oi = curve_oi.sort_values("date")
        curve_oi["total_oi_chg"] = curve_oi["total_oi"].diff()
        curve_oi["total_oi_chg_pct"] = curve_oi["total_oi"].pct_change() * 100

        # front month price
        c1 = sub[sub["cn"] == 1][["date", "price_local"]].rename(columns={"price_local": "c1_price"})
        merged = curve_oi.merge(c1, on="date", how="inner")
        merged = merged.sort_values("date")

        # forward returns on c1
        for h in [1, 5, 10]:
            merged[f"fwd_{h}d"] = (merged["c1_price"].shift(-h) / merged["c1_price"] - 1) * 100

        # --- Time series chart ---
        ax = axes_ts[idx]
        ax2 = ax.twinx()
        ax.bar(merged["date"], merged["total_oi_chg"], color="steelblue", alpha=0.4, width=1, label="Total OI Change")
        ax2.plot(merged["date"], merged["c1_price"], color="darkorange", lw=1, label="c1 Price")
        ax.set_ylabel("Total Curve OI Change", color="steelblue")
        ax2.set_ylabel("c1 Price (local)", color="darkorange")
        ax.set_title(f"{prod_name} - Total Curve OI Change vs c1 Price")
        lines1, labels1 = ax.get_legend_handles_labels()
        lines2, labels2 = ax2.get_legend_handles_labels()
        ax.legend(lines1 + lines2, labels1 + labels2, loc="upper left", fontsize=8)

        # --- Quintile analysis ---
        valid = merged.dropna(subset=["total_oi_chg_pct", "fwd_5d"])
        valid["quintile"] = pd.qcut(valid["total_oi_chg_pct"], 5, labels=["Q1\n(big drop)", "Q2", "Q3", "Q4", "Q5\n(big rise)"])

        rprint(f"\n### {prod_name}: Total Curve OI Change Quintiles vs c1 Forward Returns\n")
        rprint("| Quintile | OI Chg% Range | Avg 1D% | Avg 5D% | Avg 10D% | Hit 5D% | N |")
        rprint("|----------|---------------|---------|---------|----------|---------|---|")

        q_means_5d = []
        for q in valid["quintile"].cat.categories:
            qd = valid[valid["quintile"] == q]
            oi_lo = qd["total_oi_chg_pct"].min()
            oi_hi = qd["total_oi_chg_pct"].max()
            a1 = qd["fwd_1d"].mean()
            a5 = qd["fwd_5d"].mean()
            a10 = qd["fwd_10d"].mean()
            h5 = (qd["fwd_5d"] > 0).mean() * 100
            q_means_5d.append(a5)
            rprint(
                f"| {str(q):<8} | {oi_lo:>+5.1f} to {oi_hi:>+5.1f} "
                f"| {a1:>+7.3f} | {a5:>+7.3f} | {a10:>+7.3f} | {h5:>5.1f}% | {len(qd)} |"
            )

        ax_q = axes_q[idx]
        colors = ["#e74c3c", "#e67e22", "#95a5a6", "#3498db", "#2ecc71"]
        ax_q.bar(range(5), q_means_5d, color=colors)
        ax_q.set_xticks(range(5))
        ax_q.set_xticklabels(["Q1\n(big drop)", "Q2", "Q3", "Q4", "Q5\n(big rise)"], fontsize=8)
        ax_q.set_ylabel("Avg 5D Forward Return (%)")
        ax_q.set_title(f"{prod_name} - Curve OI Quintile vs 5D Return")
        ax_q.axhline(0, color="black", lw=0.5)

        # rolling correlation
        corr_20 = merged["total_oi_chg_pct"].rolling(60).corr(merged["fwd_5d"])
        avg_corr = corr_20.mean()
        rprint(f"\nRolling 60D correlation (OI chg% vs 5D fwd return): avg = {avg_corr:.3f}")

    fig_ts.tight_layout()
    fig_ts.savefig(DATA_DIR / "oi_m2_curve_oi_vs_price.png", dpi=150)
    plt.close(fig_ts)

    fig_q.tight_layout()
    fig_q.savefig(DATA_DIR / "oi_m2_quintile_returns.png", dpi=150)
    plt.close(fig_q)


# ═══════════════════════════════════════════════════════════════════════════════
# MODULE 3: Near-Expiry OI Migration & Calendar Spreads (FCPO)
# ═══════════════════════════════════════════════════════════════════════════════

def module_3(futures):
    rprint("\n" + "=" * 70)
    rprint("## Module 3: Near-Expiry OI Migration & Calendar Spreads (FCPO)")
    rprint("=" * 70)

    fcpo = futures[futures["product"] == "FCPO"].copy()

    # OI share per contract
    total_oi = fcpo.groupby("date")["oi"].sum().rename("total_oi")
    fcpo = fcpo.merge(total_oi, on="date", how="left")
    fcpo["oi_share"] = fcpo["oi"] / fcpo["total_oi"]

    # Calendar spread: c1 - c2, c1 - c3
    pivot_price = fcpo.pivot_table(index="date", columns="cn", values="price_local")
    pivot_price.columns = [f"c{c}" for c in pivot_price.columns]
    pivot_price["spread_c1c2"] = pivot_price["c1"] - pivot_price["c2"]
    pivot_price["spread_c1c3"] = pivot_price["c1"] - pivot_price["c3"]
    pivot_price = pivot_price.sort_index()

    # OI share pivot
    pivot_oi = fcpo.pivot_table(index="date", columns="cn", values="oi_share")
    pivot_oi.columns = [f"oi_share_c{c}" for c in pivot_oi.columns]

    combined = pivot_price.join(pivot_oi, how="inner")

    # FCPO roll dates: 15th of each month
    roll_dates = []
    for y in range(2015, 2027):
        for m in range(1, 13):
            rd = pd.Timestamp(y, m, 15)
            # find nearest trading day
            mask = combined.index >= rd - pd.Timedelta(days=5)
            mask &= combined.index <= rd + pd.Timedelta(days=5)
            candidates = combined.index[mask]
            if len(candidates) > 0:
                nearest = candidates[candidates >= rd]
                if len(nearest) > 0:
                    roll_dates.append(nearest[0])
                else:
                    roll_dates.append(candidates[-1])
    roll_dates = sorted(set(roll_dates))

    # Pre-roll windows: 30 days before each roll
    rprint("\n### Pre-Roll Analysis (30 days before roll)\n")
    rprint("| Roll Date | c1 OI Share Start | c1 OI Share End | OI Drop | Spread c1c3 Chg | Type |")
    rprint("|-----------|-------------------|-----------------|---------|-----------------|------|")

    episodes = []
    for rd in roll_dates:
        start = rd - pd.Timedelta(days=45)  # ~30 trading days
        window = combined.loc[start:rd]
        if len(window) < 15:
            continue
        oi_start = window["oi_share_c1"].iloc[0]
        oi_end = window["oi_share_c1"].iloc[-1]
        oi_drop = oi_end - oi_start
        spread_start = window["spread_c1c3"].iloc[0]
        spread_end = window["spread_c1c3"].iloc[-1]
        spread_chg = spread_end - spread_start
        roll_type = "Fast" if oi_drop < -0.15 else "Slow"
        episodes.append({
            "roll_date": rd, "oi_start": oi_start, "oi_end": oi_end,
            "oi_drop": oi_drop, "spread_chg": spread_chg, "type": roll_type,
        })
        if rd.year >= 2023:  # show recent ones
            rprint(
                f"| {rd.date()} | {oi_start:.3f} | {oi_end:.3f} "
                f"| {oi_drop:>+.3f} | {spread_chg:>+.1f} | {roll_type} |"
            )

    ep_df = pd.DataFrame(episodes)
    if not ep_df.empty:
        rprint(f"\nTotal roll episodes: {len(ep_df)}")
        rprint(f"Fast rolls (c1 OI share drop > 15%): {(ep_df['type'] == 'Fast').sum()}")
        rprint(f"Slow rolls: {(ep_df['type'] == 'Slow').sum()}")

        for rtype in ["Fast", "Slow"]:
            sub = ep_df[ep_df["type"] == rtype]
            if len(sub) > 0:
                rprint(f"\n{rtype} rolls: avg spread c1-c3 change = {sub['spread_chg'].mean():+.1f}, "
                       f"median = {sub['spread_chg'].median():+.1f}")

        # correlation
        corr = ep_df["oi_drop"].corr(ep_df["spread_chg"])
        rprint(f"\nCorrelation (c1 OI share drop vs c1-c3 spread change): {corr:.3f}")

    # --- Chart: stacked OI share (last 2 years) ---
    recent = combined.loc["2024-01-01":]
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(16, 10))

    oi_cols = [f"oi_share_c{i}" for i in range(1, 9) if f"oi_share_c{i}" in recent.columns]
    ax1.stackplot(
        recent.index,
        *[recent[c].fillna(0) for c in oi_cols],
        labels=[c.replace("oi_share_", "") for c in oi_cols],
        alpha=0.7,
    )
    for rd in roll_dates:
        if rd >= recent.index[0]:
            ax1.axvline(rd, color="red", lw=0.5, ls="--", alpha=0.5)
    ax1.set_ylabel("OI Share")
    ax1.set_title("FCPO OI Share by Contract (2024+)")
    ax1.legend(loc="upper right", fontsize=8)

    ax2.plot(recent.index, recent["spread_c1c3"], color="navy", lw=1)
    for rd in roll_dates:
        if rd >= recent.index[0]:
            ax2.axvline(rd, color="red", lw=0.5, ls="--", alpha=0.5)
    ax2.set_ylabel("c1 - c3 Spread")
    ax2.set_title("FCPO Calendar Spread c1-c3 (2024+)")
    ax2.axhline(0, color="black", lw=0.5)

    plt.tight_layout()
    plt.savefig(DATA_DIR / "oi_m3_oi_migration.png", dpi=150)
    plt.close()

    # --- Scatter: OI drop vs spread change ---
    if not ep_df.empty:
        fig, ax = plt.subplots(figsize=(8, 6))
        colors = ["#e74c3c" if t == "Fast" else "#3498db" for t in ep_df["type"]]
        ax.scatter(ep_df["oi_drop"], ep_df["spread_chg"], c=colors, alpha=0.6, edgecolor="black", lw=0.5)
        ax.set_xlabel("c1 OI Share Change (pre-roll)")
        ax.set_ylabel("c1-c3 Spread Change")
        ax.set_title("FCPO: OI Migration Speed vs Calendar Spread Change")
        ax.axhline(0, color="black", lw=0.5)
        ax.axvline(0, color="black", lw=0.5)
        # add regression line
        mask = ep_df[["oi_drop", "spread_chg"]].dropna()
        if len(mask) > 5:
            slope, intercept, r, p, se = stats.linregress(mask["oi_drop"], mask["spread_chg"])
            x_line = np.linspace(mask["oi_drop"].min(), mask["oi_drop"].max(), 50)
            ax.plot(x_line, slope * x_line + intercept, "k--", lw=1, label=f"R={r:.2f}, p={p:.3f}")
            ax.legend()
        plt.tight_layout()
        plt.savefig(DATA_DIR / "oi_m3_spread_vs_migration.png", dpi=150)
        plt.close()


# ═══════════════════════════════════════════════════════════════════════════════
# MODULE 4: DCP-BMD Spread Analysis
# ═══════════════════════════════════════════════════════════════════════════════

def module_4(futures, fx):
    rprint("\n" + "=" * 70)
    rprint("## Module 4: DCP-BMD Spread Analysis")
    rprint("=" * 70)

    # --- BMD reference: FCPOc3 ---
    bmd = futures[(futures["RIC"] == "FCPOc3")][["date", "price_usd", "oi"]].copy()
    bmd = bmd.rename(columns={"price_usd": "bmd_price", "oi": "bmd_oi"})

    # --- DCP reference: highest OI month each day ---
    dcp_all = futures[futures["product"] == "DCP"][["date", "cn", "price_usd", "oi"]].copy()
    dcp_all = dcp_all.dropna(subset=["oi"])

    # find the contract with max OI per day
    idx_max = dcp_all.groupby("date")["oi"].idxmax()
    dcp_active = dcp_all.loc[idx_max][["date", "cn", "price_usd", "oi"]].copy()
    dcp_active = dcp_active.rename(columns={
        "cn": "dcp_active_cn", "price_usd": "dcp_price", "oi": "dcp_oi"
    })

    # merge
    spread = bmd.merge(dcp_active, on="date", how="inner").sort_values("date")
    spread["spread"] = spread["dcp_price"] - spread["bmd_price"]

    # day-on-day changes
    spread["spread_chg"] = spread["spread"].diff()
    spread["bmd_oi_chg"] = spread["bmd_oi"].diff()
    spread["dcp_oi_chg"] = spread["dcp_oi"].diff()
    spread["bmd_oi_chg_pct"] = spread["bmd_oi"].pct_change() * 100
    spread["dcp_oi_chg_pct"] = spread["dcp_oi"].pct_change() * 100

    # joint OI regime classification
    def _joint_regime(row):
        d = row["dcp_oi_chg"]
        b = row["bmd_oi_chg"]
        if pd.isna(d) or pd.isna(b):
            return np.nan
        if d > 0 and b > 0:
            return "Both Building"
        if d > 0 and b <= 0:
            return "DCP Build / BMD Liq"
        if d <= 0 and b > 0:
            return "DCP Liq / BMD Build"
        return "Both Liquidating"

    spread["regime"] = spread.apply(_joint_regime, axis=1)

    # forward spread returns
    for h in [1, 5, 10]:
        spread[f"spread_fwd_{h}d"] = spread["spread"].shift(-h) - spread["spread"]

    # --- Active month chart ---
    rprint("\n### DCP Active Contract (Highest OI Month)\n")
    month_counts = spread["dcp_active_cn"].value_counts().sort_index()
    rprint("| Contract | Days as Active | % of Total |")
    rprint("|----------|----------------|------------|")
    total_days = len(spread)
    for cn, cnt in month_counts.items():
        rprint(f"| DCPc{cn}    | {cnt:>14} | {cnt / total_days * 100:>9.1f}% |")

    fig, ax = plt.subplots(figsize=(16, 3))
    ax.scatter(spread["date"], spread["dcp_active_cn"], c="steelblue", s=1, alpha=0.3)
    ax.set_ylabel("Active DCP Contract")
    ax.set_yticks(range(1, 9))
    ax.set_yticklabels([f"c{i}" for i in range(1, 9)])
    ax.set_title("DCP Active Contract (Highest OI) Over Time")
    plt.tight_layout()
    plt.savefig(DATA_DIR / "oi_m4_active_month.png", dpi=150)
    plt.close()

    # --- Regime analysis ---
    rprint("\n### Joint OI Regime Analysis\n")
    rprint("| Regime | Count | Avg Spread Chg | Avg 5D Fwd | Hit 5D Widen% |")
    rprint("|--------|-------|----------------|------------|---------------|")

    regimes = ["Both Building", "DCP Build / BMD Liq", "DCP Liq / BMD Build", "Both Liquidating"]
    regime_colors = {
        "Both Building": "#2ecc71", "DCP Build / BMD Liq": "#e74c3c",
        "DCP Liq / BMD Build": "#3498db", "Both Liquidating": "#e67e22",
    }

    regime_5d = []
    for reg in regimes:
        mask = spread["regime"] == reg
        sub = spread[mask]
        if len(sub) == 0:
            regime_5d.append(0)
            continue
        avg_chg = sub["spread_chg"].mean()
        avg_5d = sub["spread_fwd_5d"].mean()
        hit_5d = (sub["spread_fwd_5d"] > 0).mean() * 100
        regime_5d.append(avg_5d)
        rprint(f"| {reg:<25} | {len(sub):>5} | {avg_chg:>+14.2f} | {avg_5d:>+10.2f} | {hit_5d:>12.1f}% |")

    fig, ax = plt.subplots(figsize=(10, 5))
    bars = ax.bar(range(len(regimes)), regime_5d, color=[regime_colors[r] for r in regimes])
    ax.set_xticks(range(len(regimes)))
    ax.set_xticklabels(regimes, fontsize=8, rotation=15)
    ax.set_ylabel("Avg 5D Forward Spread Change (USD/MT)")
    ax.set_title("DCP-BMD Spread: Avg 5D Forward Change by Joint OI Regime")
    ax.axhline(0, color="black", lw=0.5)
    for bar, val in zip(bars, regime_5d):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height(),
                f"{val:+.2f}", ha="center", va="bottom", fontsize=9)
    plt.tight_layout()
    plt.savefig(DATA_DIR / "oi_m4_regime_returns.png", dpi=150)
    plt.close()

    # --- Spread time series with regime coloring ---
    recent = spread[spread["date"] >= "2023-01-01"].copy()
    fig, ax = plt.subplots(figsize=(16, 6))
    for reg in regimes:
        mask = recent["regime"] == reg
        ax.scatter(recent.loc[mask, "date"], recent.loc[mask, "spread"],
                   c=regime_colors[reg], s=3, alpha=0.6, label=reg)
    ax.plot(recent["date"], recent["spread"], color="black", lw=0.3, alpha=0.3)
    ax.set_ylabel("DCP - BMD Spread (USD/MT)")
    ax.set_title("DCP-BMD Spread with Joint OI Regime (2023+)")
    ax.legend(fontsize=8, markerscale=3)
    plt.tight_layout()
    plt.savefig(DATA_DIR / "oi_m4_spread_timeseries.png", dpi=150)
    plt.close()

    # --- Lag correlation ---
    rprint("\n### Lag Analysis: Does OI Change Lead Spread Movement?\n")
    rprint("| Lag (days) | Corr(DCP OI chg, Spread chg) | Corr(BMD OI chg, Spread chg) |")
    rprint("|------------|------------------------------|------------------------------|")

    lag_dcp, lag_bmd = [], []
    valid = spread.dropna(subset=["dcp_oi_chg", "bmd_oi_chg", "spread_chg"])
    for lag in range(0, 11):
        if lag == 0:
            dc = valid["dcp_oi_chg"].corr(valid["spread_chg"])
            bc = valid["bmd_oi_chg"].corr(valid["spread_chg"])
        else:
            dc = valid["dcp_oi_chg"].corr(valid["spread_chg"].shift(-lag))
            bc = valid["bmd_oi_chg"].corr(valid["spread_chg"].shift(-lag))
        lag_dcp.append(dc)
        lag_bmd.append(bc)
        rprint(f"| {lag:>10} | {dc:>+28.4f} | {bc:>+28.4f} |")

    fig, ax = plt.subplots(figsize=(10, 5))
    ax.plot(range(0, 11), lag_dcp, marker="o", label="DCP OI chg -> Spread chg", color="#e74c3c")
    ax.plot(range(0, 11), lag_bmd, marker="s", label="BMD OI chg -> Spread chg", color="#3498db")
    ax.set_xlabel("Lag (days)")
    ax.set_ylabel("Correlation")
    ax.set_title("Cross-Correlation: OI Change vs Future Spread Change")
    ax.axhline(0, color="black", lw=0.5)
    ax.legend()
    plt.tight_layout()
    plt.savefig(DATA_DIR / "oi_m4_lag_correlation.png", dpi=150)
    plt.close()

    # --- Part B: Persistent regime streaks ---
    rprint("\n### Part B: Regime Streak Analysis\n")
    rprint("How does cumulative spread change evolve as a regime persists?\n")

    spread_valid = spread.dropna(subset=["regime"]).copy()
    spread_valid["regime_grp"] = (spread_valid["regime"] != spread_valid["regime"].shift(1)).cumsum()
    spread_valid["streak_len"] = spread_valid.groupby("regime_grp").cumcount() + 1

    rprint("| Regime | Streak Days | Count | Avg Cum Spread Chg | Avg Fwd 5D |")
    rprint("|--------|-------------|-------|--------------------|------------|")

    fig, ax = plt.subplots(figsize=(12, 6))
    for reg in regimes:
        xs, ys = [], []
        for slen in range(1, 11):
            mask = (spread_valid["regime"] == reg) & (spread_valid["streak_len"] == slen)
            sub = spread_valid[mask]
            if len(sub) < 15:
                continue

            # cumulative spread change from streak start
            cum_chgs = []
            for _, row in sub.iterrows():
                grp = row["regime_grp"]
                grp_data = spread_valid[spread_valid["regime_grp"] == grp]
                cum_chg = grp_data.loc[:row.name, "spread_chg"].sum()
                cum_chgs.append(cum_chg)
            avg_cum = np.mean(cum_chgs)

            fwd5 = sub["spread_fwd_5d"].mean()
            xs.append(slen)
            ys.append(avg_cum)

            rprint(
                f"| {reg:<25} | {slen:>11} | {len(sub):>5} "
                f"| {avg_cum:>+18.2f} | {fwd5:>+10.2f} |"
            )

        if xs:
            ax.plot(xs, ys, marker="o", label=reg, color=regime_colors[reg])

    ax.set_xlabel("Consecutive Days in Regime")
    ax.set_ylabel("Avg Cumulative Spread Change (USD/MT)")
    ax.set_title("DCP-BMD Spread: Cumulative Change by Regime Streak Length")
    ax.axhline(0, color="black", lw=0.5)
    ax.legend(fontsize=8)
    plt.tight_layout()
    plt.savefig(DATA_DIR / "oi_m4_streak_cumulative.png", dpi=150)
    plt.close()


# ═══════════════════════════════════════════════════════════════════════════════
# REPORT GENERATION
# ═══════════════════════════════════════════════════════════════════════════════

def save_report():
    header = [
        f"# OI Analysis: BMD (FCPO) and Dalian (DCP)",
        f"**Generated:** {date.today().isoformat()}",
        f"**Data Range:** 2015-01-01 to 2026-03-24",
        f"**Contracts:** FCPO c1-c8, DCP c1-c8",
        "",
    ]
    report_path = DATA_DIR / "OI Analysis (BMD and Dalian).md"
    with open(report_path, "w", encoding="utf-8") as f:
        for line in header + REPORT_LINES:
            f.write(line + "\n")
    print(f"\nReport saved to: {report_path}")


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    print("Loading and preparing data...")
    futures, fx = load_and_prepare()
    print(f"  {len(futures):,} rows loaded ({futures['RIC'].nunique()} RICs)")
    print(f"  Date range: {futures['date'].min().date()} to {futures['date'].max().date()}")

    module_1(futures)
    module_2(futures)
    module_3(futures)
    module_4(futures, fx)
    save_report()

    print("\nDone. All charts saved to data/oi/")


if __name__ == "__main__":
    main()
