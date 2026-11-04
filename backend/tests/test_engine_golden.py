"""Golden tests: the new engine must reproduce the legacy pipeline exactly on identical input.

Runs legacy/scripts/process.py (calc_spreads) and legacy/scripts/oi_cumulative.py (build_cumulative)
side by side with app.engine on the same synthetic raw data.
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import date

import numpy as np
import pandas as pd
import pytest

from conftest import REPO

LEGACY = REPO / "legacy"


def _load_legacy(name: str):
    sys.path.insert(0, str(LEGACY))
    spec = importlib.util.spec_from_file_location(f"legacy_{name}", LEGACY / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def raw_rows():
    from app.seed.demo_data import generate
    df = generate(date(2017, 1, 2), date(2026, 10, 30))
    return df[pd.to_datetime(df["Trade Date"]) >= "2024-01-01"].reset_index(drop=True)


def _legacy_long(raw: pd.DataFrame, fx_rics: set[str]) -> pd.DataFrame:
    """Legacy load_prices semantics (FX mid, else Last→Settlement)."""
    df = raw.rename(columns={"Trade Date": "date"}).copy()
    df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    last = pd.to_numeric(df["Last"], errors="coerce")
    sett = pd.to_numeric(df["Settlement Price"], errors="coerce")
    mid = (pd.to_numeric(df["Bid"], errors="coerce") + pd.to_numeric(df["Ask"], errors="coerce")) / 2
    df["price"] = np.where(df["RIC"].isin(fx_rics), mid, last.fillna(sett))
    return df[["date", "RIC", "price"]].drop_duplicates(subset=["date", "RIC"])


def test_spreads_match_legacy(raw_rows, tmp_path):
    legacy = _load_legacy("process")
    legacy.PROC_DIR = tmp_path
    long_legacy = _legacy_long(raw_rows, legacy.config.FX_RICS)
    wide = legacy.convert_currencies(legacy.to_wide(long_legacy)).sort_index().ffill()
    wide_raw = legacy.to_wide(long_legacy).sort_index().ffill()
    legacy.calc_spreads(wide, wide_raw)

    from app.engine.conversions import build_frames
    from app.engine.spreads import compute_all
    long_new = long_legacy.rename(columns={"RIC": "ric"})
    new = compute_all(build_frames(long_new))

    compared = 0
    for csv in sorted(tmp_path.glob("*.csv")):
        key = csv.stem
        if key not in new:
            continue  # legacy-only intermediates (e.g. CPO-MYFOB-M1) are not displayed series
        old = pd.read_csv(csv, parse_dates=["date"]).set_index("date")["spread_value"]
        cur = new[key]["value"]
        cur.index = pd.to_datetime(cur.index)
        assert set(old.index) == set(cur.index), f"{key}: date sets differ"
        diff = (old - cur.reindex(old.index)).abs().max()
        assert diff < 1e-6, f"{key}: max abs diff {diff}"
        compared += 1
    assert compared >= 120, f"only {compared} series compared"


def test_oi_cumulative_matches_legacy(raw_rows):
    legacy = _load_legacy("oi_cumulative")
    raw = raw_rows.rename(columns={"Trade Date": "date"}).copy()
    raw["date"] = pd.to_datetime(raw["date"])
    raw = raw[raw["RIC"].isin(legacy.config._OI_CUM_ALL_RICS)]
    raw["price"] = pd.to_numeric(raw["Last"], errors="coerce").fillna(pd.to_numeric(raw["Settlement Price"],
                                                                                    errors="coerce"))
    raw["oi"] = pd.to_numeric(raw["Open Interest"], errors="coerce")
    raw["volume"] = pd.to_numeric(raw["Volume"], errors="coerce")
    legacy_input = raw[["date", "RIC", "price", "oi", "volume"]].sort_values(["RIC", "date"])
    legacy._load_raw = lambda: legacy_input.reset_index(drop=True)
    old = legacy.build_cumulative()

    from app.engine.oi import build_cumulative
    new = build_cumulative(legacy_input.rename(columns={"RIC": "ric"}))
    for product in old["product"].unique():
        o = old[old["product"] == product].set_index("date")
        n = new[new["product_code"] == product].set_index("date")
        assert list(o.index) == list(n.index), product
        for col in ["total_oi", "oi_chg", "total_volume", "volume_chg", "oi_vol_pct", "avg_price", "price_chg_pct"]:
            pd.testing.assert_series_equal(o[col], n[col], check_names=False, atol=1e-9)


def test_conversion_factors():
    from app.catalog.instruments import instrument_conversion
    assert instrument_conversion("BOc3")[:2] == ("factor", 22.0462)
    assert instrument_conversion("Sc1")[1] == 0.3674
    assert instrument_conversion("SMc1")[1] == 1.1023
    assert instrument_conversion("HOc2")[1] == 264.172
    assert instrument_conversion("CLc7")[1] == 6.2898
    assert instrument_conversion("FCPOc12")[:3] == ("fx_div", None, "MYR=")
    assert instrument_conversion("DBYc1")[2] == "CNY="
    assert instrument_conversion("CPO-MYSTH-M2")[2] == "MYR="
    assert instrument_conversion("LGOc3")[0] == "none"


def test_universe_matches_legacy_config():
    legacy_cfg = _load_legacy("process").config
    from app.catalog.instruments import all_rics
    assert set(all_rics()) == set(legacy_cfg.ALL_RICS)
