"""Google Sheet sources: Argus / Tradable Prem / India / China parsers against the legacy code, the refresh job,
series-write scoping, the Waste/Bio and parity APIs, and the admin routes. No network: grids are synthetic.
"""
from __future__ import annotations

import csv
import importlib.util
import io
import json
import sys
import uuid
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import text

from app.core.db import get_engine

LEGACY = Path(__file__).resolve().parents[2] / "legacy"

# Header row of the Argus "Prices" tab as of 2026-11-11 (columns 1–75).
ARGUS_HEADERS = [
    "UCOME fob Strait of Malacca prompt, Singapore close, midpoint, USD/t, fob",
    "UCOME fob ARA range swap month 1, London close, midpoint, USD/t, fob",
    "UCOME fob ARA range swap month 2, London close, midpoint, USD/t, fob",
    "UCOME fob ARA range swap month 3, London close, midpoint, USD/t, fob",
    "HVO (hydrotreated vegetable oil) (Class II) fob ARA range swap month 1, London close, midpoint, USD/t, fob",
    "HVO (hydrotreated vegetable oil) (Class II) fob ARA range swap month 2, London close, midpoint, USD/t, fob",
    "HVO (hydrotreated vegetable oil) (Class II) fob ARA range swap month 3, London close, midpoint, USD/t, fob",
    "HVO (hydrotreated vegetable oil) fob ARA range (Class II) USD/t prompt, London close, midpoint, USD/t, fob",
    "HVO (hydrotreated vegetable oil) (Class II) fob ARA range diff to Ice gasoil month 1, London close, midpoint, USD/t, fob",
    "RED SAF (HEFA-SPK) fob ARA range diff to Ice gasoil month 1, London close, midpoint, USD/t, fob",
    "RED SAF (HEFA-SPK) fob ARA range swap month 1, London close, midpoint, USD/t, fob",
    "RED SAF (HEFA-SPK) fob ARA range swap month 2, London close, midpoint, USD/t, fob",
    "RED SAF (HEFA-SPK) fob ARA range swap month 3, London close, midpoint, USD/t, fob",
    "Biodiesel FAME 0C CFPP RED swap month 1, London close, midpoint, USD/t, barge",
    "Biodiesel FAME 0C CFPP RED swap month 2, London close, midpoint, USD/t, barge",
    "Biodiesel FAME 0C CFPP RED swap month 3, London close, midpoint, USD/t, barge",
    "Biodiesel FAME -10C CFPP RED month 1, London close, midpoint, USD/t, barge",
    "Biodiesel FAME -10C CFPP RED month 2, London close, midpoint, USD/t, barge",
    "Biodiesel FAME -10C CFPP RED month 3, London close, midpoint, USD/t, barge",
    "Biodiesel FAME -10C CFPP RED ARA range barge fob diff to Gasoil 7-28 day prompt, London close, diff midpoint, USD/t, barge",
    "Biodiesel FAME 0C CFPP RED ARA range barge fob diff to Gasoil 7-28 day prompt, London close, diff midpoint, USD/t, barge",
    "Biodiesel Advanced FAME 0C CFPP RED ARA range barge fob prompt, London close, midpoint, USD/t, barge",
    "Biodiesel UCOME (used cooking oil) RED ARA range barge fob prompt, London close, midpoint, USD/t, barge",
    "Rapeseed oil fob Dutch Mill RSO prompt, London close, midpoint, Euro/t, fob",
    "Biodiesel Rapeseed OME RED ARA range barge fob diff to Gasoil 7-28 day prompt, London close, diff midpoint, USD/t, barge",
    "Biodiesel Rapeseed OME RED swap month 1, London close, midpoint, USD/t, barge",
    "Biodiesel Rapeseed OME RED swap month 2, London close, midpoint, USD/t, barge",
    "Biodiesel Rapeseed OME RED swap month 3, London close, midpoint, USD/t, barge",
    "Biodiesel POME (palm oil mill effluent) OME RED ARA range barge fob prompt, London close, midpoint, USD/t, barge",
    "RED UCO (hydrotreating) fob ARA range prompt, London close, midpoint, USD/t, fob",
    "UCO (used cooking oil) fob ARA prompt, London close, midpoint, USD/t, fob",
    "UCO (used cooking oil) fob China prompt, Singapore close, midpoint, USD/t, fob",
    "Biodiesel SME Argentina waterborne fob upriver prompt, Houston close, midpoint, USD/t, fob",
    "Biodiesel Soya OME RED ARA range barge fob diff to Gasoil 7-28 day prompt, London close, diff midpoint, USD/t, barge",
    "RED Refined POME (palm oil mill effluent) oil fob Indonesia prompt, Singapore close, midpoint, USD/t, fob",
    "Palm oil mill effluent (POME) RED Malaysia fob prompt, Singapore close, midpoint, USD/t, fob",
    "B24 Marine UCOME and VLSFO dob Singapore USD/t prompt, Singapore close, midpoint, USD/t, dob",
    "B24 marine UCOME and VLSFO dob Fujairah USD/t spot, Singapore close, midpoint, USD/t, dob",
    "B24 marine UCOME and VLSFO dob Guangzhou USD/t prompt, Singapore close, midpoint, USD/t, dob",
    "B30 marine UCOME and VLSFO dob Houston USD/t spot, Houston close, midpoint, USD/t, dob",
    "Soybean oil Argentina waterborne fob upriver prompt, Houston close, midpoint, USD/t, fob",
    "Bionaphtha chemical feedstock UCO-based fob ARA range prompt, London close, midpoint, USD/t, fob",
    "Glycerine crude vegetable oil-based 80pc CFR China main ports flexi USD/t spot, No time stamp, midpoint, USD/t, cfr",
    "Specialised coated IMO2 biodiesel China to ARA 5kt prompt, No time stamp, midpoint, USD/t, _",
    "Specialised coated IMO2 biodiesel Singapore/Malaysia to ARA 5kt prompt, No time stamp, midpoint, USD/t, _",
    "B30 marine UCOME and VLSFO dob Los Angeles USD/t spot, Houston close, midpoint, USD/t, dob",
    "B30 Marine UCOME and VLSFO dob Singapore USD/t prompt, Singapore close, midpoint, USD/t, dob",
    "Biodiesel Advanced FAME 0C CFPP RED ARA range barge fob diff to Gasoil 7-28 day prompt, London close, diff midpoint, USD/t, barge",
    "RED SAF (HEFA-SPK) fob ARA range diff to Ice gasoil month 1, London close, midpoint, USD/t, fob",
    "HVO (hydrotreated vegetable oil) (Class II) fob ARA range diff to Ice gasoil month 1, London close, midpoint, USD/t, fob",
    "HVO (hydrotreated vegetable oil) (Class IV) fob ARA range diff to Ice gasoil month 1, London close, midpoint, USD/t, fob",
    "Biodiesel FAME 0C CFPP RED swap month 1, London close, midpoint, USD/t, barge",
    "Biodiesel FAME 0C CFPP RED swap month 2, London close, midpoint, USD/t, barge",
    "Biodiesel FAME 0C CFPP RED swap month 3, London close, midpoint, USD/t, barge",
    "Biodiesel FAME -10C CFPP RED month 1, London close, midpoint, USD/t, barge",
    "Biodiesel FAME -10C CFPP RED month 2, London close, midpoint, USD/t, barge",
    "Biodiesel FAME -10C CFPP RED month 3, London close, midpoint, USD/t, barge",
    "German Advanced GHG reduction obligation Euro/t CO2e Wed snapshot current year 1, London close, midpoint, Euro/t CO2e, _",
    "German Advanced sub quota GHG reduction obligation Euro/GJ Wed snapshot current year 1, London close, midpoint, Euro/GJ, _",
    "German Conventional GHG reduction obligation Euro/t CO2e Wed snapshot current year 1, London close, midpoint, Euro/t CO2e, _",
    "German Other GHG reduction obligation Euro/t CO2e Wed snapshot current year 1, London close, midpoint, Euro/t CO2e, _",
    "Netherlands LRE-Advanced (LRE-G) obligation Wed snapshot current year 1, London close, midpoint, Euro cent/kg CO2e, _",
    "Netherlands LRE-Conventional (LRE-C) obligation Wed snapshot current year 1, London close, midpoint, Euro cent/kg CO2e, _",
    "Netherlands ZRE-Advanced (ZRE-G) obligation Wed snapshot current year 1, London close, midpoint, Euro cent/kg CO2e, _",
    "UK Crop-based RTFC reduction obligation monthly snapshot current year 1, London close, midpoint, p/RTFC, _",
    "UK Other RTFC reduction obligation monthly snapshot current year 1, London close, midpoint, p/RTFC, _",
    "Biodiesel UCOME (used cooking oil) RED ARA range barge fob prompt, London close, diff midpoint, USD/t, barge",
    "Biodiesel UCOME (used cooking oil) RED bulk China fob prompt, Singapore close, midpoint, USD/t, fob",
    "California Carbon Allowance - diesel CARB month 1, Washington close, midpoint, USC/USG, _",
    "California LCFS credit based on soybean oil CI pathway US Gulf coast prompt, No time stamp, midpoint, USC/USG, _",
    "California LCFS credit based on UCO CI pathway US Gulf coast prompt, No time stamp, midpoint, USC/USG, _",
    "Soybean oil crude degummed US Gulf coast del rail prompt, Houston close, midpoint, USC/lb, del rail",
    "Soybean oil refined bleached and deodorized US west coast del rail prompt, Houston close, midpoint, USC/lb, del rail",
    "RIN biomass-based diesel (D4) current year Balance of year, Houston close, midpoint, USC/RIN, _",
    "Biodiesel B99 fob NYH barge month Balance of month, Houston close, midpoint, USC/USG, delivered",
]
US_FIRST_COL = 69  # 1-based column of the first US header


def _legacy(name: str):
    sys.path.insert(0, str(LEGACY))
    spec = importlib.util.spec_from_file_location(f"legacy_{name}", LEGACY / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _argus_grid(with_us: bool, seed: int = 7) -> tuple[list[list[str]], pd.DatetimeIndex]:
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2025-01-02", "2026-06-30")
    headers = ARGUS_HEADERS if with_us else ARGUS_HEADERS[:US_FIRST_COL - 1]
    cols = []
    for j, h in enumerate(headers):
        base = 40 + 10 * (j % 7) if "OBLIGATION" in h.upper() else (1 + j % 5 if "USC" in h.upper() else 900 + 13 * j)
        walk = base + np.cumsum(rng.normal(0, base * 0.004, len(dates)))
        vals = [f"{v:.2f}" for v in walk]
        if "OBLIGATION" in h.upper():                      # weekly/monthly snapshots: sparse
            vals = [v if i % 5 == 0 else "" for i, v in enumerate(vals)]
        for i in rng.choice(len(dates), size=12, replace=False):
            vals[i] = ""                                    # holes, as in the real sheet
        cols.append(vals)
    grid = [["Copyright 2026 Argus Media group. Updated at 11-Nov-2026"], ["combined\ndescription"] + headers]
    for i, d in enumerate(dates):
        grid.append([d.strftime("%d/%m/%Y")] + [c[i] for c in cols])
    return grid, dates


def _legs(dates: pd.DatetimeIndex, seed: int = 3) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = dates.union(pd.bdate_range("2024-08-01", "2024-12-31"))
    df = pd.DataFrame(index=idx)
    for i in (1, 2, 3):
        df[f"FCPO_M{i}"] = 1000 + np.cumsum(rng.normal(0, 5, len(idx))) - 3 * i
        df[f"GASOIL_M{i}"] = 700 + np.cumsum(rng.normal(0, 4, len(idx))) - 2 * i
    df["GBPUSD"] = 1.27 + np.cumsum(rng.normal(0, 0.002, len(idx)))
    df["EURUSD"] = 1.08 + np.cumsum(rng.normal(0, 0.002, len(idx)))
    df["SBO_ARG_LSEG"] = 1000 + np.cumsum(rng.normal(0, 4, len(idx)))
    return df


def test_argus_series_match_legacy():
    from app.sheets import argus
    legacy = _legacy("argus_data")
    grid, dates = _argus_grid(with_us=False)
    legs = _legs(dates)
    lw = legacy._parse_argus_wide(grid)
    for group in (["FCPO_M1", "FCPO_M2", "FCPO_M3"], ["GASOIL_M1", "GASOIL_M2", "GASOIL_M3"],
                  ["GBPUSD", "EURUSD"], ["SBO_ARG_LSEG"]):
        lw = lw.join(legs[group], how="left")
    expected = {k: s.dropna() for k, s in legacy._compute_series(lw).items() if not s.dropna().empty}
    got = argus.compute(argus.parse_wide(grid), legs)
    assert set(got) == set(expected)
    assert len(got) > 150
    for k, s in expected.items():
        pd.testing.assert_series_equal(got[k], s, check_names=False, check_freq=False)
    # titles and every charted key are known to the catalog
    specs = {s["key"] for s in argus.series_specs()}
    assert set(got) <= specs


def test_argus_us_columns_fix_legacy_uco_overwrite():
    from app.sheets import argus
    legacy = _legacy("argus_data")
    grid, _ = _argus_grid(with_us=True)
    uco_ara_col = 31
    expected_uco = pd.to_numeric(pd.Series([r[uco_ara_col] or None for r in grid[2:]]),
                                 errors="coerce")
    new = argus.parse_wide(grid)
    old = legacy._parse_argus_wide(grid)
    assert np.allclose(new["UCO_ARA"].to_numpy(dtype=float), expected_uco.to_numpy(dtype=float), equal_nan=True)
    assert not np.allclose(old["UCO_ARA"].to_numpy(dtype=float), expected_uco.to_numpy(dtype=float), equal_nan=True)
    assert {"US_CCA", "US_LCFS_SOY", "US_LCFS_UCO", "US_SBO_GULF", "US_SBO_RBD_WC", "US_RIN_D4",
            "US_B99_NYH"} <= set(new.columns)
    got = argus.compute(new, pd.DataFrame())
    assert "ARGUS_US_US_RIN_D4" in got and "ARGUS_OUT_UCO_ARA" in got


def test_mip_matches_legacy():
    from app.sheets import argus
    legacy = _legacy("argus_data")
    legs = _legs(pd.bdate_range("2025-01-02", "2026-10-30"))
    quarters = [(2025, q) for q in (1, 2, 3, 4)] + [(2026, q) for q in (1, 2, 3, 4)]
    got = argus.mip_table(legs, quarters)
    sbo, fx = legs[["SBO_ARG_LSEG"]], legs[["EURUSD"]]
    expected = [r for y, q in quarters if (r := legacy._mip_quarter_from_legs(sbo, fx, y, q)) is not None]
    assert got == expected and len(got) >= 6


def _premiums_grid() -> list[list[str]]:
    rng = np.random.default_rng(11)
    products = ["CPO", "RPO", "ROL", "RPS", "PFAD"]
    letters = [""] * 2
    prod_row, head = ["", ""], ["", "Date"]
    for p in products:
        prod_row += [p, p] + [""] * 9
        head += ["Reuters Indo", "Reuters Indo (Smooth)", f"{p} Reuters Indo Premium", "Reuters Msia",
                 "R Msia (Smooth)", f"{p} Reuters Msia Premium", "Tradable", f"{p} Tradable (Smooth)",
                 f"{p} Tradable Premium", "Levy + Duty", f"{p} Local Tradable"]
    grid = [letters + ["CI"] * (len(head) - 2), prod_row, head]
    for i, d in enumerate(pd.date_range("2026-01-01", "2026-06-30")):
        row = [str(739 + i), d.strftime("%-d %b %Y") if sys.platform != "win32" else d.strftime("%#d %b %Y")]
        for j, _p in enumerate(products):
            base = 1000 + 20 * j + i
            weekend = d.weekday() >= 5
            indo, msia, trad = base + rng.normal(0, 5), base + 10 + rng.normal(0, 5), base + 30 + rng.normal(0, 5)
            if i == 40 and j == 0:
                trad = trad / 10                                  # typo the outlier rule must drop
            cell = (lambda v: "" if weekend else f"{v:,.0f}")
            row += [cell(indo), f"{indo:,.0f}", "", cell(msia), f"{msia:,.0f}", "", cell(trad), f"{trad:,.0f}", "",
                    "257", "800"]
        grid.append(row)
    return grid


def test_tradable_prem_matches_legacy(tmp_path):
    from app.sheets import tradable
    legacy = _legacy("tradable_prem_data")
    grid = _premiums_grid()
    legacy._read_grid = lambda: grid
    legacy.CACHE_PATH = tmp_path / "tp.csv"
    tidy = legacy.fetch_tradable_prem()
    got = tradable.compute(grid)
    assert len(got) == 10
    for (region, product), g in tidy.groupby(["region", "product"]):
        exp = g.set_index("date")["premium"]
        pd.testing.assert_series_equal(got[f"TP_{region}_{product}"], exp, check_names=False, check_index_type=False)
    assert pd.Timestamp("2026-02-10") not in got["TP_MY_CPO"].index      # outlier dropped (i = 40)


def _csv_text(rows) -> str:
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerows(rows)
    return buf.getvalue()


def _india_local_rows():
    head = ["Date", "FX", "Imported CPO Refined to Olein", "Olein", "Olein Spread", "Soya Oil Ref", "Ref SBO Spread",
            "Sun Oil Ref", "Ref SFO Spread", "Gnut Oil Raw", "Raw GNO Spread", "Rapeseed Oil", "RSO Spread",
            "Cotton Oil Ref", "Ref Cotton Oil Spread", "Rice Bran Oil", "Ref Rice Bran Oil Spread"]
    rows = [head]
    for i, d in enumerate(pd.bdate_range("2025-10-01", "2026-03-31")[::-1]):
        rows.append([f"{d.day}/{d.month}/{d.year}", f"{88 + i % 5} "] + [f"{1300 + 10 * k + i} " for k in range(15)])
    return rows


def _india_dashboard_rows():
    rows = [[""] * 16, ["Date", "11/11/2026"] + [""] * 14,
            ["", "", "", "Olein Eq - SBO", "", "", "", "", "", "Olein Eq - SFO (Black Sea)", "", "", "",
             "CSBO - CSFO", "", ""],
            ["", "Date", "Nearest", "M1", "M2", "M3", "M4", "M5", "M6", "M1", "M2", "M3", "M4", "M1", "M2", "M3"]]
    for j, lbl in enumerate(["Today", "D-1", "D-2", "D-3", "D-4"]):
        rows.append([lbl, "10/11/2026", "10/11/2026"] + [f"{-100 + j + k:.2f}" for k in range(6)]
                    + ["#DIV/0!" if j == 0 else f"{-150 + j:.2f}", "-94.54", "-89.26", "-80.1", "-55.85", "-50.3", "-45"])
    rows.append([""] * 16)
    rows.append(["", "", "", "Olein Eq - SBO", "", "", "", "", "", "Olein Eq - SFO (Black Sea)", "", "", "",
                 "CSBO - CSFO", "", ""])
    rows.append(["", "Date", "Nearest", "M1", "M2", "M3", "M4", "M5", "M6", "M1", "M2", "M3", "M4", "M1", "M2", "M3"])
    for j, lbl in enumerate(["Today", "W-1", "W-2", "W-3", "W-4"]):
        rows.append([lbl, "10/11/2026", "10/11/2026"] + [f"{-90 + j + k:.2f}" for k in range(13)])
    return rows


def test_india_local_and_parity_match_legacy():
    from app.sheets import india
    g = _legacy("gsheet_data")
    par = _legacy("parity_data")
    local_csv = _csv_text(_india_local_rows())
    g.GSHEET_CSV_URL = io.StringIO(local_csv)
    usd_o, inr_o, usd_s, inr_s = g.fetch_india_data()
    got = india.local_prices(local_csv)
    for leg_usd, leg_inr in ((usd_o, inr_o), (usd_s, inr_s)):
        for key, df in leg_usd.items():
            usd, inr = got[key]
            assert np.allclose(usd.dropna().to_numpy(), df["spread_value"].to_numpy())
            assert np.allclose(inr.dropna().to_numpy(), leg_inr[key]["spread_value"].to_numpy())
    dash_csv = _csv_text(_india_dashboard_rows())
    par.INDIA_PARITY_URL = io.StringIO(dash_csv)
    exp = par.fetch_india_parity()
    snap = {(s["key"], s["period"]): s["records"] for s in india.parity_snapshot(dash_csv)}
    for key, blocks in exp.items():
        for period, recs in blocks.items():
            assert snap[(key, period)] == [{"label": r["label"], "x": r["x"], "y": [float(v) for v in r["y"]]}
                                           for r in recs]
    assert snap[("INDIA_PARITY_OLEIN_CSFO", "daily")][0]["x"] == ["M2", "M3", "M4"]     # #DIV/0! → gap


def _india_calc_grid():
    grid = [[""] * 27 for _ in range(5)]
    grid[1][0] = "2-Oct-24"
    grid[2][7] = "*All figures in USD per MT"
    grid[3][7:27] = ["Date", "CPO", "", "", "", "RBD OLEIN", "", "CDSBO", "", "", "", "CSFO", "", "", "", "PFAD", "",
                     "Duty Paid (DP)", "", "DP"]
    grid[4][7:27] = ["", "CNF", "Ref Parity", "HS", "Parity", "CNF", "Ref Parity", "CNF", "Ref Parity", "HS",
                     "Parity", "CIF", "Ref Parity", "HS", "Parity", "CNF", "Parity", "CPO", "CDSBO", "CDSBO-CPO"]
    for i, d in enumerate(pd.bdate_range("2024-10-02", "2024-12-31")):
        row = [""] * 27
        row[0] = "USD/INR Imports Customs rate" if i == 0 else ""
        row[7] = f"{d.day}/{d.month}/{d.year}"
        row[8:27] = [f"{1100 + k + i} " for k in range(19)]
        grid.append(row)
    return grid


def test_india_import_parity_reads_right_table():
    from app.sheets import india
    got = india.import_parity(_india_calc_grid())
    assert len(got) == 19
    assert got["INDIA_IMP_CPO_REF"].iloc[0] == 1101.0 and got["INDIA_DP_CDSBO_CPO"].iloc[0] == 1118.0
    assert got["INDIA_IMP_CPO_REF"].index[0] == pd.Timestamp("2024-10-02")


def _china_dashboard_grid():
    def block(label_pref, start):
        return [[lbl, "10/11/2026", "10/11/2026"] + [f"{start + j + k:.2f}" for k in range(5)] + [""] * 6
                for j, lbl in enumerate(["Today"] + [f"{label_pref}-{n}" for n in range(1, 5)])]
    head = ["Label", "Date", "Nearest", "Spot", "M1", "M2", "M3", "M4"] + [""] * 6
    g = [["Daily"] + [""] * 13, [""] * 14, ["POBO (average of NC, MC, SC)"] + [""] * 13, head] + block("D", -50)
    g += [[""] * 14, ["", "", "", "", "", "", "", "", "", "", "WoW Change", "", "", ""],
          ["Weekly"] + [""] * 13, head] + block("W", 10)
    g += [[""] * 14, ["Palm Olein Import Margin (South China)"] + [""] * 13, head] + block("D", -70)
    g += [[""] * 14, head] + block("W", -60)
    return g


def test_china_snapshot_reads_every_block():
    from app.sheets import china
    snap = china.parity_snapshot(_china_dashboard_grid())
    assert [(s["title"], s["period"]) for s in snap] == [
        ("China: POBO (average of NC, MC, SC)", "daily"), ("China: POBO (average of NC, MC, SC)", "weekly"),
        ("China: Palm Olein Import Margin (South China)", "daily"),
        ("China: Palm Olein Import Margin (South China)", "weekly")]
    assert snap[0]["records"][0] == {"label": "Today", "x": ["Spot", "M1", "M2", "M3", "M4"],
                                     "y": [-50.0, -49.0, -48.0, -47.0, -46.0]}


def _china_history_grids():
    days = pd.bdate_range("2025-01-02", "2026-03-31")
    cross = [["date", "Palm Main", "SBO Main", "RSO Main", "POBO", "PORSO", "BORSO"]]
    cross += [[d.strftime("%Y-%m-%d"), "9000", "#N/A" if i == 0 else "8800", "9800", "200", "-800", "-1000"]
              for i, d in enumerate(days)]
    units = ["", "", "CNY/mt", "", "", "USD/mt"] + [""] * 7 + ["CNY/mt", "CNY/mt", "CNY/mt", "", "", "USD/mt", "", "",
                                                             "CNY/mt", "", "", "USD/mt", "", ""]
    names = ["Date", "Palm CNF", "Spot - North China (CNY/mt)", "Spot - East China (CNY/mt)",
             "Spot - South China (CNY/mt)", "Spot N USD", "Spot E USD", "Spot S USD", "FX", "VAT", "Import Duty",
             "Port Cost", "DCE Main", "Landed Cost", "Landed Cost VS DCE", "Basis - North China", "Basis - East China",
             "Basis - South China", "Basis - North China", "Basis - East China", "Basis - South China",
             "Import Margin - North China", "Import Margin - East China", "Import Margin - South China",
             "Import Margin - North China", "Import Margin - East China", "Import Margin - South China"]
    palm = [units, names] + [[f"{d.day}/{d.month}/{d.year}"] + ["1"] * 14 + ["228", "200", "48", "35.78", "31.2",
                                                                          "7.53", "-355.57", "-300", "-535.57",
                                                                          "-55.80", "-47.1", "-84.06"]
                             for d in days]
    soy = [["", "USD/mt"], ["Date", "Brazil CNF", "Arg CNF", "Soyoil Spot", "FX", "Import Duty", "VAT", "Port Cost",
                            "Brazil Landed Cost", "Arg Landed Cost", "Brazil Import Margin", "Arg Import Margin"]]
    soy += [[f"{d.day}/{d.month}/{d.year}", "1471", "1468", "9870", "6.36", "0.09", "0.09", "100", "1848", "1844",
             "-298", "-294"] for d in days]
    return {"dashboard": _china_dashboard_grid(), "cross_oil": cross, "palm_import": palm, "soyoil_import": soy}


def _all_grids():
    grid, dates = _argus_grid(with_us=True)
    return {
        "argus": {"prices": grid},
        "market_price": {"premiums": _premiums_grid()},
        "china_parity": _china_history_grids(),
        "india_parity": {"local": _india_local_rows(), "dashboard": _india_dashboard_rows(),
                         "calculation": _india_calc_grid()},
    }


@pytest.fixture(scope="module")
def sheets_loaded():
    from unittest import mock

    from app.sheets import job, sources
    grids = _all_grids()
    with mock.patch.object(sources, "fetch", lambda src, client: (grids[src.code], "upload")):
        res = job.refresh_sources()
    return res


def test_refresh_job_loads_every_source(sheets_loaded):
    res = sheets_loaded
    for code in ("argus", "market_price", "china_parity", "india_parity"):
        assert res[code]["status"] == "ok", res[code]
    with get_engine().connect() as c:
        n = dict(c.execute(text(
            "SELECT s.definition->>'source', count(DISTINCT sp.series_key) FROM market.series_points sp "
            "JOIN market.series s ON s.key = sp.series_key WHERE s.kind = 'sheet' GROUP BY 1")).all())
        cny = c.execute(text("SELECT value, value_local FROM market.series_points "
                             "WHERE series_key = 'CHINA_PALM_IMPORT_MARGIN_NORTH' LIMIT 1")).first()
        snaps = {r[0] for r in c.execute(text("SELECT key FROM intel.sheet_snapshots"))}
    assert n["market_price"] == 10 and n["india_parity"] == 40 and n["china_parity"] == 11 and n["argus"] > 150
    assert (cny.value, cny.value_local) == (-55.8, -355.57)
    assert {"parity_china", "parity_india"} <= snaps
    # the Argus legs come from the demo LSEG data: the gasoil-based series exist
    with get_engine().connect() as c:
        assert c.execute(text("SELECT count(*) FROM market.series_points WHERE series_key = 'ARGUS_PREMFWD_UCOME_M1'")
                         ).scalar() > 0


def test_lseg_recompute_keeps_sheet_series(sheets_loaded):
    from app.pipeline import runner
    rid = runner.enqueue("recompute", "cli")
    assert runner.execute(rid) in ("succeeded", "partial")
    with get_engine().connect() as c:
        assert c.execute(text("SELECT count(*) FROM market.series_points WHERE series_key = 'TP_MY_CPO'")).scalar() > 0
        assert c.execute(text("SELECT count(*) FROM market.series_points WHERE series_key = 'BOPO_c3'")).scalar() > 0


def test_failed_source_keeps_its_data(sheets_loaded):
    from unittest import mock

    from app.sheets import job, sources
    with mock.patch.object(sources, "fetch", lambda src, client: ({"premiums": [["nothing"]]}, "upload")):
        res = job.refresh_sources(["market_price"])
    assert res["market_price"]["status"] == "failed"
    with get_engine().connect() as c:
        assert c.execute(text("SELECT count(*) FROM market.series_points WHERE series_key = 'TP_ID_PFAD'")).scalar() > 0


def test_waste_bio_views(admin_client, sheets_loaded):
    cat = admin_client.get("/api/market/catalog").json()["modules"]
    wb = next(m for m in cat if m["code"] == "waste_bio")
    assert {g["section"] for g in wb["groups"]} >= {"calendar", "tickets", "us"}
    assert all(g["section"] != "_internal" for g in wb["groups"])
    r = admin_client.get("/api/market/sections/waste_bio?section=calendar&group=UCOME&years=2025,2026")
    assert r.status_code == 200
    assert [s["key"] for s in r.json()["series"]] == ["ARGUS_CAL_UCOME_M1M2", "ARGUS_CAL_UCOME_M2M3",
                                                      "ARGUS_CAL_UCOME_M1M3"]
    assert r.json()["series"][0]["traces"]
    tp = admin_client.get("/api/market/sections/basis_premiums?section=tradable&group=Tradable Prem · Malaysia").json()
    assert len(tp["series"]) == 5
    c = admin_client.get("/api/sheets/waste-bio/curves/fwd_diff?years=2025,2026&snapshot=06-15").json()
    assert len(c["curves"]) == 6 and c["curves"][0]["traces"][0]["x"] == ["M1", "M2", "M3"]
    y = admin_client.get("/api/sheets/waste-bio/ytd?year=2026").json()
    assert y["products"][0]["legs"][0]["label"] == "M1-M2"
    a = admin_client.get("/api/sheets/waste-bio/arb")
    assert a.status_code == 200 and "quarters" in a.json() and a.json()["china_ucome"]["status"] in ("open", "closed")
    t = admin_client.get("/api/sheets/waste-bio/tickets-calc").json()
    assert [r["market"] for r in t["rows"]] == ["UK", "Germany", "Netherlands"]
    s = admin_client.get("/api/sheets/parity-snapshots").json()
    assert len(s["india"]) == 6 and len(s["china"]) == 4
    assert admin_client.get("/api/sheets/waste-bio/curves/nope").status_code == 404


def test_sheet_admin_and_access(admin_client, sheets_loaded):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from fastapi.testclient import TestClient

    from app.main import app
    from conftest import login
    st = admin_client.get("/api/admin/sheets").json()
    assert [s["code"] for s in st["sources"]] == ["argus", "market_price", "china_parity", "india_parity"]
    assert st["service_account"]["configured"] is False
    assert admin_client.put("/api/admin/sheets/service-account", json={"key_json": "x" * 60}).status_code == 422
    pem = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    key = {"type": "service_account", "client_email": "reader@proj.iam.gserviceaccount.com", "private_key": pem,
           "private_key_id": "abc", "token_uri": "https://oauth2.googleapis.com/token"}
    r = admin_client.put("/api/admin/sheets/service-account", json={"key_json": json.dumps(key)})
    assert r.status_code == 200 and r.json()["email"] == key["client_email"]
    st = admin_client.get("/api/admin/sheets")
    assert st.json()["service_account"] == {"configured": True, "email": key["client_email"], "origin": "admin"}
    assert "PRIVATE KEY" not in st.text
    with get_engine().connect() as c:
        enc = c.execute(text("SELECT secret_enc FROM ops.integration_secrets WHERE provider = 'google_sa'")).scalar()
    assert "PRIVATE KEY" not in enc
    assert admin_client.delete("/api/admin/sheets/service-account").json()["removed"] is True
    # upload: an .xlsx export with the Premiums tab
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Premiums"
    for row in _premiums_grid():
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    r = admin_client.post("/api/admin/sheets/upload", data={"code": "market_price"},
                          files={"file": ("kpn.xlsx", buf.getvalue(), "application/octet-stream")})
    assert r.status_code == 201, r.text
    bad = admin_client.post("/api/admin/sheets/upload", data={"code": "argus"},
                            files={"file": ("kpn.xlsx", buf.getvalue(), "application/octet-stream")})
    assert bad.status_code == 422 and "Prices" in bad.json()["error"]["message"]
    assert admin_client.delete("/api/admin/sheets/upload/market_price").json()["removed"] is True
    with get_engine().begin() as c:
        c.execute(text("DELETE FROM ops.jobs WHERE kind = 'sheet_refresh'"))
        acts = {r[0] for r in c.execute(text("SELECT action FROM ops.audit_logs WHERE action LIKE 'SHEETS_%'"))}
    assert {"SHEETS_SERVICE_ACCOUNT_SET", "SHEETS_SERVICE_ACCOUNT_REMOVED", "SHEETS_UPLOAD",
            "SHEETS_UPLOAD_DELETE"} <= acts
    # a viewer: no Waste/Bio, no admin, but Ind/China parity (viewer has the parity module)
    email, pw = f"viewer.sheets.{uuid.uuid4().hex[:6]}@aastar-test.com", "Viewer-Sheets-Pass-9"
    assert admin_client.post("/api/admin/users", json={"email": email, "display_name": "Viewer Sheets",
                                                       "role": "viewer", "auth_provider": "local", "password": pw}
                             ).status_code == 201
    with TestClient(app, base_url="http://testserver", headers={"Origin": "http://testserver"}) as v:
        assert login(v, email, pw).status_code == 200
        assert v.get("/api/sheets/waste-bio/arb").status_code == 403
        assert v.get("/api/market/series/ARGUS_OUT_UCOME_M1").status_code == 403
        assert v.get("/api/admin/sheets").status_code == 403
        assert v.post("/api/admin/sheets/refresh", json={}).status_code == 403
        assert v.get("/api/sheets/parity-snapshots").status_code == 200
