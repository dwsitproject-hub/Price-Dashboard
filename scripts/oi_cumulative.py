import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import config

RAW_FILE = ROOT / "data" / "raw" / "all_instruments_raw.csv"
PROC_DIR = ROOT / "data" / "processed"
OUT_FILE = PROC_DIR / "oi_cumulative.csv"
THRESH_FILE = PROC_DIR / "oi_arrow_thresholds.csv"

# Front months averaged for the "outright price" direction indicator, per
# trader's explicit ask — c1-c5, not just c1, to smooth out prompt-month roll noise.
PRICE_FRONT_CONTRACTS = 5

# Arrow-threshold lookback window, per trader's explicit ask — median/75th
# percentile of abs(day-on-day % price move) computed over this window,
# per product, rather than a single fixed threshold for every instrument.
ARROW_THRESHOLD_START = "2023-01-01"


def _load_raw() -> pd.DataFrame:
    df = pd.read_csv(RAW_FILE, parse_dates=["Trade Date"])
    df = df.rename(columns={"Trade Date": "date"})
    df["date"] = pd.to_datetime(df["date"]).dt.normalize()

    df = df[df["RIC"].isin(config._OI_CUM_ALL_RICS)].copy()

    df["price"]  = pd.to_numeric(df["Last"], errors="coerce").fillna(
                   pd.to_numeric(df["Settlement Price"], errors="coerce"))
    df["oi"]     = pd.to_numeric(df["Open Interest"], errors="coerce")
    df["volume"] = pd.to_numeric(df["Volume"], errors="coerce")

    return (df[["date", "RIC", "price", "oi", "volume"]]
            .drop_duplicates(subset=["date", "RIC"])
            .sort_values(["RIC", "date"])
            .reset_index(drop=True))


def build_cumulative() -> pd.DataFrame:
    """
    Total OI / Total Volume / avg price per product per day. Price is kept in
    each instrument's own native quote unit (MYR/MT, cents/lb, USD/barrel,
    etc. — see dashboard.py SPREAD_LOCAL_UNITS), NOT converted to USD/MT, per
    trader's explicit ask to see each instrument in its own local currency.
    """
    long_df = _load_raw()

    price_wide  = long_df.pivot(index="date", columns="RIC", values="price").sort_index()
    oi_wide     = long_df.pivot(index="date", columns="RIC", values="oi").sort_index()
    volume_wide = long_df.pivot(index="date", columns="RIC", values="volume").sort_index()

    records = []
    for product, rics in config.OI_CUMULATIVE_RICS.items():
        present_rics = [r for r in rics if r in oi_wide.columns]
        if not present_rics:
            continue

        total_oi     = oi_wide[present_rics].clip(lower=0).sum(axis=1, min_count=1)
        vol_rics     = [r for r in present_rics if r in volume_wide.columns]
        total_volume = (volume_wide[vol_rics].clip(lower=0).sum(axis=1, min_count=1)
                        if vol_rics else pd.Series(dtype=float))

        front = [r for r in rics[:PRICE_FRONT_CONTRACTS] if r in price_wide.columns]
        avg_price = price_wide[front].mean(axis=1, skipna=True) if front else pd.Series(dtype=float)

        prod_df = pd.DataFrame({
            "total_oi": total_oi, "total_volume": total_volume, "avg_price": avg_price,
        }).dropna(subset=["total_oi"])
        prod_df = prod_df.sort_index()
        prod_df["oi_chg"]         = prod_df["total_oi"].diff()
        prod_df["volume_chg"]     = prod_df["total_volume"].diff()
        prod_df["price_chg_pct"]  = prod_df["avg_price"].pct_change(fill_method=None) * 100
        # % of the day's volume that resulted in net new Open Interest, per
        # trader's explicit ask — a rough gauge of "new contracts" vs same-day
        # offsetting/day-trading turnover. Only meaningful when volume > 0.
        prod_df["oi_vol_pct"] = (prod_df["oi_chg"] / prod_df["total_volume"] * 100).where(
            prod_df["total_volume"] > 0
        )
        prod_df["product"] = product
        prod_df = prod_df.reset_index().rename(columns={"index": "date"})
        records.append(prod_df)

    out = pd.concat(records, ignore_index=True) if records else pd.DataFrame()
    return out[["date", "product", "total_oi", "oi_chg", "total_volume", "volume_chg",
                "oi_vol_pct", "avg_price", "price_chg_pct"]]


def build_arrow_thresholds(df: pd.DataFrame) -> pd.DataFrame:
    """
    Per-product arrow-magnitude thresholds for the Cumulative tab's price-
    direction indicator: median and 75th percentile of abs(day-on-day %
    price move) over the trailing ~3 years (2023-present), per trader's
    explicit ask, so "how big is this move" is judged against each
    instrument's own volatility rather than one fixed % for every product.
    """
    start = pd.Timestamp(ARROW_THRESHOLD_START)
    records = []
    for product, sub in df.groupby("product"):
        recent = sub[sub["date"] >= start]["price_chg_pct"].abs().dropna()
        if recent.empty:
            continue
        records.append({
            "product":    product,
            "median_pct": recent.median(),
            "p75_pct":    recent.quantile(0.75),
            "n_obs":      len(recent),
        })
    return pd.DataFrame(records)


def save(df: pd.DataFrame) -> None:
    PROC_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT_FILE, index=False)
    print(f"  SAVED oi_cumulative.csv  {len(df):>5} rows  ({df['product'].nunique()} products)")


def save_thresholds(thresh_df: pd.DataFrame) -> None:
    PROC_DIR.mkdir(parents=True, exist_ok=True)
    thresh_df.to_csv(THRESH_FILE, index=False)
    print(f"  SAVED oi_arrow_thresholds.csv  {len(thresh_df):>5} rows")
    for _, r in thresh_df.iterrows():
        print(f"    {r['product']:<6}  median={r['median_pct']:.2f}%  "
              f"p75={r['p75_pct']:.2f}%  (n={int(r['n_obs'])}, since {ARROW_THRESHOLD_START})")


def print_summary(df: pd.DataFrame) -> None:
    print(f"\n{'='*72}")
    print("  Cumulative OI — Latest Total OI per Product")
    print(f"{'='*72}")
    for product in config.OI_CUMULATIVE_RANGE:
        sub = df[df["product"] == product]
        if sub.empty:
            print(f"  {product:<6}  no data")
            continue
        r = sub.sort_values("date").iloc[-1]
        n_contracts = len(config.OI_CUMULATIVE_RICS[product])
        ov_pct = r["oi_vol_pct"]
        ov_str = "n/a" if pd.isna(ov_pct) else f"{ov_pct:+.0f}%"
        flag = "  [FLAG: |OI/Vol|>100%]" if pd.notna(ov_pct) and abs(ov_pct) > 100 else ""
        print(f"  {product:<6}  {r['date'].date()}  Total OI: {r['total_oi']:>12,.0f} "
              f"({r['oi_chg']:+,.0f})  Total Vol: {r['total_volume']:>12,.0f} "
              f"({r['volume_chg']:+,.0f})  OI/Vol: {ov_str:>6}  "
              f"Avg c1-c{PRICE_FRONT_CONTRACTS}: {r['avg_price']:>10,.2f} "
              f"({r['price_chg_pct']:+.2f}%)  [{n_contracts} contracts scanned]{flag}")
    print(f"{'='*72}\n")


def main() -> None:
    print(f"Loading {RAW_FILE.name} for {len(config._OI_CUM_ALL_RICS)} extended-curve RICs...")
    df = build_cumulative()
    print("\nSaving output...")
    save(df)
    print_summary(df)

    print("\nComputing per-product arrow thresholds (median/p75 abs % move, "
          f"since {ARROW_THRESHOLD_START})...")
    thresh_df = build_arrow_thresholds(df)
    save_thresholds(thresh_df)


if __name__ == "__main__":
    main()
