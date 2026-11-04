import sys
from datetime import date, timedelta
from pathlib import Path
import calendar as _cal

import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import config

ANALYSIS_FILE  = ROOT / "data" / "processed" / "oi_analysis.csv"
PROC_DIR       = ROOT / "data" / "processed"

SIGNALS = ["Long Buildup", "Short Buildup", "Short Covering", "Long Unwinding", "Neutral"]
ABBRS   = ["LB", "SB", "SC", "LU", "N"]
SIG_TO_ABBR = dict(zip(SIGNALS, ABBRS))

# ── Roll calendar (calendar-based, no OI-threshold heuristic) ──────────────────

# Rule name → product mapping
_ROLL_RULES = {
    "FCPO": "15th",
    "BO":   "bday_before_15th",
    "S":    "bday_before_15th",
    "SM":   "bday_before_15th",
    "LGO":  "2bdays_before_14th",
    "HO":   "last_bday",
    "CL":   "3bdays_before_25th",
    "LCO":  "last_bday",
    "DCP":  "10th",
    "DBY":  "10th",
}


def _n_prev_bdays(d: date, n: int) -> date:
    """Step back n business days (Mon–Fri) from d."""
    for _ in range(n):
        d -= timedelta(days=1)
        while d.weekday() >= 5:   # 5=Sat, 6=Sun
            d -= timedelta(days=1)
    return d


def _last_bday(y: int, m: int) -> date:
    last = _cal.monthrange(y, m)[1]
    d = date(y, m, last)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def _roll_date_for(rule: str, y: int, m: int) -> date:
    if rule == "15th":
        return date(y, m, 15)
    if rule == "10th":
        return date(y, m, 10)
    if rule == "bday_before_15th":
        return _n_prev_bdays(date(y, m, 15), 1)
    if rule == "2bdays_before_14th":
        return _n_prev_bdays(date(y, m, 14), 2)
    if rule == "last_bday":
        return _last_bday(y, m)
    if rule == "3bdays_before_25th":
        return _n_prev_bdays(date(y, m, 25), 3)
    raise ValueError(f"Unknown roll rule: {rule}")


def _build_roll_calendar(
    date_min: pd.Timestamp, date_max: pd.Timestamp
) -> dict[str, set]:
    """
    Returns {product: set_of_pd.Timestamps} covering every month in
    [date_min, date_max] using the product-specific roll rule.
    """
    months = []
    y, m = date_min.year, date_min.month
    ey, em = date_max.year, date_max.month
    while (y, m) <= (ey, em):
        months.append((y, m))
        m += 1
        if m > 12:
            m, y = 1, y + 1

    roll_by_product: dict[str, set] = {}
    for product, rule in _ROLL_RULES.items():
        s: set = set()
        for y, m in months:
            try:
                s.add(pd.Timestamp(_roll_date_for(rule, y, m)))
            except Exception:
                pass
        roll_by_product[product] = s
    return roll_by_product


# ── Load & roll-mask ───────────────────────────────────────────────────────────

def load_masked() -> pd.DataFrame:
    """
    Load oi_analysis.csv, drop calendar roll dates per product, then
    forward-fill each RIC back to its original date spine so windows
    (15D / 30D) always count the intended number of trading days.
    """
    df = pd.read_csv(ANALYSIS_FILE, parse_dates=["date"])
    df["date"] = pd.to_datetime(df["date"])

    # Capture full date spine per RIC before any masking
    ric_dates: dict[str, list] = {
        ric: sorted(grp["date"]) for ric, grp in df.groupby("RIC")
    }

    date_min = df["date"].min()
    date_max = df["date"].max()
    roll_by_product = _build_roll_calendar(date_min, date_max)

    ric_to_product = {
        ric: product
        for product, rics in config.RICS["futures"].items()
        for ric in rics
    }

    n_before  = len(df)
    roll_mask = pd.Series(False, index=df.index)

    for ric in df["RIC"].unique():
        product  = ric_to_product.get(ric)
        if product is None:
            continue
        roll_set = roll_by_product.get(product, set())
        if not roll_set:
            continue
        roll_mask |= (df["RIC"] == ric) & df["date"].isin(roll_set)

    df        = df[~roll_mask].reset_index(drop=True)
    n_dropped = n_before - len(df)

    # Forward-fill: reindex each RIC back to its original date spine and
    # carry the last known signal forward across any removed roll dates.
    parts = []
    for ric, grp in df.groupby("RIC"):
        grp       = grp.sort_values("date").set_index("date")
        orig_dates = ric_dates.get(ric, [])
        if orig_dates:
            grp           = grp.reindex(orig_dates)
            grp["RIC"]    = ric
            grp["signal"] = grp["signal"].ffill()
        parts.append(grp.reset_index())

    df       = pd.concat(parts, ignore_index=True)
    n_filled = len(df) - (n_before - n_dropped)

    print(f"  {n_before:,} rows loaded  |  {n_dropped:,} roll dates dropped (calendar)"
          f"  |  {n_filled:,} forward-filled  |  {len(df):,} rows retained")

    return df.sort_values(["RIC", "date"]).reset_index(drop=True)


# ── Signal counts ──────────────────────────────────────────────────────────────

def _counts(rows: pd.DataFrame) -> dict[str, int]:
    c = rows["signal"].value_counts()
    return {abbr: int(c.get(sig, 0)) for sig, abbr in SIG_TO_ABBR.items()}


# ── Window computation ─────────────────────────────────────────────────────────

def compute_windows(df: pd.DataFrame) -> pd.DataFrame:
    """
    For each RIC compute signal across 1D / 15D / 30D windows (after roll masking).
    Windows are taken from the tail of the date-sorted, masked series.
    Returns one row per RIC.
    """
    records = []

    for ric, grp in df.groupby("RIC", sort=False):
        grp = grp.sort_values("date")

        sig_1d = grp.iloc[-1]["signal"] if len(grp) >= 1 else "n/a"

        c15 = _counts(grp.tail(15))
        c30 = _counts(grp.tail(30))

        row = {"RIC": ric, "signal_1d": sig_1d}
        for abbr in ABBRS:
            row[f"{abbr}_15d"] = c15[abbr]
            row[f"{abbr}_30d"] = c30[abbr]
        records.append(row)

    col_order = (["RIC", "signal_1d"]
                 + [f"{a}_15d" for a in ABBRS]
                 + [f"{a}_30d" for a in ABBRS])
    return pd.DataFrame(records, columns=col_order)


# ── Save ───────────────────────────────────────────────────────────────────────

def save(summary: pd.DataFrame) -> None:
    PROC_DIR.mkdir(parents=True, exist_ok=True)

    ric_to_product = {
        ric: product
        for product, rics in config.RICS["futures"].items()
        for ric in rics
    }
    out = summary.copy()
    out.insert(1, "product", out["RIC"].map(ric_to_product))
    out.to_csv(PROC_DIR / "oi_summary.csv", index=False)
    print(f"  SAVED oi_summary.csv    {len(out):>5} rows")


# ── Terminal output ────────────────────────────────────────────────────────────

def print_table(summary: pd.DataFrame) -> None:
    W_RIC = 10   # e.g. "FCPOc1"
    W_SIG = 16   # e.g. "Short Covering"
    W_C   = 3    # count columns (0-30, 2 digits max)

    cnt_block = "  ".join(f"{a:>{W_C}}" for a in ABBRS)   # " LB   SB   SC   LU   N"

    # Two-row header: row 1 shows window labels, row 2 shows column names
    sig_w = W_RIC + 2 + W_SIG          # width of the RIC + Signal prefix
    blk_w = len(cnt_block)             # width of one count block

    hdr1 = (f"{'':>{sig_w + 2 + W_SIG}}"
            f"  {'15D':^{blk_w}}"
            f"    {'30D':^{blk_w}}")
    hdr2 = (f"  {'RIC':<{W_RIC}}  {'Signal':<{W_SIG}}"
            f"  {cnt_block}"
            f"    {cnt_block}")
    sep  = "-" * len(hdr2)

    print(f"\n{'='*72}")
    print(f"  OI Signal Summary  (roll dates excluded by calendar)")
    print(f"{'='*72}")

    idx = summary.set_index("RIC")

    for product, rics in config.RICS["futures"].items():
        present = [r for r in rics if r in idx.index]
        if not present:
            continue

        print(f"\n  {product}")
        print(f"  {hdr1}")
        print(f"  {hdr2}")
        print(f"  {sep}")

        for ric in present:
            r     = idx.loc[ric]
            sig   = r["signal_1d"]
            c15   = [r[f"{a}_15d"] for a in ABBRS]
            c30   = [r[f"{a}_30d"] for a in ABBRS]

            line = (f"  {ric:<{W_RIC}}  {sig:<{W_SIG}}"
                    + "  " + "  ".join(f"{v:>{W_C}}" for v in c15)
                    + "    " + "  ".join(f"{v:>{W_C}}" for v in c30))
            print(line)

    print(f"\n{'='*72}\n")


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    print(f"Loading {ANALYSIS_FILE.name}...")
    df = load_masked()

    print("\nComputing signal windows (1D / 15D / 30D)...")
    summary = compute_windows(df)

    print("\nSaving output...")
    save(summary)

    print_table(summary)


if __name__ == "__main__":
    main()
