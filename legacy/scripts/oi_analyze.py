import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import config

RAW_FILE = ROOT / "data" / "raw"  / "all_instruments_raw.csv"
PROC_DIR = ROOT / "data" / "processed"

NOISE = 0.1   # price change % below this threshold → Neutral


# ── Classification ─────────────────────────────────────────────────────────────

def _classify(price_pct: float, oi_pct: float) -> str:
    if abs(price_pct) < NOISE:
        return "Neutral"
    if price_pct > 0 and oi_pct > 0:
        return "Long Buildup"
    if price_pct < 0 and oi_pct > 0:
        return "Short Buildup"
    if price_pct > 0 and oi_pct <= 0:
        return "Short Covering"
    return "Long Unwinding"


# ── Load & prepare ─────────────────────────────────────────────────────────────

def load_futures() -> pd.DataFrame:
    """
    Load raw CSV, filter to futures RICs, resolve price column (Last → Settlement),
    parse Volume and Open Interest. Returns long-format DataFrame sorted by RIC + date.
    """
    df = pd.read_csv(RAW_FILE, parse_dates=["Trade Date"])
    df = df.rename(columns={"Trade Date": "date"})
    df["date"] = pd.to_datetime(df["date"]).dt.normalize()

    missing = [c for c in ("Volume", "Open Interest") if c not in df.columns]
    if missing:
        raise ValueError(
            f"Column(s) {missing} not found in raw CSV.\n"
            "Run fetch.py (with updated FIELDS) to download fresh data first."
        )

    df = df[df["RIC"].isin(config.FUTURES_RICS)].copy()

    df["price"] = pd.to_numeric(df["Last"], errors="coerce").fillna(
                  pd.to_numeric(df["Settlement Price"], errors="coerce"))
    df["oi"]     = pd.to_numeric(df["Open Interest"], errors="coerce")
    df["volume"] = pd.to_numeric(df["Volume"],        errors="coerce")

    df = (df[["date", "RIC", "price", "oi", "volume"]]
            .drop_duplicates(subset=["date", "RIC"])
            .sort_values(["RIC", "date"])
            .reset_index(drop=True))

    return df


# ── Day-on-day computation ─────────────────────────────────────────────────────

def compute_dod(df: pd.DataFrame) -> pd.DataFrame:
    """
    For each RIC compute day-on-day absolute and percentage changes for
    price, OI, and volume; then classify each row.
    """
    parts = []

    for ric, grp in df.groupby("RIC", sort=False):
        grp = grp.sort_values("date").copy()

        grp["price_chg"]     = grp["price"].diff()
        grp["price_chg_pct"] = grp["price"].pct_change(fill_method=None) * 100

        grp["oi_chg"]        = grp["oi"].diff()
        grp["oi_chg_pct"]    = grp["oi"].pct_change(fill_method=None) * 100

        grp["volume_chg"]    = grp["volume"].diff()
        grp["volume_chg_pct"]= grp["volume"].pct_change(fill_method=None) * 100

        grp["signal"] = grp.apply(
            lambda r: _classify(
                r["price_chg_pct"] if pd.notna(r["price_chg_pct"]) else 0.0,
                r["oi_chg_pct"]    if pd.notna(r["oi_chg_pct"])    else 0.0,
            ),
            axis=1,
        )

        parts.append(grp)

    out = pd.concat(parts, ignore_index=True)
    out["date"] = out["date"].dt.strftime("%Y-%m-%d")
    return out


# ── Save ───────────────────────────────────────────────────────────────────────

def save(analysis: pd.DataFrame) -> pd.DataFrame:
    PROC_DIR.mkdir(parents=True, exist_ok=True)

    full_path   = PROC_DIR / "oi_analysis.csv"
    latest_path = PROC_DIR / "oi_latest.csv"

    analysis.to_csv(full_path, index=False)
    print(f"  SAVED oi_analysis.csv   {len(analysis):>5} rows")

    latest = (analysis
              .sort_values("date")
              .groupby("RIC", sort=False)
              .last()
              .reset_index())
    latest.to_csv(latest_path, index=False)
    print(f"  SAVED oi_latest.csv     {len(latest):>5} rows")

    return latest


# ── Terminal summary ───────────────────────────────────────────────────────────

def _fmt_chg(val, pct, unit="") -> str:
    """Format absolute change + pct, e.g. '+1 234  (+2.3%)'."""
    if pd.isna(val) or pd.isna(pct):
        return "n/a"
    sign = "+" if val >= 0 else ""
    return f"{sign}{val:,.0f}{unit}  ({sign}{pct:.1f}%)"


def print_summary(latest: pd.DataFrame) -> None:
    print(f"\n{'='*72}")
    print(f"  OI / Volume Analysis — Latest Signal per Contract")
    print(f"{'='*72}")

    for product, rics in config.RICS["futures"].items():
        rows = latest[latest["RIC"].isin(rics)].set_index("RIC")
        if rows.empty:
            continue

        print(f"\n  {product}")
        for ric in rics:
            if ric not in rows.index:
                continue
            r = rows.loc[ric]

            price_str  = f"{r['price']:>8,.2f}" if pd.notna(r["price"]) else "      n/a"
            price_dod  = _fmt_chg(r["price_chg"], r["price_chg_pct"])
            oi_str     = f"{r['oi']:>10,.0f}" if pd.notna(r["oi"]) else "         n/a"
            oi_dod     = _fmt_chg(r["oi_chg"], r["oi_chg_pct"])
            vol_str    = f"{r['volume']:>10,.0f}" if pd.notna(r["volume"]) else "         n/a"
            vol_dod    = _fmt_chg(r["volume_chg"], r["volume_chg_pct"])
            signal     = r["signal"]

            print(f"    {ric:<12}  [{signal:<15}]"
                  f"  Price: {price_str} ({price_dod})"
                  f"  OI: {oi_str} ({oi_dod})"
                  f"  Vol: {vol_str} ({vol_dod})")

    print(f"\n{'='*72}\n")


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    print(f"Loading {RAW_FILE.name}...")
    df = load_futures()
    print(f"  {len(df):,} rows across {df['RIC'].nunique()} futures RICs\n")

    print("Computing day-on-day changes and signals...")
    analysis = compute_dod(df)

    print("\nSaving output files...")
    latest = save(analysis)

    print_summary(latest)


if __name__ == "__main__":
    main()
