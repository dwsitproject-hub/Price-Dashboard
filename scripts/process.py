import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import config

RAW_FILE   = ROOT / "data" / "raw"  / "all_instruments_raw.csv"
PROC_DIR   = ROOT / "data" / "processed"


# ── Step 1: Load & resolve price per row ──────────────────────────────────────

def load_prices() -> pd.DataFrame:
    """
    Load raw CSV and resolve a single 'price' column per (date, RIC) row.
    - FX RICs  → mid = (Bid + Ask) / 2
    - All others → Last, fallback to Settlement Price
    Returns a DataFrame with columns: [date, RIC, price]
    """
    df = pd.read_csv(RAW_FILE, parse_dates=["Trade Date"])
    df = df.rename(columns={"Trade Date": "date"})
    df["date"] = pd.to_datetime(df["date"]).dt.normalize()

    prices = []
    for _, row in df.iterrows():
        ric = row["RIC"]
        if ric in config.FX_RICS:
            bid = pd.to_numeric(row.get("Bid"), errors="coerce")
            ask = pd.to_numeric(row.get("Ask"),  errors="coerce")
            price = (bid + ask) / 2 if pd.notna(bid) and pd.notna(ask) else None
        else:
            last = pd.to_numeric(row.get("Last"), errors="coerce")
            sett = pd.to_numeric(row.get("Settlement Price"), errors="coerce")
            price = last if pd.notna(last) else sett
        prices.append({"date": row["date"], "RIC": ric, "price": price})

    result = pd.DataFrame(prices)
    # Drop duplicate (date, RIC) pairs — keep first
    result = result.drop_duplicates(subset=["date", "RIC"])
    return result


# ── Step 2: Pivot to wide format ──────────────────────────────────────────────

def to_wide(long_df: pd.DataFrame) -> pd.DataFrame:
    """
    Pivot long (date, RIC, price) → wide (date × RIC).
    Forward-fill FX to cover weekends/holidays.
    """
    wide = long_df.pivot(index="date", columns="RIC", values="price")
    wide = wide.sort_index()

    # Forward-fill FX rates across all dates (they quote on weekends too,
    # but futures don't trade — ffill ensures every trading day has a rate)
    for ric in config.FX_RICS:
        if ric in wide.columns:
            wide[ric] = wide[ric].ffill()

    return wide


# ── Step 3: Currency conversion (MYR → USD/MT) ───────────────────────────────

def convert_currencies(wide: pd.DataFrame) -> pd.DataFrame:
    """
    Apply all unit conversions before spread calculation:
      - MYR-denominated RICs (FCPO, CPO-MYSTH) → divide by MYR= mid → USD/MT
      - CNY-denominated RICs (DCP, DBY)         → divide by CNY= mid → USD/MT
      - BO (cents/lb)                            → multiply by 22.0462 → USD/MT
    """
    # MYR → USD/MT
    if "MYR=" in wide.columns:
        myr = wide["MYR="]
        for ric in config.MYR_RICS:
            if ric in wide.columns:
                wide[ric] = wide[ric] / myr
    else:
        print("WARNING: MYR= not found — skipping MYR conversion.")

    # CNY → USD/MT
    if "CNY=" in wide.columns:
        cny = wide["CNY="]
        for ric in config.CNY_RICS:
            if ric in wide.columns:
                wide[ric] = wide[ric] / cny
    else:
        print("WARNING: CNY= not found — skipping CNY conversion.")

    # cents/lb → USD/MT  (1 MT = 2204.62 lbs, so ÷100 × 2204.62 = × 22.0462)
    for ric in config.CENTSLB_RICS:
        if ric in wide.columns:
            wide[ric] = wide[ric] * 22.0462

    # Soybeans: cents/bushel → USD/MT
    for ric in config.S_RICS:
        if ric in wide.columns:
            wide[ric] = wide[ric] * config.S_CONV

    # Soybean Meal: USD/short ton → USD/MT
    for ric in config.SM_RICS:
        if ric in wide.columns:
            wide[ric] = wide[ric] * config.SM_CONV

    # Heating Oil: USD/gallon → USD/MT
    for ric in config.HO_RICS:
        if ric in wide.columns:
            wide[ric] = wide[ric] * config.HO_CONV

    # WTI: USD/barrel → USD/MT
    for ric in config.CL_RICS:
        if ric in wide.columns:
            wide[ric] = wide[ric] * config.CL_CONV

    # Brent: USD/barrel → USD/MT
    for ric in config.LCO_RICS:
        if ric in wide.columns:
            wide[ric] = wide[ric] * config.LCO_CONV

    return wide


# ── Step 4: Spread calculations ───────────────────────────────────────────────

def _save_spread(name: str, series: pd.Series) -> None:
    series = series.dropna()
    series = series[series.index.notna()]   # drop NaT-dated rows (blank date in raw pull)
    if series.empty:
        print(f"  SKIP  {name:<30} (no overlapping data)")
        return
    out = series.reset_index()
    out.columns = ["date", "spread_value"]
    out["date"] = out["date"].dt.strftime("%Y-%m-%d")
    out.to_csv(PROC_DIR / f"{name}.csv", index=False)
    print(f"  SAVED {name:<30} {len(out):>5} rows  "
          f"{out['date'].iloc[0]} to {out['date'].iloc[-1]}")


def calc_spreads(wide: pd.DataFrame, wide_raw: pd.DataFrame) -> None:
    PROC_DIR.mkdir(parents=True, exist_ok=True)

    # Helper: safe column lookup
    def col(ric: str) -> pd.Series:
        return wide[ric] if ric in wide.columns else pd.Series(dtype=float)

    # Build {product: {"c1": ric, "c2": ric, ...}} lookup from RIC suffix.
    # Tolerates variable-length lists (c1..c3,c5,c7 or full c1..c7).
    futures_by_contract = {
        product: {ric[len(product):]: ric for ric in rics}
        for product, rics in config.RICS["futures"].items()
    }

    # ── Section 1: Calendar spreads ───────────────────────────────────────────
    print("\n--- Section 1: Futures Calendar Spreads ---")
    # Synthetic FOB Malaysia CPO (USD/MT) = (Local Malaysia PK/PG CPO + 10) * 1.10 export duty.
    # `wide` is already USD/MT here (CPO-MYSTH is in MYR_RICS).
    for _m in ("M1", "M2"):
        _loc = f"CPO-MYSTH-{_m}"
        if _loc in wide.columns:
            wide[f"CPO-MYFOB-{_m}"] = (wide[_loc] + config.CPO_FOB_PREM_USD) * config.CPO_FOB_DUTY_MULT
    CALENDAR = [("c1", "c3"), ("c3", "c5"), ("c5", "c7")]

    for product, ric_by_contract in futures_by_contract.items():
        for leg_a, leg_b in CALENDAR:
            name = f"{product}_{leg_a}{leg_b}"
            spread = col(ric_by_contract[leg_a]) - col(ric_by_contract[leg_b])
            _save_spread(name, spread)

    # ── Section 2: Product spreads (c1, c2, c3, c5, c7) ────────────────────
    print("\n--- Section 2: Futures Product Spreads ---")
    PRODUCT_SPREAD_DEFS = [
        ("BO",   "FCPO",  "BOPO"),
        ("BO",   "LGO",   "BOGO"),
        ("FCPO", "LGO",   "POGO"),
        ("DCP",  "FCPO",  "DCP-FCPO"),
        ("DCP",  "BO",    "DCP-BO"),
        ("DCP",  "LGO",   "DCP-LGO"),
        ("DBY",  "FCPO",  "DBY-FCPO"),
        ("DBY",  "BO",    "DBY-BO"),
        ("DBY",  "LGO",   "DBY-LGO"),
        ("DCP",  "DBY",   "DCP-DBY"),
        ("SM",   "FCPO",  "SM-PO"),
        ("BO",   "HO",    "BOHO"),
        ("LCO",  "CL",    "Brent-WTI"),
        ("HO",   "LGO",   "HO-LGO"),
    ]
    PS_CONTRACTS = ["c1", "c2", "c3", "c5", "c7"]
    for prod_a, prod_b, label in PRODUCT_SPREAD_DEFS:
        a_by_contract = futures_by_contract[prod_a]
        b_by_contract = futures_by_contract[prod_b]
        for contract in PS_CONTRACTS:
            _save_spread(
                f"{label}_{contract}",
                col(a_by_contract[contract]) - col(b_by_contract[contract]),
            )

    # ── Section 3: Malaysia cash premium vs FCPOc3 ───────────────────────────
    print("\n--- Section 3: Malaysia Cash Premium vs FCPOc3 ---")
    fcpo_c3 = futures_by_contract["FCPO"]["c3"]
    MAL_PREM = [
        ("CPO-MYSTH-M2",  "MAL_PREM_CPO"),
        ("POL-MYRBD-M2",  "MAL_PREM_POL"),
        ("PO-MYRBD-M2",   "MAL_PREM_PO"),
        ("PSTR-MYRBD-M2", "MAL_PREM_PSTR"),
        ("PFAD-MY-M2",    "MAL_PREM_PFAD"),
    ]
    for ric, name in MAL_PREM:
        _save_spread(name, col(ric) - col(fcpo_c3))
    _save_spread("MAL_PREM_CPO_FOB", col("CPO-MYFOB-M2") - col(fcpo_c3))

    # ── Section 4: Indonesia cash premium vs FCPOc3 ──────────────────────────
    print("\n--- Section 4: Indonesia Cash Premium vs FCPOc3 ---")
    IDO_PREM = [
        ("CPO-ID-M2",    "IDO_PREM_CPO"),
        ("POL-IDRBD-M2", "IDO_PREM_POL"),
        ("PO-IDRBD-M2",  "IDO_PREM_PO"),
        ("PSTR-IDRBD-M2","IDO_PREM_PSTR"),
    ]
    for ric, name in IDO_PREM:
        _save_spread(name, col(ric) - col(fcpo_c3))

    # ── Section 5: Malindo spreads (Malaysia minus Indonesia) ─────────────────
    print("\n--- Section 5: Malindo Spreads (Malaysia - Indonesia) ---")
    MALINDO = [
        ("CPO-MYSTH-M1",  "CPO-ID-M1",    "MALINDO_CPO"),
        ("POL-MYRBD-M1",  "POL-IDRBD-M1", "MALINDO_POL"),
        ("PO-MYRBD-M1",   "PO-IDRBD-M1",  "MALINDO_PO"),
        ("PSTR-MYRBD-M1", "PSTR-IDRBD-M1","MALINDO_PSTR"),
    ]
    for ric_mal, ric_ido, name in MALINDO:
        _save_spread(name, col(ric_mal) - col(ric_ido))
    _save_spread("MALINDO_CPO_FOB", col("CPO-MYFOB-M1") - col("CPO-ID-M1"))

    # ── Section 6: Cash product spreads (already in USD/MT) ───────────────────
    print("\n--- Section 6: Cash Product Spreads ---")
    CASH_PROD = [
        ("POL-MYRBD-M2", "PSTR-MYRBD-M2", "MAL_PROD_OLEIN_STEARIN"),
        ("POL-MYRBD-M2", "PFAD-MY-M2",    "MAL_PROD_OLEIN_PFAD"),
        ("POL-IDRBD-M2", "PSTR-IDRBD-M2", "IDO_PROD_OLEIN_STEARIN"),
    ]
    for ric_a, ric_b, name in CASH_PROD:
        _save_spread(name, col(ric_a) - col(ric_b))

    # ── Section 7: Soybean Crush Spread ($/bushel) ─────────────────────────────
    print("\n--- Section 7: Soybean Crush Spread ($/bushel) ---")
    # Crush = SM($/short ton)*0.022 + BO(cents/lb)*0.11 - S(cents/bushel)/100
    # Uses raw (unconverted) prices in local units
    def raw_col(ric: str) -> pd.Series:
        return wide_raw[ric] if ric in wide_raw.columns else pd.Series(dtype=float)

    bo_c3 = futures_by_contract["BO"]["c3"]
    sm_c3 = futures_by_contract["SM"]["c3"]
    s_c3  = futures_by_contract["S"]["c3"]
    bo_c1 = futures_by_contract["BO"]["c1"]
    sm_c1 = futures_by_contract["SM"]["c1"]
    s_c1  = futures_by_contract["S"]["c1"]
    _save_spread("CRUSH_c3", raw_col(sm_c3) * 0.022 + raw_col(bo_c3) * 0.11 - raw_col(s_c3) / 100)
    _save_spread("CRUSH_c1", raw_col(sm_c1) * 0.022 + raw_col(bo_c1) * 0.11 - raw_col(s_c1) / 100)

    # ── Section 7b: South America Crush Spread (USD/MT) ──────────────────────
    print("\n--- Section 7b: South America Crush Spread (USD/MT) ---")
    # Argentina Crush ($/bushel) = ((Meal_AR × 0.790) + (Oil_AR × 0.185) − Bean_AR) × 0.0272155
    _save_spread("SA_CRUSH",
                 (col("SM-ARGUPR-P1") * 0.790 + col("BO-ARGUPR-C1") * 0.185 - col("S-ARGUPR-C1")) * 0.0272155)

    # ── Section 8: US Cash Premium/Basis ─────────────────────────────────────
    print("\n--- Section 8: US Cash Premium/Basis ---")
    _save_spread("US_BASIS_SOY_OIL", col("SOY-CTILL-US"))

    # ── Section 9: South America Cash Premium/Basis ──────────────────────────
    print("\n--- Section 9: South America Cash Premium/Basis ---")
    # SA BO c1 = BO-ARGUPR-C1 - BOc1
    _save_spread("SA_PREM_BO", col("BO-ARGUPR-C1") - col(futures_by_contract["BO"]["c1"]))
    # SA Soybean basis = S-ARGUPR-C1 - Sc1
    _save_spread("SA_BASIS_SOY", col("S-ARGUPR-C1") - col(futures_by_contract["S"]["c1"]))
    # SA Soybean Meal basis = SM-ARGUPR-P1 - SMc1
    _save_spread("SA_BASIS_SM", col("SM-ARGUPR-P1") - col(futures_by_contract["SM"]["c1"]))
    # SA Soybean Oil vs MDEX Palm = BO-ARGUPR-C1 - FCPOc1 (both USD/MT)
    _save_spread("SA_ARGSBO_FCPO", col("BO-ARGUPR-C1") - col(futures_by_contract["FCPO"]["c1"]))

    # ── Section 10: China Cash Premium/Basis ─────────────────────────────────
    print("\n--- Section 10: China Cash Premium/Basis ---")
    # China Soybean Oil = JCI-SOA-DALN - DBYc1 (both converted to USD/MT)
    _save_spread("CN_BASIS_SOY_OIL", col("JCI-SOA-DALN") - col(futures_by_contract["DBY"]["c1"]))

    # ── Section 11: Cross-Geography Product Spreads ──────────────────────────
    print("\n--- Section 11: Cross-Geography Product Spreads ---")
    _save_spread("SA-CN_BO",
                 col("BO-ARGUPR-C1") - col("JCI-SOA-DALN"))
    _save_spread("SA-SUN_BO",
                 col("BO-ARGUPR-C1") - col("KTS-SOUABS-FOB"))
    _save_spread("CPO-SUN",
                 col("CPO-MYSTH-M1") - col("KTS-SOUABS-FOB"))
    _save_spread("SA-CPO_BO",
                 col("BO-ARGUPR-C1") - col("CPO-MYSTH-M1"))
    _save_spread("CPOFOB-SUN",
                 col("CPO-MYFOB-M1") - col("KTS-SOUABS-FOB"))
    _save_spread("SA-CPOFOB_BO",
                 col("BO-ARGUPR-C1") - col("CPO-MYFOB-M1"))

    # Synthetic FOB CPO outrights + M1-M2 calendar (USD/MT)
    for _m in ("M1", "M2"):
        _save_spread(f"CPO-MYFOB-{_m}", col(f"CPO-MYFOB-{_m}"))
    _save_spread("CPO-MYFOB_M1M2", col("CPO-MYFOB-M1") - col("CPO-MYFOB-M2"))

    # ── Section 12: Leading Signals — Soybean Oilshare ────────────────────────
    print("\n--- Section 12: Oilshare ---")
    # CME's "Oilshare": % of combined oil+meal crush revenue driven by oil, using
    # the standard 60lb-bushel yields (11lb oil, 44lb meal — the same factors as
    # the CRUSH_c1 spread above). BO (cents/lb) -> $/11lb (x0.11),
    # SM ($/short ton) -> $/44lb (x0.022). Uses raw (unconverted) prices.
    # NOTE: matches CME's published methodology — the ~5lb/bushel of hulls/waste
    # (60 - 11 - 44) is NOT priced separately and is excluded from the split.
    oil_value_c1  = raw_col(bo_c1) * 0.11
    meal_value_c1 = raw_col(sm_c1) * 0.022
    _save_spread("OIL_SHARE_c1", oil_value_c1 / (oil_value_c1 + meal_value_c1) * 100)

    # South America (Argentina Up River FOB) equivalent, using the same physical
    # extraction yields as SA_CRUSH above (18.5% oil, 79.0% meal by weight — already
    # USD/MT, no unit conversion needed).
    sa_oil_value  = col("BO-ARGUPR-C1") * 0.185
    sa_meal_value = col("SM-ARGUPR-P1") * 0.790
    _save_spread("SA_OIL_SHARE", sa_oil_value / (sa_oil_value + sa_meal_value) * 100)


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    print(f"Loading {RAW_FILE.name}...")
    long_df = load_prices()
    print(f"  {len(long_df):,} rows across {long_df['RIC'].nunique()} RICs")

    print("\nPivoting to wide format...")
    wide = to_wide(long_df)
    print(f"  Shape: {wide.shape[0]} dates x {wide.shape[1]} RICs")

    print("\nApplying currency conversions (MYR/CNY/cents-lb -> USD/MT)...")
    myr_converted  = [r for r in config.MYR_RICS      if r in wide.columns]
    cny_converted  = [r for r in config.CNY_RICS      if r in wide.columns]
    clb_converted  = [r for r in config.CENTSLB_RICS  if r in wide.columns]
    s_converted    = [r for r in config.S_RICS         if r in wide.columns]
    sm_converted   = [r for r in config.SM_RICS        if r in wide.columns]
    ho_converted   = [r for r in config.HO_RICS        if r in wide.columns]
    cl_converted   = [r for r in config.CL_RICS        if r in wide.columns]
    lco_converted  = [r for r in config.LCO_RICS       if r in wide.columns]
    print(f"  MYR->USD : {', '.join(myr_converted)}")
    print(f"  CNY->USD : {', '.join(cny_converted)}")
    print(f"  c/lb->USD: {', '.join(clb_converted)}")
    print(f"  S c/bu->USD: {', '.join(s_converted)}")
    print(f"  SM t->USD  : {', '.join(sm_converted)}")
    print(f"  HO gal->USD: {', '.join(ho_converted)}")
    print(f"  CL bbl->USD: {', '.join(cl_converted)}")
    print(f"  LCO bbl->USD: {', '.join(lco_converted)}")
    wide = convert_currencies(wide)

    print("\nForward-filling wide DataFrame (carry last price across holidays)...")
    wide = wide.sort_index()
    wide = wide.ffill()

    # Keep a raw (unconverted) copy for crush spread in local units
    wide_raw = to_wide(long_df).sort_index().ffill()

    print("\nCalculating spreads...")
    calc_spreads(wide, wide_raw)

    saved = list(PROC_DIR.glob("*.csv"))
    print(f"\nDone. {len(saved)} spread files written to data/processed/")


if __name__ == "__main__":
    main()