"""
oi_dalian_backtest.py
=====================
Backtest DCP-DBY spread strategies based on OI regime/signal combos.

Training: 2019-01-01 to 2024-12-31
Test:     2025-01-01 to present

Entry: OI regime streaks and signal combos
Exit:  Dynamic — regime flip, OI reversal, or target/stop
"""

import warnings
from pathlib import Path
from datetime import date
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

warnings.filterwarnings("ignore")

ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / "data" / "oi"
RAW_FILE = DATA_DIR / "dcp_dby_oi_raw.csv"

NOISE = 0.1
TRAIN_START = "2019-01-01"
TRAIN_END = "2024-12-31"
TEST_START = "2025-01-01"

REPORT = []


def rprint(line=""):
    print(line)
    REPORT.append(line)


# ═══════════════════════════════════════════════════════════════════════════════
# DATA PREP (reuse from dalian report)
# ═══════════════════════════════════════════════════════════════════════════════

def load_spread():
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

    # day-on-day per RIC
    df["price_prev"] = df.groupby("RIC")["price"].shift(1)
    df["oi_prev"] = df.groupby("RIC")["oi"].shift(1)
    df["price_chg_pct"] = (df["price"] - df["price_prev"]) / df["price_prev"] * 100
    df["oi_chg"] = df["oi"] - df["oi_prev"]
    df["oi_chg_pct"] = df["oi_chg"] / df["oi_prev"] * 100

    def classify(row):
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

    df["signal"] = df.apply(classify, axis=1)

    # liquid month per product per day
    def get_liquid(product):
        sub = df[(df["product"] == product) & df["oi"].notna()].copy()
        idx = sub.groupby("date")["oi"].idxmax()
        liq = sub.loc[idx, ["date", "cn", "price", "oi", "signal", "oi_chg", "oi_chg_pct"]].copy()
        p = product.lower()
        liq = liq.rename(columns={
            "cn": f"{p}_cn", "price": f"{p}_price", "oi": f"{p}_oi",
            "signal": f"{p}_signal", "oi_chg": f"{p}_oi_chg", "oi_chg_pct": f"{p}_oi_chg_pct",
        })
        return liq

    dcp = get_liquid("DCP")
    dby = get_liquid("DBY")

    sp = dcp.merge(dby, on="date", how="inner").sort_values("date").reset_index(drop=True)
    sp["spread"] = sp["dcp_price"] - sp["dby_price"]
    sp["spread_chg"] = sp["spread"].diff()

    # curve OI
    for prod in ["DCP", "DBY"]:
        curve = df[df["product"] == prod].groupby("date")["oi"].sum().reset_index()
        curve = curve.rename(columns={"oi": f"{prod.lower()}_curve_oi"})
        sp = sp.merge(curve, on="date", how="left")

    sp["dcp_curve_oi_chg_pct"] = sp["dcp_curve_oi"].pct_change() * 100
    sp["dby_curve_oi_chg_pct"] = sp["dby_curve_oi"].pct_change() * 100

    # joint regime
    def joint_regime(row):
        d, b = row["dcp_oi_chg"], row["dby_oi_chg"]
        if pd.isna(d) or pd.isna(b):
            return np.nan
        if d > 0 and b > 0:
            return "Both_Build"
        if d > 0 and b <= 0:
            return "DCP_Build"
        if d <= 0 and b > 0:
            return "DBY_Build"
        return "Both_Liq"

    sp["regime"] = sp.apply(joint_regime, axis=1)

    # streaks
    sp["regime_grp"] = (sp["regime"] != sp["regime"].shift(1)).cumsum()
    sp["streak"] = sp.groupby("regime_grp").cumcount() + 1

    # combined signal key
    sp["combo"] = sp["dcp_signal"].astype(str) + "_" + sp["dby_signal"].astype(str)

    return sp


# ═══════════════════════════════════════════════════════════════════════════════
# TRADE ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class Trade:
    entry_date: pd.Timestamp
    direction: int          # +1 = long spread (buy DCP-DBY), -1 = short
    entry_price: float
    exit_date: pd.Timestamp = None
    exit_price: float = None
    exit_reason: str = ""
    pnl: float = 0.0
    holding_days: int = 0
    peak_pnl: float = 0.0
    trough_pnl: float = 0.0


def run_backtest(sp, strategy, label=""):
    """
    Generic backtest engine.
    strategy dict keys:
      entry_fn(row, state) -> +1 (long), -1 (short), 0 (no trade)
      exit_fn(row, trade, state) -> True/False
      state: dict carried across bars
    """
    trades = []
    active_trade = None
    state = strategy.get("init_state", {}).copy()

    for i, row in sp.iterrows():
        if pd.isna(row["regime"]):
            continue

        if active_trade is not None:
            # update P&L
            unrealized = (row["spread"] - active_trade.entry_price) * active_trade.direction
            active_trade.peak_pnl = max(active_trade.peak_pnl, unrealized)
            active_trade.trough_pnl = min(active_trade.trough_pnl, unrealized)
            active_trade.holding_days += 1

            if strategy["exit_fn"](row, active_trade, state):
                active_trade.exit_date = row["date"]
                active_trade.exit_price = row["spread"]
                active_trade.pnl = (row["spread"] - active_trade.entry_price) * active_trade.direction
                trades.append(active_trade)
                active_trade = None
        else:
            direction = strategy["entry_fn"](row, state)
            if direction != 0:
                active_trade = Trade(
                    entry_date=row["date"],
                    direction=direction,
                    entry_price=row["spread"],
                )

        # update state
        if "update_fn" in strategy:
            strategy["update_fn"](row, state)

    # close any open trade at end
    if active_trade is not None:
        last = sp.iloc[-1]
        active_trade.exit_date = last["date"]
        active_trade.exit_price = last["spread"]
        active_trade.pnl = (last["spread"] - active_trade.entry_price) * active_trade.direction
        active_trade.exit_reason = "end_of_data"
        trades.append(active_trade)

    return trades


def trades_to_df(trades):
    if not trades:
        return pd.DataFrame()
    records = []
    for t in trades:
        records.append({
            "entry_date": t.entry_date, "exit_date": t.exit_date,
            "direction": t.direction, "entry": t.entry_price, "exit": t.exit_price,
            "pnl": t.pnl, "days": t.holding_days, "reason": t.exit_reason,
            "peak_pnl": t.peak_pnl, "trough_pnl": t.trough_pnl,
        })
    return pd.DataFrame(records)


def print_stats(tdf, label):
    if tdf.empty:
        rprint(f"\n**{label}**: No trades.")
        return
    n = len(tdf)
    wins = (tdf["pnl"] > 0).sum()
    total_pnl = tdf["pnl"].sum()
    avg_pnl = tdf["pnl"].mean()
    med_pnl = tdf["pnl"].median()
    avg_days = tdf["days"].mean()
    win_rate = wins / n * 100
    avg_win = tdf.loc[tdf["pnl"] > 0, "pnl"].mean() if wins > 0 else 0
    avg_loss = tdf.loc[tdf["pnl"] <= 0, "pnl"].mean() if (n - wins) > 0 else 0
    # months in data
    date_range = (tdf["exit_date"].max() - tdf["entry_date"].min()).days / 30.44
    trades_per_month = n / date_range if date_range > 0 else 0

    rprint(f"\n### {label}\n")
    rprint(f"| Metric | Value |")
    rprint(f"|--------|-------|")
    rprint(f"| Trades | {n} |")
    rprint(f"| Win Rate | {win_rate:.1f}% |")
    rprint(f"| Total PnL | {total_pnl:+.0f} CNY |")
    rprint(f"| Avg PnL/Trade | {avg_pnl:+.1f} CNY |")
    rprint(f"| Median PnL | {med_pnl:+.1f} CNY |")
    rprint(f"| Avg Win | {avg_win:+.1f} CNY |")
    rprint(f"| Avg Loss | {avg_loss:+.1f} CNY |")
    rprint(f"| Avg Holding Days | {avg_days:.1f} |")
    rprint(f"| Trades/Month | {trades_per_month:.1f} |")
    rprint(f"| Max Drawdown (worst trade) | {tdf['trough_pnl'].min():+.0f} CNY |")
    rprint(f"| Best Trade | {tdf['pnl'].max():+.0f} CNY |")
    rprint(f"| Worst Trade | {tdf['pnl'].min():+.0f} CNY |")
    rprint(f"| Profit Factor | {abs(tdf.loc[tdf['pnl']>0,'pnl'].sum() / tdf.loc[tdf['pnl']<=0,'pnl'].sum()):.2f} |" if (tdf["pnl"] <= 0).any() and (tdf["pnl"] > 0).any() else "| Profit Factor | inf |")

    return {"label": label, "n": n, "win_rate": win_rate, "avg_pnl": avg_pnl,
            "total_pnl": total_pnl, "trades_pm": trades_per_month}


# ═══════════════════════════════════════════════════════════════════════════════
# STRATEGY DEFINITIONS
# ═══════════════════════════════════════════════════════════════════════════════

def build_strategies(train_sp):
    """Scan training data and build strategy configs."""
    strategies = []

    # ── S1: Regime streak entry, regime flip exit ─────────────────────────────
    # DCP_Build streak >= N → long spread; DBY_Build streak >= N → short spread
    # Exit: regime flips away from entry regime OR max hold
    for streak_n in [2, 3]:
        for max_hold in [10, 20]:
            def make_s1(sn, mh):
                def entry(row, state):
                    if row["regime"] == "DCP_Build" and row["streak"] >= sn:
                        return +1
                    if row["regime"] == "DBY_Build" and row["streak"] >= sn:
                        return -1
                    return 0
                def exit_fn(row, trade, state):
                    if trade.holding_days >= mh:
                        trade.exit_reason = "max_hold"
                        return True
                    if row["regime"] != ("DCP_Build" if trade.direction == 1 else "DBY_Build"):
                        trade.exit_reason = "regime_flip"
                        return True
                    return False
                return {"entry_fn": entry, "exit_fn": exit_fn,
                        "label": f"S1_regime_streak{sn}_max{mh}"}
            strategies.append(make_s1(streak_n, max_hold))

    # ── S2: Signal combo entry, OI reversal exit ─────────────────────────────
    # Long spread: DCP LBU + DBY LU (best combo from analysis: +73.5, 69% hit)
    # Short spread: DCP LU + DBY LBU
    # Exit: when OI flips against position OR target/stop
    for target, stop in [(40, 30), (60, 40), (80, 50)]:
        def make_s2(tgt, stp):
            def entry(row, state):
                c = row["combo"]
                if c == "LBU_LU":
                    return +1
                if c == "LBU_SBU":
                    return +1
                if c == "LU_LBU":
                    return -1
                if c == "SBU_LBU":
                    return -1
                return 0
            def exit_fn(row, trade, state):
                unrealized = (row["spread"] - trade.entry_price) * trade.direction
                if unrealized >= tgt:
                    trade.exit_reason = f"target_{tgt}"
                    return True
                if unrealized <= -stp:
                    trade.exit_reason = f"stop_{stp}"
                    return True
                # OI reversal exit: if entry was long (DCP building) and DCP OI now dropping 2 days
                if trade.holding_days >= 2:
                    if trade.direction == 1 and row["dcp_oi_chg"] < 0 and state.get("prev_dcp_oi_chg", 0) < 0:
                        trade.exit_reason = "dcp_oi_reversal"
                        return True
                    if trade.direction == -1 and row["dby_oi_chg"] < 0 and state.get("prev_dby_oi_chg", 0) < 0:
                        trade.exit_reason = "dby_oi_reversal"
                        return True
                if trade.holding_days >= 20:
                    trade.exit_reason = "max_hold"
                    return True
                return False
            def update(row, state):
                state["prev_dcp_oi_chg"] = row["dcp_oi_chg"]
                state["prev_dby_oi_chg"] = row["dby_oi_chg"]
            return {"entry_fn": entry, "exit_fn": exit_fn, "update_fn": update,
                    "init_state": {"prev_dcp_oi_chg": 0, "prev_dby_oi_chg": 0},
                    "label": f"S2_combo_tgt{tgt}_stp{stp}"}
        strategies.append(make_s2(target, stop))

    # ── S3: Both Liq → mean reversion (long spread) ─────────────────────────
    # From analysis: Both_Liq has +17.3 avg 5D fwd. Spread compressed, bounce back.
    # Exit: regime change or target/stop
    for streak_n in [2, 3]:
        for target, stop in [(30, 25), (50, 35)]:
            def make_s3(sn, tgt, stp):
                def entry(row, state):
                    if row["regime"] == "Both_Liq" and row["streak"] >= sn:
                        return +1  # mean reversion: spread bounces
                    return 0
                def exit_fn(row, trade, state):
                    unrealized = (row["spread"] - trade.entry_price) * trade.direction
                    if unrealized >= tgt:
                        trade.exit_reason = f"target_{tgt}"
                        return True
                    if unrealized <= -stp:
                        trade.exit_reason = f"stop_{stp}"
                        return True
                    if trade.holding_days >= 15:
                        trade.exit_reason = "max_hold"
                        return True
                    return False
                return {"entry_fn": entry, "exit_fn": exit_fn,
                        "label": f"S3_bothLiq_streak{sn}_tgt{tgt}_stp{stp}"}
            strategies.append(make_s3(streak_n, target, stop))

    # ── S4: DCP Liq / DBY Build momentum (short spread) ─────────────────────
    # From analysis: -30.1 avg 5D, and streak 3 → -182.5 fwd 5D
    # Pure momentum: ride the trend
    for streak_n in [2, 3]:
        for max_hold in [8, 15]:
            def make_s4(sn, mh):
                def entry(row, state):
                    if row["regime"] == "DBY_Build" and row["streak"] >= sn:
                        return -1  # short spread
                    return 0
                def exit_fn(row, trade, state):
                    if trade.holding_days >= mh:
                        trade.exit_reason = "max_hold"
                        return True
                    # exit if DCP starts building OI (regime breaks)
                    if row["regime"] in ["DCP_Build", "Both_Build"] and trade.holding_days >= 2:
                        trade.exit_reason = "regime_break"
                        return True
                    # trailing stop: if we were up 30+ and now gave back half
                    if trade.peak_pnl >= 30 and (trade.peak_pnl - ((row["spread"] - trade.entry_price) * trade.direction)) > trade.peak_pnl * 0.5:
                        trade.exit_reason = "trailing_stop"
                        return True
                    return False
                return {"entry_fn": entry, "exit_fn": exit_fn,
                        "label": f"S4_dcpLiq_dbyBuild_streak{sn}_max{mh}"}
            strategies.append(make_s4(streak_n, max_hold))

    # ── S5: Curve OI divergence ──────────────────────────────────────────────
    # DCP curve OI rising + DBY curve OI falling = long spread
    for consec in [2, 3]:
        for target, stop in [(40, 30), (60, 40)]:
            def make_s5(cn, tgt, stp):
                def entry(row, state):
                    hist = state.get("curve_hist", [])
                    if len(hist) >= cn:
                        recent = hist[-cn:]
                        if all(d > 0 and b < 0 for d, b in recent):
                            return +1  # DCP OI growing, DBY OI shrinking
                        if all(d < 0 and b > 0 for d, b in recent):
                            return -1
                    return 0
                def exit_fn(row, trade, state):
                    unrealized = (row["spread"] - trade.entry_price) * trade.direction
                    if unrealized >= tgt:
                        trade.exit_reason = f"target_{tgt}"
                        return True
                    if unrealized <= -stp:
                        trade.exit_reason = f"stop_{stp}"
                        return True
                    if trade.holding_days >= 15:
                        trade.exit_reason = "max_hold"
                        return True
                    return False
                def update(row, state):
                    if "curve_hist" not in state:
                        state["curve_hist"] = []
                    d = row.get("dcp_curve_oi_chg_pct", 0)
                    b = row.get("dby_curve_oi_chg_pct", 0)
                    if not pd.isna(d) and not pd.isna(b):
                        state["curve_hist"].append((d, b))
                        if len(state["curve_hist"]) > 10:
                            state["curve_hist"] = state["curve_hist"][-10:]
                return {"entry_fn": entry, "exit_fn": exit_fn, "update_fn": update,
                        "init_state": {"curve_hist": []},
                        "label": f"S5_curveOI_div{cn}_tgt{tgt}_stp{stp}"}
            strategies.append(make_s5(consec, target, stop))

    # ── S6: DCP 2-day LBU streak + DBY bearish (high conviction long) ───────
    for target, stop in [(50, 35), (80, 50)]:
        def make_s6(tgt, stp):
            def entry(row, state):
                prev_dcp = state.get("prev_dcp_signal", "")
                if prev_dcp == "LBU" and row["dcp_signal"] == "LBU" and row["dby_signal"] in ["SBU", "LU"]:
                    return +1
                if prev_dcp == "SBU" and row["dcp_signal"] == "SBU" and row["dby_signal"] in ["LBU", "SC"]:
                    return -1
                return 0
            def exit_fn(row, trade, state):
                unrealized = (row["spread"] - trade.entry_price) * trade.direction
                if unrealized >= tgt:
                    trade.exit_reason = f"target_{tgt}"
                    return True
                if unrealized <= -stp:
                    trade.exit_reason = f"stop_{stp}"
                    return True
                # OI-based dynamic exit: if DCP OI drops for 2 consecutive days on long
                if trade.holding_days >= 2:
                    if trade.direction == 1 and row["dcp_oi_chg"] < 0 and state.get("prev_dcp_oi_chg", 0) < 0:
                        trade.exit_reason = "oi_exit"
                        return True
                    if trade.direction == -1 and row["dcp_oi_chg"] > 0 and state.get("prev_dcp_oi_chg", 0) > 0:
                        trade.exit_reason = "oi_exit"
                        return True
                if trade.holding_days >= 20:
                    trade.exit_reason = "max_hold"
                    return True
                return False
            def update(row, state):
                state["prev_dcp_signal"] = row["dcp_signal"]
                state["prev_dcp_oi_chg"] = row["dcp_oi_chg"]
            return {"entry_fn": entry, "exit_fn": exit_fn, "update_fn": update,
                    "init_state": {"prev_dcp_signal": "", "prev_dcp_oi_chg": 0},
                    "label": f"S6_dcp2dayStreak_tgt{tgt}_stp{stp}"}
        strategies.append(make_s6(target, stop))

    return strategies


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    rprint("# DCP-DBY Spread Backtest: OI-Based Strategies")
    rprint(f"**Generated:** {date.today().isoformat()}")
    rprint(f"**Training:** {TRAIN_START} to {TRAIN_END}")
    rprint(f"**Test:** {TEST_START} to present")
    rprint("")

    print("Loading data...")
    sp = load_spread()
    sp = sp[sp["date"] >= TRAIN_START].reset_index(drop=True)

    train = sp[(sp["date"] >= TRAIN_START) & (sp["date"] <= TRAIN_END)].copy()
    test = sp[sp["date"] >= TEST_START].copy()

    print(f"  Training: {len(train)} rows ({train['date'].min().date()} to {train['date'].max().date()})")
    print(f"  Test:     {len(test)} rows ({test['date'].min().date()} to {test['date'].max().date()})")

    strategies = build_strategies(train)
    print(f"  Strategies to scan: {len(strategies)}")

    # ── Phase 1: Train scan ──────────────────────────────────────────────────
    rprint("\n" + "=" * 70)
    rprint("## Phase 1: Training Period Scan (2019-2024)")
    rprint("=" * 70)

    train_results = []
    for strat in strategies:
        trades = run_backtest(train, strat)
        tdf = trades_to_df(trades)
        if tdf.empty:
            continue
        n = len(tdf)
        avg_pnl = tdf["pnl"].mean()
        win_rate = (tdf["pnl"] > 0).mean() * 100
        date_range = (tdf["exit_date"].max() - tdf["entry_date"].min()).days / 30.44
        tpm = n / date_range if date_range > 0 else 0
        total_pnl = tdf["pnl"].sum()
        avg_days = tdf["days"].mean()
        profit_factor = abs(tdf.loc[tdf["pnl"] > 0, "pnl"].sum() / tdf.loc[tdf["pnl"] <= 0, "pnl"].sum()) if (tdf["pnl"] <= 0).any() and (tdf["pnl"] > 0).any() else 99

        train_results.append({
            "label": strat["label"], "n": n, "avg_pnl": avg_pnl,
            "win_rate": win_rate, "total_pnl": total_pnl,
            "trades_pm": tpm, "avg_days": avg_days,
            "profit_factor": profit_factor, "strat": strat, "tdf": tdf,
        })

    # rank by criteria
    res_df = pd.DataFrame([{k: v for k, v in r.items() if k not in ["strat", "tdf"]} for r in train_results])

    rprint("\n### All Strategies Ranked by Total PnL\n")
    rprint("| Strategy | Trades | Avg PnL | Win% | Total PnL | Trades/Mo | Avg Days | PF |")
    rprint("|----------|--------|---------|------|-----------|-----------|----------|----|")
    res_df_sorted = res_df.sort_values("total_pnl", ascending=False)
    for _, r in res_df_sorted.iterrows():
        rprint(
            f"| {r['label']:<35} | {r['n']:>6} | {r['avg_pnl']:>+7.1f} "
            f"| {r['win_rate']:>4.0f}% | {r['total_pnl']:>+9.0f} "
            f"| {r['trades_pm']:>9.1f} | {r['avg_days']:>8.1f} | {r['profit_factor']:>4.1f} |"
        )

    # ── Select top strategies (diverse picks across families) ───────────────
    viable = [r for r in train_results if r["avg_pnl"] > 0 and r["n"] >= 5]

    # Pick best from each strategy family for diversity
    families = {}
    for r in viable:
        parts = r["label"].split("_")
        fam = parts[0] + "_" + parts[1]
        if fam not in families or r["total_pnl"] > families[fam]["total_pnl"]:
            families[fam] = r

    # Category A: low frequency (<=1/month), highest avg return per trade
    cat_a = sorted([r for r in families.values() if r["trades_pm"] <= 1.0],
                   key=lambda x: -x["avg_pnl"])
    # Category B: higher frequency (>0.5/month), best total PnL
    cat_b = sorted([r for r in families.values() if r["trades_pm"] > 0.5],
                   key=lambda x: -x["total_pnl"])

    seen = set()
    top_picks = []
    for lst in [cat_a[:3], cat_b[:3]]:
        for r in lst:
            if r["label"] not in seen:
                top_picks.append(r)
                seen.add(r["label"])

    # best profit factor if not already included
    pf_best = sorted(viable, key=lambda x: -x["profit_factor"])
    if pf_best and pf_best[0]["label"] not in seen:
        top_picks.append(pf_best[0])
        seen.add(pf_best[0]["label"])

    rprint("\n" + "=" * 70)
    rprint("## Phase 2: Top Strategies — Training Detail")
    rprint("=" * 70)

    for r in top_picks:
        print_stats(r["tdf"], f"TRAIN: {r['label']}")
        # exit reason breakdown
        reasons = r["tdf"]["reason"].value_counts()
        rprint("\n**Exit Reasons:**\n")
        for reason, cnt in reasons.items():
            rprint(f"- {reason}: {cnt}")

    # ── Phase 3: Out-of-sample test ──────────────────────────────────────────
    rprint("\n" + "=" * 70)
    rprint("## Phase 3: Out-of-Sample Test (2025-Jan to Now)")
    rprint("=" * 70)

    test_summary = []
    for r in top_picks:
        strat = r["strat"]
        trades = run_backtest(test, strat)
        tdf = trades_to_df(trades)
        stats = print_stats(tdf, f"TEST: {r['label']}")
        if stats:
            test_summary.append(stats)

        # print individual trades
        if not tdf.empty:
            rprint("\n**Trade Log:**\n")
            rprint("| # | Entry | Exit | Dir | Entry Px | Exit Px | PnL | Days | Reason |")
            rprint("|---|-------|------|-----|----------|---------|-----|------|--------|")
            for j, t in tdf.iterrows():
                d = "LONG" if t["direction"] == 1 else "SHORT"
                rprint(
                    f"| {j+1} | {t['entry_date'].date()} | {t['exit_date'].date()} "
                    f"| {d} | {t['entry']:.0f} | {t['exit']:.0f} "
                    f"| {t['pnl']:+.0f} | {t['days']} | {t['reason']} |"
                )

    # ── Phase 4: Equity curves ───────────────────────────────────────────────
    rprint("\n" + "=" * 70)
    rprint("## Equity Curves")
    rprint("=" * 70)

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(16, 10))

    for r in top_picks:
        # training equity
        trades = run_backtest(train, r["strat"])
        tdf = trades_to_df(trades)
        if not tdf.empty:
            tdf["cum_pnl"] = tdf["pnl"].cumsum()
            ax1.plot(tdf["exit_date"], tdf["cum_pnl"], marker=".", ms=3, label=r["label"])

        # test equity
        trades = run_backtest(test, r["strat"])
        tdf = trades_to_df(trades)
        if not tdf.empty:
            tdf["cum_pnl"] = tdf["pnl"].cumsum()
            ax2.plot(tdf["exit_date"], tdf["cum_pnl"], marker="o", ms=5, label=r["label"])

    ax1.set_title("Training Period Equity (2019-2024)")
    ax1.set_ylabel("Cumulative PnL (CNY)")
    ax1.axhline(0, color="black", lw=0.5)
    ax1.legend(fontsize=7)
    ax1.grid(alpha=0.3)

    ax2.set_title("Out-of-Sample Equity (2025+)")
    ax2.set_ylabel("Cumulative PnL (CNY)")
    ax2.axhline(0, color="black", lw=0.5)
    ax2.legend(fontsize=7)
    ax2.grid(alpha=0.3)

    plt.tight_layout()
    plt.savefig(DATA_DIR / "oi_dalian_backtest_equity.png", dpi=150)
    plt.close()

    # spread with trade markers
    fig, ax = plt.subplots(figsize=(16, 6))
    test_plot = test.copy()
    ax.plot(test_plot["date"], test_plot["spread"], color="gray", lw=0.8, alpha=0.7, label="Spread")

    colors_strat = ["#e74c3c", "#2ecc71", "#3498db", "#9b59b6", "#e67e22"]
    for k, r in enumerate(top_picks):
        trades = run_backtest(test, r["strat"])
        tdf = trades_to_df(trades)
        if tdf.empty:
            continue
        c = colors_strat[k % len(colors_strat)]
        for _, t in tdf.iterrows():
            marker_entry = "^" if t["direction"] == 1 else "v"
            ax.plot(t["entry_date"], t["entry"], marker_entry, color=c, ms=10, zorder=5)
            ax.plot(t["exit_date"], t["exit"], "x", color=c, ms=10, zorder=5)
            ax.plot([t["entry_date"], t["exit_date"]], [t["entry"], t["exit"]],
                    color=c, lw=1.5, alpha=0.5)
        # dummy for legend
        ax.plot([], [], color=c, marker="^", ls="-", label=r["label"][:30])

    ax.set_title("OOS Trades on DCP-DBY Spread (2025+)")
    ax.set_ylabel("Spread (CNY)")
    ax.legend(fontsize=7, loc="upper left")
    plt.tight_layout()
    plt.savefig(DATA_DIR / "oi_dalian_backtest_trades.png", dpi=150)
    plt.close()

    # ── Save report ──────────────────────────────────────────────────────────
    path = DATA_DIR / "OI Backtest (DCP vs DBY Dalian).md"
    with open(path, "w", encoding="utf-8") as f:
        for line in REPORT:
            f.write(line + "\n")
    print(f"\nReport saved to: {path}")
    print("Done. Charts saved to data/oi/")


if __name__ == "__main__":
    main()
