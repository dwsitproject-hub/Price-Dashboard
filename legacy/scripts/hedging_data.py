"""
Dashboard adapter for CashBookHedging.

Reads CashBookHedging/cache/prices.parquet (shared cache, refreshed by
run_daily.bat) and re-uses the hedging module for betas. Exposes:

  get_prices_wide()               -> wide USD/MT DataFrame (Date × RIC)
  load_snapshots()                -> (snap, waste_snaps, prices)
  rolling_beta_series(prices,ric,w) -> rolling beta Series
  HEDGE_PRODUCTS                  -> {display_name: {"MY": base, "ID": base}}
"""

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_CBH = Path(__file__).resolve().parent.parent.parent / "CashBookHedging"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {name} from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# CashBookHedging's hedging.py does `import config`. The dashboard also has a
# `config` module on sys.path. Temporarily bind sys.modules["config"] to the
# CashBookHedging config while we load its sibling modules, then restore.
_cbh_config = _load_module("_cbh_config", _CBH / "config.py")

_saved_config = sys.modules.get("config")
sys.modules["config"] = _cbh_config
try:
    _cbh_hedging = _load_module("_cbh_hedging",      _CBH / "hedging.py")
    _cbh_waste   = _load_module("_cbh_waste_loader", _CBH / "waste_loader.py")
finally:
    if _saved_config is not None:
        sys.modules["config"] = _saved_config
    else:
        sys.modules.pop("config", None)

compute_rolling_betas   = _cbh_hedging.compute_rolling_betas
compute_all_snapshots   = _cbh_hedging.compute_all_snapshots
compute_waste_snapshots = _cbh_hedging.compute_waste_snapshots
load_argus = _cbh_waste.load_argus
load_ptp   = _cbh_waste.load_ptp

_DASH_ROOT    = Path(__file__).resolve().parent.parent
RAW_CSV_PATH  = _DASH_ROOT / "data" / "raw" / "all_instruments_raw.csv"
FUTURES_RIC   = _cbh_config.FUTURES_RIC
FX_RIC        = _cbh_config.FX_RIC
PRODUCTS      = _cbh_config.PRODUCTS
MYR_CASH_RICS = _cbh_config.MYR_CASH_RICS
_ALL_RICS     = [FUTURES_RIC, FX_RIC] + [
    ric for p in PRODUCTS.values() for ric in (p["M1"], p["M2"])
]

# Product groupings for the rolling-graph product picker.
# Each entry maps a display name to the Malaysia/Indonesia RIC stems
# (append "-M1" or "-M2" to form a full RIC).
HEDGE_PRODUCTS: dict[str, dict[str, str | None]] = {
    "CPO":      {"MY": "CPO-MYSTH",  "ID": "CPO-ID"},
    "Olein":    {"MY": "POL-MYRBD",  "ID": "POL-IDRBD"},
    "Palm Oil": {"MY": "PO-MYRBD",   "ID": "PO-IDRBD"},
    "Stearin":  {"MY": "PSTR-MYRBD", "ID": "PSTR-IDRBD"},
    "PFAD":     {"MY": "PFAD-MY",    "ID": None},
}

_PRICES_CACHE: dict[str, pd.DataFrame] = {}


def get_prices_wide(force_reload: bool = False) -> pd.DataFrame:
    """Read the dashboard's all_instruments_raw.csv (refreshed daily) and
    return a wide USD/MT DataFrame for FCPOc3 + all cash M1/M2 RICs.

    Conversion mirrors CashBookHedging/data_manager.get_prices():
    FCPOc3 and CPO-MYSTH M1/M2 are divided by MYR= to get USD/MT; everything
    else is already USD/MT. MYR= itself is kept for reference.
    """
    if not force_reload and "wide" in _PRICES_CACHE:
        return _PRICES_CACHE["wide"]

    if not RAW_CSV_PATH.exists():
        raise FileNotFoundError(
            f"all_instruments_raw.csv not found: {RAW_CSV_PATH}"
        )

    df = pd.read_csv(
        RAW_CSV_PATH, parse_dates=["Trade Date"],
        usecols=["Trade Date", "RIC", "Last", "Settlement Price", "Bid", "Ask"],
    )
    df = df[df["RIC"].isin(_ALL_RICS)].copy()
    for col in ("Last", "Settlement Price", "Bid", "Ask"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    mid = (df["Bid"] + df["Ask"]) / 2.0
    # Priority: Settlement → Last → mid(Bid, Ask) → Bid → Ask (matches
    # CashBookHedging/lseg_client.py fallback chain — needed because FX
    # series like MYR= only publish Bid/Ask).
    df["price"] = (
        df["Settlement Price"]
          .fillna(df["Last"])
          .fillna(mid)
          .fillna(df["Bid"])
          .fillna(df["Ask"])
    )
    df = df.dropna(subset=["price"])
    df = df.drop_duplicates(subset=["Trade Date", "RIC"], keep="last")

    wide = (df.pivot(index="Trade Date", columns="RIC", values="price")
              .sort_index()
              .ffill(limit=3))
    wide.index.name = "Date"

    if FX_RIC in wide.columns:
        myr = wide[FX_RIC]
        for ric in [FUTURES_RIC] + MYR_CASH_RICS:
            if ric in wide.columns:
                wide[ric] = wide[ric] / myr

    _PRICES_CACHE["wide"] = wide
    return wide


def load_snapshots() -> tuple[pd.DataFrame, pd.DataFrame | None, pd.DataFrame]:
    """Return (cash_snap, waste_snap_or_None, prices_wide)."""
    prices = get_prices_wide()
    snap   = compute_all_snapshots(prices)

    waste_snaps = None
    waste_dir = _CBH / "Waste"
    argus_path = waste_dir / "Argus Historical Prices.xlsx"
    ptp_path   = waste_dir / "PTP.xlsx"
    try:
        if argus_path.exists() and ptp_path.exists():
            waste_snaps = compute_waste_snapshots(
                {
                    "RPOME (Argus)":  load_argus(str(argus_path)),
                    "CPO-KPB (PTP)":  load_ptp(str(ptp_path)),
                },
                fcpoc3_usd=prices[FUTURES_RIC],
            )
    except Exception as e:
        print(f"  [hedging_data] Waste load failed: {e}")

    return snap, waste_snaps, prices


def rolling_beta_series(prices: pd.DataFrame, cash_ric: str,
                        window: int) -> pd.Series:
    """Full rolling beta time-series for one cash RIC vs FCPOc3."""
    if cash_ric not in prices.columns or FUTURES_RIC not in prices.columns:
        return pd.Series(dtype=float)
    return compute_rolling_betas(prices[cash_ric],
                                 prices[FUTURES_RIC], window)
