"""Argus biofuels "Prices" sheet → Waste/Bio series — a port of legacy/scripts/argus_data.py.

Header classification, the wide parse, every derived-series formula, the titles and the MIP fixing logic are
copied from the legacy module unchanged (tests/test_sheets.py runs both on the same grid). Only the I/O
differs: the grid comes from app.integrations.gsheets (or an admin upload) and the LSEG legs come from
market.price_daily via app.engine.conversions.build_frames, exactly as the legacy joined them.
"""
# ruff: noqa: E501
from __future__ import annotations

import logging
import re
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

# Products that carry an M1/M2/M3 swap curve in the sheet. FAME0/FAME10 were
# always this same swap curve (London close, M1-M3) — confirmed 2026-10-06 by
# re-fetching and diffing against the prior cache (0 value change across
# 3,500-4,800 overlapping dates) after Argus added the word "swap" to FAME0's
# header text; no new/second column appeared for either product, so labels
# were updated (PRODUCT_LABELS) but no series/classification logic changed.
CURVE_PRODUCTS = ["UCOME", "FAME0", "FAME10", "HVO", "HEFA", "RSO_OME"]

# Prompt-only ("flat") series — no M1/M2/M3 curve of their own.
FLAT_OUTRIGHT_KEYS = [
    "UCOME_MALACCA", "UCOME_BARGE_PROMPT", "HVO_PROMPT", "FAME0_ADVANCED",
    "RSO_EUR", "POME_PROMPT", "RED_UCO_HYDROTREATING", "UCO_ARA", "UCO_CHINA",
    "SME_ARGENTINA",
    # added 2026-09-21 (source sheet column set expanded)
    "POME_REFINED_INDONESIA", "POME_REFINED_MALAYSIA", "SBO_ARGENTINA",
    # added 2026-10-14 (new sheet column, for the China UCOME CIF Malaysia arb)
    "UCOME_CHINA",
]

# Byproduct feedstocks/outputs (added 2026-09-21) -> Byproduct tab.
BYPRODUCT_KEYS = ["BIONAPHTHA_UCO", "GLYCERINE_CHINA"]

# Freight rates (added 2026-09-21) -> Freight tab.
FREIGHT_KEYS = ["FREIGHT_CHINA_ARA", "FREIGHT_SEASIA_ARA"]

# Marine bunker blends — B24/B30 (UCOME + VLSFO) at various ports (added
# 2026-09-21) -> Bunker Market tab.
BUNKER_KEYS = [
    "BUNKER_B24_SINGAPORE", "BUNKER_B24_FUJAIRAH", "BUNKER_B24_GUANGZHOU",
    "BUNKER_B30_HOUSTON", "BUNKER_B30_LOSANGELES", "BUNKER_B30_SINGAPORE",
]

# Compliance-ticket prices (GHG-quota / RTFC / HBE-style certificates) for
# UK, Germany and Netherlands (added 2026-10-08) -> Tickets tab. Key ->
# (country, label, native unit). Native units differ by scheme and are NOT
# converted (UK p/RTFC, DE EUR/t CO2e + one EUR/GJ sub-quota, NL EUR cent/kg
# CO2e). Source cadence is sparse: DE/NL are Wednesday snapshots, UK is a
# monthly snapshot, all 2026-only so far.
TICKET_DEFS = {
    "TKT_UK_CROP":        ("UK", "UK Crop-based RTFC", "p/RTFC"),
    "TKT_UK_OTHER":       ("UK", "UK Other RTFC", "p/RTFC"),
    "TKT_DE_ADVANCED":    ("DE", "Germany Advanced GHG quota", "EUR/t CO2e"),
    "TKT_DE_ADV_SUBQUOTA": ("DE", "Germany Advanced sub-quota", "EUR/GJ"),
    "TKT_DE_CONVENTIONAL": ("DE", "Germany Conventional GHG quota", "EUR/t CO2e"),
    "TKT_DE_OTHER":       ("DE", "Germany Other GHG quota", "EUR/t CO2e"),
    "TKT_NL_LRE_G":       ("NL", "Netherlands LRE-Advanced (LRE-G)", "EUR cent/kg CO2e"),
    "TKT_NL_LRE_C":       ("NL", "Netherlands LRE-Conventional (LRE-C)", "EUR cent/kg CO2e"),
    "TKT_NL_ZRE_G":       ("NL", "Netherlands ZRE-Advanced (ZRE-G)", "EUR cent/kg CO2e"),
}

# ── Blend margin: ticket value vs. (biofuel − gasoil) premium (added 2026-10-12) ──
# For each product and country: ticket value per tonne of biofuel (USD/t) minus
# the physical premium over fossil gasoil, all M1, all USD/t:
#     blend margin = ticket value − Argus "diff to Gasoil" (see BLEND_DIFF_COL)
# Positive = the compliance ticket more than pays for the premium of blending.
# Ticket value assumptions (trader-specified; edit here, titles/calc box follow):
#   UK : t = 1000 / density(kg/l) litres; × RTFC per litre × RTFC price (p) /100 × GBPUSD.
#        Crop-based = 1 RTFC/l (Crop RTFC); waste = 2 RTFC/l (Other RTFC).
#   DE : t CO2 saved = MJ/kg × 94 g/MJ × GHG saving / 1000;
#        value = t CO2 × THG quota (EUR/t) × EURUSD. No nEHS carbon levy (dropped
#        2026-10-12 to match the expert's German 9A line).
#   NL : value = t CO2 saved × LRE price (EUR cent/kg ×10 = EUR/t CO2) × EURUSD;
#        no national fuel carbon levy.
# Tickets are sparse snapshots (UK monthly, DE/NL Wednesday) so each is held
# forward until the next snapshot (limit below) to line up with daily prices.
BLEND_PRODUCTS = {
    # key: (label, Argus curve col base, density kg/l, MJ/kg, GHG saving, waste?)
    "FAME":  ("FAME 0 (crop-based)", "FAME0", 0.88, 37.4, 0.85, False),
    "UCOME": ("UCOME (waste-based)", "UCOME", 0.88, 37.4, 0.85, True),
    "HVO":   ("HVO (renewable diesel, waste-based)", "HVO", 0.78, 43.6, 0.90, True),
}
# Premium over gasoil: for ALL products = product M1 − ICE Gasoil M1 (LGOc1),
# computed (None). A non-None entry would use that Argus "diff to Gasoil" column.
# NB the sheet has no UCOME-specific diff, so UCOME (None) = UCOME M1 − ICE Gasoil
# M1 (LGOc1) computed directly, per trader's call.
BLEND_DIFF_COL = {
    "FAME":  None,
    "UCOME": None,
    "HVO":   None,
}
BLEND_PREMIUM_DESC = {
    "FAME":  "FAME 0 M1 − ICE Gasoil M1 (LGOc1)",
    "UCOME": "UCOME M1 − ICE Gasoil M1 (LGOc1)",
    "HVO":   "HVO M1 − ICE Gasoil M1 (LGOc1)",
}
FOSSIL_COMPARATOR_G_MJ = 94.0
# ticket column used per country, crop (conventional) vs waste (advanced/other)
BLEND_TICKET = {
    "UK": {False: "TKT_UK_CROP",         True: "TKT_UK_OTHER"},
    # DE/NL: FAME (crop) and UCOME/HVO (UCO-based, Annex IX 9B) are all
    # non-advanced -> the non-advanced ticket. 9A (advanced) tickets are not used.
    "DE": {False: "TKT_DE_CONVENTIONAL", True: "TKT_DE_CONVENTIONAL"},
    "NL": {False: "TKT_NL_LRE_C",        True: "TKT_NL_LRE_C"},
}
BLEND_TICKET_FFILL_DAYS = {"UK": 35, "DE": 8, "NL": 8}
BLEND_COUNTRY_NAMES = {"UK": "UK", "DE": "Germany", "NL": "Netherlands"}
UK_RTFC_PER_LITRE = {False: 1, True: 2}


def uk_litres_per_tonne(product: str) -> float:
    return 1000.0 / BLEND_PRODUCTS[product][2]


def blend_ticket_usd(country: str, product: str, price, fx):
    """Ticket value (USD per tonne of biofuel). `price` = ticket quote in its
    native unit (p/RTFC, EUR/t CO2e or EUR cent/kg CO2e); `fx` = USD per
    GBP (UK) or per EUR (DE/NL). Works on scalars or aligned Series."""
    _lbl, _col, dens, mj, sav, waste = BLEND_PRODUCTS[product]
    if country == "UK":
        litres = 1000.0 / dens
        return litres * UK_RTFC_PER_LITRE[waste] * price / 100.0 * fx  # USD/t (reference only; UK charts are p/litre)
    t_co2 = mj * FOSSIL_COMPARATOR_G_MJ * sav / 1000.0
    if country == "DE":
        return t_co2 * price * fx
    return t_co2 * price * 10.0 * fx   # NL: cent/kg CO2e -> EUR/t CO2e


# Raw "diff to Gasoil"/"diff to Ice Gasoil" quotes -> Biofuel Premium tab
# (kept out of Outright Prices per trader's explicit instruction).
PREMIUM_DIFF_LABELS = {
    "FAME0_DIFF_GASOIL":    "FAME 0 CFPP RED fob barge — diff to Gasoil (7-28 day prompt)",
    "FAME10_DIFF_GASOIL":   "FAME -10 CFPP RED fob barge — diff to Gasoil (7-28 day prompt)",
    "HVO_DIFF_GASOIL_M1":   "HVO (Class II) fob ARA — diff to Ice Gasoil (M1)",
    "HEFA_DIFF_GASOIL_M1":  "RED SAF (HEFA-SPK) fob ARA — diff to Ice Gasoil (M1)",
    "RSO_OME_DIFF_GASOIL":  "Rapeseed OME RED fob barge — diff to Gasoil (7-28 day prompt)",
    "SOYA_OME_DIFF_GASOIL": "Soya OME RED fob barge — diff to Gasoil (7-28 day prompt)",
    # added 2026-09-21
    "FAME0_ADVANCED_DIFF_GASOIL": "Advanced FAME 0 CFPP RED fob barge — diff to Gasoil (7-28 day prompt)",
    "HVO4_DIFF_GASOIL_M1":        "HVO (Class IV) fob ARA — diff to Ice Gasoil (M1)",
    # added 2026-10-13 (sheet col BP)
    "UCOME_DIFF_GASOIL":          "UCOME RED fob barge — diff to Gasoil (prompt)",
}

# European Margin (added 2026-09-21, per trader's explicit landed-cost
# buildup — see module docstring). Selling price is always the finished
# product's FOB ARA swap at M3 only. Cost is (feedstock + freight) × (1 +
# duty) + opex for the two waste/residue feedstocks, or a flat FX + opex
# for Dutch rapeseed oil (no freight/duty — EU-origin).
EURO_MARGIN_FREIGHT = {"POME_REFINED_INDONESIA": 180.0, "UCO_CHINA": 130.0}
EURO_MARGIN_DUTY    = {"POME_REFINED_INDONESIA": 1.00,  "UCO_CHINA": 1.02}
# Post-import processing cost, added after duty (not dutiable). UCO needs pre-treatment.
EURO_MARGIN_PRETREAT = {"POME_REFINED_INDONESIA": 0.0, "UCO_CHINA": 30.0}
RSO_FX_EUR_USD = 1.15
RSO_FREIGHT    = 50.0   # USD/t, Dutch RSO delivery; no duty (EU-origin)
RSO_OPEX       = 120.0

FEEDSTOCK_LABELS = {
    "POME_REFINED_INDONESIA": "Indonesia RPOME",
    "UCO_CHINA":               "Chinese UCO",
    "RSO":                     "Dutch rapeseed oil (RSO)",
}

# (margin_key, group, feedstock_key, product_key, product_col_base, opex, label)
# product_col_base is the df column prefix whose _M3 is the selling price
# ("RSO" as a feedstock_key is special-cased in _compute_series: EUR->USD FX
# + flat opex, no freight/duty).
EURO_MARGIN_DEFS = [
    ("RPOME_UCOME", "BD",  "POME_REFINED_INDONESIA", "UCOME", 150.0),
    ("UCO_UCOME",   "BD",  "UCO_CHINA",               "UCOME", 150.0),
    ("RSO_RME",     "BD",  "RSO",                     "RSO_OME", RSO_OPEX),
    ("RPOME_HVO",   "HVO", "POME_REFINED_INDONESIA", "HVO",   250.0),
    ("UCO_HVO",     "HVO", "UCO_CHINA",               "HVO",   250.0),
    ("RPOME_SAF",   "SAF", "POME_REFINED_INDONESIA", "HEFA",  300.0),
    ("UCO_SAF",     "SAF", "UCO_CHINA",               "HEFA",  300.0),
]

# ── Arb tab (added 2026-10-12) ────────────────────────────────────────────────
# BD Arb vs Arg SME: cost of making SME from Argentine soyoil and landing it in
# ARA, minus FAME 0 fob ARA (M2). EUR/t. Positive = import route is dearer than
# the ARA market (arb shut); negative = import route is cheaper (arb open).
#   spread = (SBO_arg/EURUSD + ARB_CONVERSION_EUR + ARB_FREIGHT_USD/EURUSD)
#            x (1 + ARB_IMPORT_DUTY)  -  FAME0_M2/EURUSD
# SBO_arg = LSEG BO-ARGUPR-C1 (FOB Up River soyoil cash, USD/MT). Duty is applied
# to the whole landed value (oil + conversion + freight), i.e. a CIF-style base.
ARB_CONVERSION_EUR = 139.0   # EUR/t, soyoil -> SME conversion
ARB_FREIGHT_USD    = 100.0   # USD/t, Argentina -> ARA
ARB_IMPORT_DUTY    = 0.059   # 5.9% EU import duty
ARB_FAME_MATURITY  = 2       # FAME 0 fob ARA M2

# China UCOME CIF Malaysia (2nd Arb section, added 2026-10-14) — a blended
# CIF Malaysia cost (60% a China-UCOME import route, 40% a POME-fob-Malaysia
# route) vs. the pure-POME reference alone, trader's exact formula:
#   0.60*(UCOME_CHINA + freight + trucking + fobbing)
#   + 0.40*(POME_REFINED_MALAYSIA + net_costs) - (POME_REFINED_MALAYSIA + net_costs)
# All USD/t, no FX conversion needed (unlike the SME arb above). Diff > 0
# means the China-blend route costs more than pure POME (no incentive) ->
# arb closed; diff < 0 -> arb open (opposite sign convention from the SME
# arb, since this diff is blend-minus-reference, not reference-minus-blend).
CHINA_UCOME_FREIGHT_MY  = 50.0   # USD/t, China -> Malaysia
CHINA_UCOME_TRUCKING_MY = 8.0    # USD/t
CHINA_UCOME_FOBBING_MY  = 6.0    # USD/t
POME_MY_NET_COSTS       = 175.0  # USD/t — used in both the 40% weighted leg and the final reference
CHINA_UCOME_WEIGHT      = 0.60
POME_MY_WEIGHT          = 0.40

# ── Arb tab: quarterly MIP table (added 2026-10-13, generalised from a
# single hardcoded Q4 2026 window) ──────────────────────────────────────────
# Physical SME import pricing is fixed one month ahead of the delivery
# quarter, off the trailing 3-month average up to that point — e.g. Q4
# (Oct-Dec) is priced off the Jun-Aug average, confirmed in Sep. So for a
# quarter starting at calendar month M: the averaging window is the 3
# months [M-4, M-3, M-2], and the EURUSD reference is the average over that
# same window (changed from last-trading-day spot, per trader, 2026-10-16). Same conversion/freight/duty
# constants as the daily ARGUS_ARB_BD_ARG_SME series above — only the
# averaging logic (a per-quarter fixing window instead of one daily value)
# differs. The quarterly MIP is a single number, applied flat across all 3
# months of that quarter (no within-quarter schedule).
_QUARTER_START_MONTH = {1: 1, 2: 4, 3: 7, 4: 10}


def _quarter_bounds(year: int, q: int):
    """(window_start, window_end, quarter_start, quarter_end) for quarter q
    of year — see module note above for the Jun-Aug -> Q4 Oct-Dec logic."""
    start_month = _QUARTER_START_MONTH[q]
    q_start = pd.Timestamp(year=year, month=start_month, day=1)
    q_end = q_start + pd.DateOffset(months=3) - pd.Timedelta(days=1)
    window_start = q_start - pd.DateOffset(months=4)
    window_end = (q_start - pd.DateOffset(months=2)) + pd.offsets.MonthEnd(0)
    return window_start, window_end, q_start, q_end


def current_quarter() -> tuple[int, int]:
    """(year, quarter) containing today — the "confirmed" quarter whose MIP
    is live/actionable right now."""
    today = pd.Timestamp.today()
    return today.year, (today.month - 1) // 3 + 1


def _mip_quarter_from_legs(sbo: pd.DataFrame, fx: pd.DataFrame, year: int, q: int) -> Optional[dict]:
    """MIP (EUR/t) for one quarter given already-loaded (and NaT-cleaned)
    SBO_arg/EURUSD frames — see compute_mip_quarter/compute_mip_table, which
    are the public entry points; this is split out so computing many
    quarters doesn't reload+reprocess the full LSEG raw history per quarter
    (measured: 16 reloads for an 8-quarter table took minutes)."""
    window_start, window_end, q_start, q_end = _quarter_bounds(year, q)
    if sbo.empty or fx.empty or window_end > sbo.index.max() or window_end > fx.index.max():
        return None   # fixing window not fully observed yet

    sbo_window = sbo.loc[window_start:window_end, "SBO_ARG_LSEG"].dropna()
    eurusd_series = fx.loc[window_start:window_end, "EURUSD"].dropna()
    if sbo_window.empty or eurusd_series.empty:
        return None

    avg_sbo = float(sbo_window.mean())
    eurusd = float(eurusd_series.mean())   # window average, same fixing window as SBO
    fx_label = f"avg {window_start.date()} → {window_end.date()}"

    mip_eur = (avg_sbo / eurusd + ARB_CONVERSION_EUR
               + ARB_FREIGHT_USD / eurusd) * (1 + ARB_IMPORT_DUTY)

    return {
        "year": year, "quarter": q, "label": f"Q{q} {year}",
        "window_start": str(window_start.date()), "window_end": str(window_end.date()),
        "q_start": q_start, "q_end": q_end,
        "avg_sbo_usd": avg_sbo, "eurusd": eurusd, "fx_date": fx_label,
        "mip_eur": mip_eur, "n_days": int(sbo_window.shape[0]),
    }


PRODUCT_LABELS = {
    "UCOME":  "UCOME fob ARA (swap)",
    "FAME0":  "FAME 0 CFPP RED (swap)",
    "FAME10": "FAME -10 CFPP RED (swap)",
    "HVO":    "HVO (Class II) fob ARA",
    "HEFA":   "RED SAF (HEFA-SPK) fob ARA",
    "RSO_OME": "Rapeseed OME RED fob barge",
    "UCOME_MALACCA":         "UCOME fob Strait of Malacca (prompt)",
    "UCOME_BARGE_PROMPT":    "UCOME RED ARA range barge fob (prompt)",
    "HVO_PROMPT":            "HVO (Class II) fob ARA range (prompt)",
    "FAME0_ADVANCED":        "Advanced FAME 0 CFPP RED fob barge (prompt)",
    "RSO_EUR":               "Rapeseed oil fob Dutch Mill (prompt, EUR/t)",
    "POME_PROMPT":           "POME RED fob barge (prompt)",
    "RED_UCO_HYDROTREATING": "RED UCO (hydrotreating grade) fob ARA (prompt)",
    "UCO_ARA":               "UCO fob ARA (prompt)",
    "UCO_CHINA":             "UCO fob China (prompt)",
    "SME_ARGENTINA":         "SME Argentina waterborne fob upriver (prompt)",
    "FCPO":                  "FCPO (BMD)",
    # added 2026-09-21
    "POME_REFINED_INDONESIA": "Refined POME fob Indonesia (prompt)",
    "POME_REFINED_MALAYSIA":  "POME (RED) fob Malaysia (prompt)",
    "SBO_ARGENTINA":          "Soybean oil Argentina waterborne fob upriver (prompt)",
    "BIONAPHTHA_UCO":         "Bionaphtha (UCO-based) fob ARA (prompt)",
    "GLYCERINE_CHINA":        "Glycerine (crude, veg-oil based, 80%) cfr China (spot)",
    "FREIGHT_CHINA_ARA":      "Freight — specialised coated IMO2 biodiesel, China to ARA (5kt prompt)",
    "FREIGHT_SEASIA_ARA":     "Freight — specialised coated IMO2 biodiesel, Singapore/Malaysia to ARA (5kt prompt)",
    "BUNKER_B24_SINGAPORE":   "B24 marine UCOME/VLSFO dob Singapore (prompt)",
    "BUNKER_B24_FUJAIRAH":    "B24 marine UCOME/VLSFO dob Fujairah (spot)",
    "BUNKER_B24_GUANGZHOU":   "B24 marine UCOME/VLSFO dob Guangzhou (prompt)",
    "BUNKER_B30_HOUSTON":     "B30 marine UCOME/VLSFO dob Houston (spot)",
    "BUNKER_B30_LOSANGELES":  "B30 marine UCOME/VLSFO dob Los Angeles (spot)",
    "BUNKER_B30_SINGAPORE":   "B30 marine UCOME/VLSFO dob Singapore (prompt)",
    # added 2026-10-14
    "UCOME_CHINA":            "Biodiesel UCOME (UCO-based) RED bulk fob China (prompt)",
}

# (leg_a, leg_b, output_label) -> spread = leg_a - leg_b. A leg in
# CURVE_PRODUCTS resolves to its M1/M2/M3 curve (one output per maturity);
# a leg that never appears in CURVE_PRODUCTS is a flat/prompt series, reused
# unchanged across all three maturities if paired with a curve, or producing
# a single flat spread if paired with another flat series.
#
# Full pairwise cross-product spreads among the 6 curve products (added
# 2026-09-24, per trader's explicit ask — every C(6,2)=15 combination, each
# shown exactly once with a fixed precedence order deciding which leg is
# leg_a: SAF (HEFA) > HVO > UCOME > RME (RSO_OME) > FAME -10 (FAME10) >
# FAME 0 (FAME0). E.g. "HVO vs UCOME" is always HVO − UCOME, never the
# reverse. (Rapeseed OME vs UCOME was previously the other way round,
# RSO_OME − UCOME — flipped here to UCOME − RSO_OME to fit the rule, since
# UCOME outranks RME.)
PRODUCT_SPREAD_DEFS = [
    ("HEFA",      "HVO",                "ARGUS_PS_HEFA_HVO"),
    ("HEFA",      "UCOME",              "ARGUS_PS_HEFA_UCOME"),
    ("HEFA",      "RSO_OME",            "ARGUS_PS_HEFA_RSO_OME"),
    ("HEFA",      "FAME10",             "ARGUS_PS_HEFA_FAME10"),
    ("HEFA",      "FAME0",              "ARGUS_PS_HEFA_FAME0"),
    ("HVO",       "UCOME",              "ARGUS_PS_HVO_UCOME"),
    ("HVO",       "RSO_OME",            "ARGUS_PS_HVO_RSO_OME"),
    ("HVO",       "FAME10",             "ARGUS_PS_HVO_FAME10"),
    ("HVO",       "FAME0",              "ARGUS_PS_HVO_FAME0"),
    ("UCOME",     "RSO_OME",            "ARGUS_PS_UCOME_RSO_OME"),
    ("UCOME",     "FAME10",             "ARGUS_PS_UCOME_FAME10"),
    ("UCOME",     "FAME0",              "ARGUS_PS_UCOME_FAME0"),
    ("RSO_OME",   "FAME10",             "ARGUS_PS_RSO_OME_FAME10"),
    ("RSO_OME",   "FAME0",              "ARGUS_PS_RSO_OME_FAME0"),
    ("FAME10",    "FAME0",              "ARGUS_PS_FAME10_FAME0"),

    # Spreads outside the 6-curve-product precedence rule above (unchanged).
    ("UCOME",     "UCO_CHINA",          "ARGUS_PS_UCOME_UCO"),
    ("UCOME",     "FCPO",               "ARGUS_PS_UCOME_FCPO"),
    ("UCO_CHINA", "FCPO",               "ARGUS_PS_UCO_FCPO"),
    # UCOME delivery/location basis (added 2026-09-08)
    ("UCOME",     "UCOME_MALACCA",      "ARGUS_PS_UCOME_VS_MALACCA"),
    ("UCOME",     "UCOME_BARGE_PROMPT", "ARGUS_PS_UCOME_VS_BARGE"),
    # UCO geography arb (added 2026-09-08)
    ("UCO_ARA",   "UCO_CHINA",          "ARGUS_PS_UCO_ARA_VS_CHINA"),
]


# ── Column classification (header text -> internal key) ────────────────────────

def _classify_header(header: str) -> Optional[str]:
    """Map one column-1 header string to an internal series key, or None
    (the Date column). Tolerant to column reordering — matched by keyword,
    not position. Order matters: more specific keywords (ADVANCED, MALACCA,
    BARGE, HYDROTREATING, ...) are checked before the generic product name
    they'd otherwise also match."""
    h = header.upper()
    m = re.search(r"MONTH (\d)", h)
    month = m.group(1) if m else None
    is_diff = "DIFF TO" in h

    # Compliance tickets (added 2026-10-08) — checked first; none of these
    # headers share keywords with the biofuel/feedstock products below.
    if "OBLIGATION" in h:
        if h.startswith("UK") and "RTFC" in h:
            return "TKT_UK_CROP" if "CROP" in h else ("TKT_UK_OTHER" if "OTHER" in h else None)
        if h.startswith("GERMAN"):
            if "SUB QUOTA" in h:
                return "TKT_DE_ADV_SUBQUOTA"
            for word, key in (("ADVANCED", "TKT_DE_ADVANCED"), ("CONVENTIONAL", "TKT_DE_CONVENTIONAL"),
                              ("OTHER", "TKT_DE_OTHER")):
                if word in h:
                    return key
            return None
        if h.startswith("NETHERLANDS"):
            for tag, key in (("LRE-G", "TKT_NL_LRE_G"), ("LRE-C", "TKT_NL_LRE_C"), ("ZRE-G", "TKT_NL_ZRE_G")):
                if tag in h:
                    return key
            return None

    # Marine bunker blends (B24/B30 UCOME+VLSFO) — checked before the generic
    # UCOME branch below, since every bunker header also contains "UCOME".
    # Port must be read off the text right after "DOB": the trailing
    # "..., Singapore close,"/"Houston close," timestamp field repeats a
    # *different* city (the exchange close time, not the delivery port) and
    # would otherwise false-match via a plain substring scan.
    if "MARINE" in h and "VLSFO" in h:
        bm = re.search(r"B(24|30)", h)
        pm = re.search(r"DOB\s+([A-Z]+(?:\s+[A-Z]+)?)\s+USD", h)
        if not bm or not pm:
            return None
        blend, port = f"B{bm.group(1)}", pm.group(1)
        tag = {"SINGAPORE": "SINGAPORE", "FUJAIRAH": "FUJAIRAH",
               "GUANGZHOU": "GUANGZHOU", "HOUSTON": "HOUSTON",
               "LOS ANGELES": "LOSANGELES"}.get(port)
        return f"BUNKER_{blend}_{tag}" if tag else None
    if "ADVANCED" in h and "FAME 0" in h:
        return "FAME0_ADVANCED_DIFF_GASOIL" if is_diff else "FAME0_ADVANCED"
    if "SAF" in h or "HEFA" in h:
        return f"HEFA_DIFF_GASOIL_M{month or 1}" if is_diff else (f"HEFA_M{month}" if month else None)
    if "FAME -10" in h or "FAME-10" in h:
        return "FAME10_DIFF_GASOIL" if is_diff else (f"FAME10_M{month}" if month else None)
    if "FAME 0" in h:
        return "FAME0_DIFF_GASOIL" if is_diff else (f"FAME0_M{month}" if month else None)
    if "RAPESEED" in h:
        if "OME" not in h:
            return "RSO_EUR"
        return "RSO_OME_DIFF_GASOIL" if is_diff else (f"RSO_OME_M{month}" if month else None)
    if "POME" in h:
        # Three distinct POME-labeled columns: the POME-based biodiesel OME
        # (ARA barge, no "REFINED"/"MALAYSIA" keyword) vs. the two raw
        # Refined POME feedstock-oil columns (Indonesia / Malaysia).
        if "REFINED" in h and "INDONESIA" in h:
            return "POME_REFINED_INDONESIA"
        if "MALAYSIA" in h:
            return "POME_REFINED_MALAYSIA"
        return "POME_PROMPT"
    if "SOYA" in h and "OME" in h:
        return "SOYA_OME_DIFF_GASOIL"
    if "SOYBEAN" in h and "ARGENTINA" in h:
        return "SBO_ARGENTINA"
    if "SME" in h and "ARGENTINA" in h:
        return "SME_ARGENTINA"
    if "MALACCA" in h:
        return "UCOME_MALACCA"
    if "UCOME" in h:
        # Added 2026-10-14: new sheet column, "Biodiesel UCOME ... RED bulk
        # China fob prompt" — a flat/prompt series (no "swap month N"), so it
        # must be split off before the generic month-based fallback below
        # (which would otherwise return None for it, silently dropping it).
        if "CHINA" in h:
            return "UCOME_CHINA"
        if "BARGE" in h:
            # Added 2026-10-13: sheet col BP is the barge prompt *diff to gasoil*;
            # its header says "diff midpoint" (no "DIFF TO"), so it must be split
            # off here or it overwrites the outright barge price (col X).
            if is_diff or "DIFF MIDPOINT" in h:
                return "UCOME_DIFF_GASOIL"
            return "UCOME_BARGE_PROMPT"
        return f"UCOME_M{month}" if month else None
    if "HVO" in h:
        is_class4 = "CLASS IV" in h
        if is_diff:
            return f"HVO4_DIFF_GASOIL_M{month or 1}" if is_class4 else f"HVO_DIFF_GASOIL_M{month or 1}"
        if is_class4:
            return f"HVO4_M{month}" if month else "HVO4_PROMPT"
        return f"HVO_M{month}" if month else "HVO_PROMPT"
    if "HYDROTREATING" in h:
        return "RED_UCO_HYDROTREATING"
    if "BIONAPHTHA" in h:
        return "BIONAPHTHA_UCO"
    if "GLYCERINE" in h:
        return "GLYCERINE_CHINA"
    if "SPECIALISED" in h and "IMO2" in h:
        if "CHINA" in h:
            return "FREIGHT_CHINA_ARA"
        if "SINGAPORE" in h or "MALAYSIA" in h:
            return "FREIGHT_SEASIA_ARA"
        return None
    if re.search(r"\bUCO\b", h) or "UCO (" in h:
        return "UCO_CHINA" if "CHINA" in h else "UCO_ARA"
    return None



def _parse_argus_wide(grid: list) -> Optional[pd.DataFrame]:
    """Grid -> date-indexed DataFrame of the internal-key columns."""
    # Header row = the first row with >1 non-empty cell (row 0 is a copyright line).
    header_row_idx = next(
        (i for i, row in enumerate(grid[:5]) if sum(1 for c in row if c) > 1), None
    )
    if header_row_idx is None:
        logger.warning("Argus: could not locate header row.")
        return None

    header = grid[header_row_idx]
    col_keys = {0: "date"}
    for c in range(1, len(header)):
        key = _classify_header(header[c])
        if key:
            col_keys[c] = key
    if len(col_keys) < 50:
        logger.warning("Argus: only matched %d/51 columns — check sheet layout.", len(col_keys))

    records = []
    for row in grid[header_row_idx + 1:]:
        if not row or not row[0]:
            continue
        rec = {}
        for c, key in col_keys.items():
            if key == "date":
                continue
            v = row[c] if c < len(row) else None
            v = str(v).strip() if v is not None else ""
            rec[key] = pd.to_numeric(v, errors="coerce") if v else None
        rec["date"] = pd.to_datetime(row[0], dayfirst=True, errors="coerce")
        records.append(rec)

    df = pd.DataFrame.from_records(records)
    if "date" not in df.columns or df["date"].isna().all():
        logger.warning("Argus: no valid dates parsed.")
        return None
    df = df.dropna(subset=["date"]).drop_duplicates(subset=["date"]).set_index("date").sort_index()
    return df


def _leg(df: pd.DataFrame, key: str, i: int) -> pd.Series:
    """Resolve one leg of a spread at maturity i: the M{i} column if the
    product has a curve, else its single flat series."""
    col = f"{key}_M{i}"
    if col in df.columns:
        return df[col]
    return df[key] if key in df.columns else pd.Series(dtype=float, index=df.index)


def _compute_series(df: pd.DataFrame) -> dict[str, pd.Series]:
    out: dict[str, pd.Series] = {}

    # Calendar spreads — one per curve product
    for p in CURVE_PRODUCTS:
        if f"{p}_M1" not in df.columns:
            continue
        m1, m2, m3 = df[f"{p}_M1"], df[f"{p}_M2"], df[f"{p}_M3"]
        out[f"ARGUS_CAL_{p}_M1M2"] = m1 - m2
        out[f"ARGUS_CAL_{p}_M2M3"] = m2 - m3
        out[f"ARGUS_CAL_{p}_M1M3"] = m1 - m3

    # Product spreads (A - B) — per maturity if either leg is a curve,
    # else a single flat spread (both legs are prompt-only).
    for leg_a, leg_b, label in PRODUCT_SPREAD_DEFS:
        if leg_a in CURVE_PRODUCTS or leg_b in CURVE_PRODUCTS:
            for i in (1, 2, 3):
                a, b = _leg(df, leg_a, i), _leg(df, leg_b, i)
                if a.empty or b.empty:
                    continue
                out[f"{label}_M{i}"] = a - b
        else:
            a, b = _leg(df, leg_a, 1), _leg(df, leg_b, 1)
            if not a.empty and not b.empty:
                out[label] = a - b

    # Outrights — curve products' M1/M2/M3 ...
    for p in CURVE_PRODUCTS:
        for i in (1, 2, 3):
            col = f"{p}_M{i}"
            if col in df.columns:
                out[f"ARGUS_OUT_{col}"] = df[col]
    # ... plus every prompt-only ("flat") series
    for key in FLAT_OUTRIGHT_KEYS:
        if key in df.columns:
            out[f"ARGUS_OUT_{key}"] = df[key]

    # Byproducts (Bionaphtha, Glycerine) — own tab, added 2026-09-21.
    for key in BYPRODUCT_KEYS:
        if key in df.columns:
            out[f"ARGUS_BYPRODUCT_{key}"] = df[key]

    # Freight rates — own tab, added 2026-09-21.
    for key in FREIGHT_KEYS:
        if key in df.columns:
            out[f"ARGUS_FREIGHT_{key}"] = df[key]

    # Marine bunker blends (B24/B30 UCOME+VLSFO) — own tab, added 2026-09-21.
    for key in BUNKER_KEYS:
        if key in df.columns:
            out[f"ARGUS_BUNKER_{key}"] = df[key]

    # Compliance tickets (UK/DE/NL) — own tab, added 2026-10-08.
    for key in TICKET_DEFS:
        if key in df.columns:
            out[f"ARGUS_TICKET_{key}"] = df[key]

    # Blend margin = ticket value (USD/t) − premium over gasoil (Argus diff; UCOME M1 − LGO M1).
    for country, fxcol in (("UK", "GBPUSD"), ("DE", "EURUSD"), ("NL", "EURUSD")):
        if fxcol not in df.columns:
            continue
        for pkey, (_l, base, _d, _m, _s, waste) in BLEND_PRODUCTS.items():
            tkt, diff = BLEND_TICKET[country][waste], BLEND_DIFF_COL[pkey]
            if diff is None:   # UCOME: computed premium over LGO
                if f"{base}_M1" not in df.columns or "GASOIL_M1" not in df.columns:
                    continue
                prem = df[f"{base}_M1"] - df["GASOIL_M1"]
            elif diff in df.columns:
                prem = df[diff]
            else:
                continue
            if tkt not in df.columns:
                continue
            price = df[tkt].ffill(limit=BLEND_TICKET_FFILL_DAYS[country])
            usd = blend_ticket_usd(country, pkey, price, df[fxcol])
            if country == "UK":
                # pence per litre of fuel: ticket = RTFCs/l x RTFC price (p);
                # premium = USD/t / GBPUSD / litres per tonne x 100.
                tkt_ppl = UK_RTFC_PER_LITRE[waste] * price
                prem_ppl = prem / df[fxcol] / uk_litres_per_tonne(pkey) * 100.0
                out[f"ARGUS_BLENDTKT_{pkey}_UK"] = tkt_ppl
                out[f"ARGUS_BLEND_{pkey}_UK"] = tkt_ppl - prem_ppl
                continue
            out[f"ARGUS_BLENDTKT_{pkey}_{country}"] = usd
            out[f"ARGUS_BLEND_{pkey}_{country}"] = usd - prem
    for col, key in (("GBPUSD", "ARGUS_FX_GBPUSD"), ("EURUSD", "ARGUS_FX_EURUSD")):
        if col in df.columns:
            out[key] = df[col]

    # Biofuel Premium — raw "diff to Gasoil" quotes, kept out of Outright
    # Prices, plus the computed Advanced Premium (both legs are the "ARA
    # range barge fob prompt" quotes, so this is a same-delivery-point
    # comparison).
    for key in PREMIUM_DIFF_LABELS:
        if key in df.columns:
            out[f"ARGUS_PREM_{key}"] = df[key]
    if "FAME0_ADVANCED" in df.columns and "UCOME_BARGE_PROMPT" in df.columns:
        out["ARGUS_PREM_ADVANCED"] = df["FAME0_ADVANCED"] - df["UCOME_BARGE_PROMPT"]

    # Biofuel Premium — Forward Diff (added 2026-09-21): each curve product's
    # M1/M2/M3 price minus ICE Gasoil at the SAME maturity — a genuine
    # maturity-matched curve, distinct from Argus's own single-M1 "diff to
    # Gasoil 7-28 day prompt" quotes (the "7-28 Day Diff" subsection above).
    # Rendered in the dashboard as a forward-curve-style chart (x = M1/M2/M3,
    # one line per year), not the usual seasonal overlay.
    for p in CURVE_PRODUCTS:
        for i in (1, 2, 3):
            prod_col, gasoil_col = f"{p}_M{i}", f"GASOIL_M{i}"
            if prod_col in df.columns and gasoil_col in df.columns:
                out[f"ARGUS_PREMFWD_{p}_M{i}"] = df[prod_col] - df[gasoil_col]

    # Features
    for i in (1, 2, 3):
        f10, f0 = f"FAME10_M{i}", f"FAME0_M{i}"
        if f10 in df.columns and f0 in df.columns:
            out[f"ARGUS_FEAT_CFPP_M{i}"] = (df[f10] - df[f0]) / 10
        uc = f"UCOME_M{i}"
        if uc in df.columns and f0 in df.columns:
            out[f"ARGUS_FEAT_GHG_M{i}"] = (df[uc] - df[f0]) / 22

        gasoil = f"GASOIL_M{i}"
        cap_key = f"ARGUS_FEAT_SYNCAP_M{i}"
        if f0 in df.columns and gasoil in df.columns:
            out[cap_key] = 2 * df[f0] - df[gasoil]
        if uc in df.columns and f0 in df.columns and gasoil in df.columns:
            denom = df[f0] - df[gasoil]
            mult = (df[uc] - df[gasoil]) / denom
            # denom is the FAME0-Gasoil "1x" credit value the multiple is
            # solved against; below ~$100/MT it's too thin for the ratio to
            # be economically meaningful — division blows up (seen: -61x to
            # +18x during 2026-03-18..05-01, when the LGO Gasoil leg itself
            # went choppy around a front-month roll). Mask rather than plot
            # the noise; genuine sustained events (e.g. 2022-06, ratio 3-5.5x
            # on a $130-290 denom) stay well clear of this floor.
            out[f"ARGUS_FEAT_SYNCAP_MULTIPLE_M{i}"] = mult.where(denom.abs() >= 100)

    # European Margin (added 2026-09-21) — landed-cost buildup, M3 selling
    # price only. See module docstring / EURO_MARGIN_DEFS for the formula.
    for margin_key, _group, feedstock_key, product_col_base, opex in EURO_MARGIN_DEFS:
        sell_col = f"{product_col_base}_M3"
        if sell_col not in df.columns:
            continue
        if feedstock_key == "RSO":
            if "RSO_EUR" not in df.columns:
                continue
            cost = df["RSO_EUR"] * RSO_FX_EUR_USD + RSO_FREIGHT + opex
        else:
            if feedstock_key not in df.columns:
                continue
            freight = EURO_MARGIN_FREIGHT[feedstock_key]
            duty = EURO_MARGIN_DUTY[feedstock_key]
            pretreat = EURO_MARGIN_PRETREAT[feedstock_key]
            cost = (df[feedstock_key] + freight) * duty + pretreat + opex
        out[f"ARGUS_EUROMARGIN_{margin_key}"] = df[sell_col] - cost

    # Arb tab (added 2026-10-12) — BD Arb vs Arg SME, EUR/t. See ARB_* constants.
    fame_col = f"FAME0_M{ARB_FAME_MATURITY}"
    if all(c in df.columns for c in ("SBO_ARG_LSEG", "EURUSD", fame_col)):
        eur = df["EURUSD"]
        landed = (df["SBO_ARG_LSEG"] / eur + ARB_CONVERSION_EUR
                  + ARB_FREIGHT_USD / eur) * (1 + ARB_IMPORT_DUTY)
        out["ARGUS_ARB_BD_ARG_SME"] = landed - df[fame_col] / eur

    # China UCOME CIF Malaysia (added 2026-10-14) — 2nd Arb section, daily
    # series for the historical chart. Both legs are native Argus sheet
    # columns (same date index), so no LSEG join/lag to worry about.
    if "UCOME_CHINA" in df.columns and "POME_REFINED_MALAYSIA" in df.columns:
        china_landed = (df["UCOME_CHINA"] + CHINA_UCOME_FREIGHT_MY
                         + CHINA_UCOME_TRUCKING_MY + CHINA_UCOME_FOBBING_MY)
        pome_ref = df["POME_REFINED_MALAYSIA"] + POME_MY_NET_COSTS
        blended = CHINA_UCOME_WEIGHT * china_landed + POME_MY_WEIGHT * pome_ref
        out["ARGUS_ARB_CHINA_UCOME_CIF_MY"] = blended - pome_ref

    return out


def _build_titles() -> dict[str, str]:
    titles: dict[str, str] = {}
    for p in CURVE_PRODUCTS:
        for i in (1, 2, 3):
            titles[f"ARGUS_OUT_{p}_M{i}"] = f"{PRODUCT_LABELS[p]} — M{i}"
        titles[f"ARGUS_CAL_{p}_M1M2"] = f"{PRODUCT_LABELS[p]}: M1 - M2"
        titles[f"ARGUS_CAL_{p}_M2M3"] = f"{PRODUCT_LABELS[p]}: M2 - M3"
        titles[f"ARGUS_CAL_{p}_M1M3"] = f"{PRODUCT_LABELS[p]}: M1 - M3"

    for key in FLAT_OUTRIGHT_KEYS:
        titles[f"ARGUS_OUT_{key}"] = PRODUCT_LABELS[key]

    for key in BYPRODUCT_KEYS:
        titles[f"ARGUS_BYPRODUCT_{key}"] = PRODUCT_LABELS[key]
    for key in FREIGHT_KEYS:
        titles[f"ARGUS_FREIGHT_{key}"] = PRODUCT_LABELS[key]
    for key in BUNKER_KEYS:
        titles[f"ARGUS_BUNKER_{key}"] = PRODUCT_LABELS[key]
    for key, (_c, label, unit) in TICKET_DEFS.items():
        titles[f"ARGUS_TICKET_{key}"] = f"{label} ({unit})"

    for leg_a, leg_b, label in PRODUCT_SPREAD_DEFS:
        name_a, name_b = PRODUCT_LABELS[leg_a], PRODUCT_LABELS[leg_b]
        if leg_a in CURVE_PRODUCTS or leg_b in CURVE_PRODUCTS:
            for i in (1, 2, 3):
                titles[f"{label}_M{i}"] = f"{name_a} − {name_b}  (M{i})"
        else:
            titles[label] = f"{name_a} − {name_b}"

    for key, label in PREMIUM_DIFF_LABELS.items():
        titles[f"ARGUS_PREM_{key}"] = label
    titles["ARGUS_PREM_ADVANCED"] = (
        "Advanced Premium — Advanced FAME 0 (barge, prompt) minus "
        "UCOME (barge, prompt)"
    )

    for country in BLEND_TICKET:
        cname = BLEND_COUNTRY_NAMES[country]
        for pkey, (plabel, base, _d, _m, _s, waste) in BLEND_PRODUCTS.items():
            tk = TICKET_DEFS[BLEND_TICKET[country][waste]][1]
            unit = "p per litre of fuel" if country == "UK" else "USD/t"
            titles[f"ARGUS_BLEND_{pkey}_{country}"] = (
                f"{cname} {plabel} — blend margin ({unit}) = {tk} value − premium to gasoil "
                f"[{BLEND_PREMIUM_DESC[pkey]}]"
            )
            titles[f"ARGUS_BLENDTKT_{pkey}_{country}"] = (
                f"{cname} {plabel}: {tk} value " + ("(p per litre of fuel)" if country == "UK" else "per tonne"))
    titles["ARGUS_FX_GBPUSD"] = "GBP/USD (USD per GBP)"
    titles["ARGUS_FX_EURUSD"] = "EUR/USD (USD per EUR)"

    for p in CURVE_PRODUCTS:
        for i in (1, 2, 3):
            titles[f"ARGUS_PREMFWD_{p}_M{i}"] = f"{PRODUCT_LABELS[p]} minus Gasoil  (M{i})"

    for i in (1, 2, 3):
        titles[f"ARGUS_FEAT_CFPP_M{i}"] = f"$ Value / CFPP °C — (FAME -10 − FAME 0) / 10  (M{i})"
        titles[f"ARGUS_FEAT_GHG_M{i}"]  = f"$ Value / GHG unit — (UCOME − FAME 0) / 22  (M{i})"
        titles[f"ARGUS_FEAT_SYNCAP_M{i}"] = (
            f"UCOME Synthetic Cap — Gasoil + 2×(FAME 0 − Gasoil)  (M{i})"
        )
        titles[f"ARGUS_FEAT_SYNCAP_MULTIPLE_M{i}"] = (
            f"Actual Double Counting Benefit — (UCOME − Gasoil) / (FAME 0 − Gasoil)  (M{i})"
        )

    end_product_name = {"UCOME": "UCOME", "HVO": "HVO", "HEFA": "SAF", "RSO_OME": "RME"}
    for margin_key, _group, feedstock_key, product_col_base, opex in EURO_MARGIN_DEFS:
        feed_label = FEEDSTOCK_LABELS[feedstock_key]
        prod_name = end_product_name[product_col_base]
        if feedstock_key == "RSO":
            # sell − (RSO × FX + opex)
            cost_desc = (
                f"({feed_label} × {RSO_FX_EUR_USD} FX + ${RSO_FREIGHT:.0f} freight "
                f"+ ${opex:.0f} opex)"
            )
        else:
            freight = EURO_MARGIN_FREIGHT[feedstock_key]
            duty = EURO_MARGIN_DUTY[feedstock_key]
            pretreat = EURO_MARGIN_PRETREAT[feedstock_key]
            pretreat_desc = f" + ${pretreat:.0f} pre-treatment" if pretreat else ""
            # sell − (((feedstock + freight) × duty) + opex) — outer parens
            # around the whole cost term are load-bearing: without them the
            # label reads (BODMAS) as "sell − (X×duty) + opex", which flips
            # the sign on opex versus what's actually computed.
            cost_desc = (
                f"(({feed_label} + ${freight:.0f} freight) × {duty:.2f} duty "
                f"{pretreat_desc} + ${opex:.0f} opex)"
            )
        titles[f"ARGUS_EUROMARGIN_{margin_key}"] = f"{prod_name} FOB ARA (M3) − {cost_desc}"
    titles["ARGUS_ARB_BD_ARG_SME"] = (
        f"BD Arb vs Arg SME — ((Arg Up River Soyoil + €{ARB_CONVERSION_EUR:.0f} conversion "
        f"+ ${ARB_FREIGHT_USD:.0f} freight) × {1 + ARB_IMPORT_DUTY:.3f} duty) "
        f"− FAME 0 FOB ARA (M{ARB_FAME_MATURITY})  [EUR/t]"
    )
    titles["ARGUS_ARB_CHINA_UCOME_CIF_MY"] = (
        f"China UCOME CIF Malaysia — ({CHINA_UCOME_WEIGHT:.0%}×(China UCOME FOB + "
        f"${CHINA_UCOME_FREIGHT_MY:.0f} freight + ${CHINA_UCOME_TRUCKING_MY:.0f} trucking "
        f"+ ${CHINA_UCOME_FOBBING_MY:.0f} fobbing) + {POME_MY_WEIGHT:.0%}×(POME FOB Malaysia + "
        f"${POME_MY_NET_COSTS:.0f} net costs)) − (POME FOB Malaysia + ${POME_MY_NET_COSTS:.0f} "
        f"net costs)  [USD/t; >0 = arb closed, <0 = arb open]"
    )
    return titles


def _build_ylabels() -> dict[str, str]:
    ylabels: dict[str, str] = {}
    for i in (1, 2, 3):
        ylabels[f"ARGUS_FEAT_CFPP_M{i}"] = "USD/MT per °C CFPP"
        ylabels[f"ARGUS_FEAT_GHG_M{i}"]  = "USD/MT per GHG unit"
    for key, (_c, _l, unit) in TICKET_DEFS.items():
        ylabels[f"ARGUS_TICKET_{key}"] = unit
    for k in list(BLEND_PRODUCTS):
        for c in BLEND_TICKET:
            ylabels[f"ARGUS_BLEND_{k}_{c}"] = (
                "p per litre of fuel" if c == "UK" else "USD per tonne of biofuel (ticket value − premium to gasoil)")
    ylabels["ARGUS_FX_GBPUSD"] = ylabels["ARGUS_FX_EURUSD"] = "USD per unit"
    ylabels["ARGUS_ARB_BD_ARG_SME"] = "EUR/MT"
    ylabels["ARGUS_ARB_CHINA_UCOME_CIF_MY"] = "USD/MT"
    ylabels["ARGUS_OUT_RSO_EUR"] = "EUR/MT"  # only non-USD series in the tab, per trader's call
    for i in (1, 2, 3):
        ylabels[f"ARGUS_FEAT_SYNCAP_MULTIPLE_M{i}"] = "multiple (x)"
    return ylabels  # everything else defaults to USD/MT


# ═══════════════════════════════════════════════════════════════════════════════
# New-app additions (not in the legacy module)
# ═══════════════════════════════════════════════════════════════════════════════

# US compliance / US biofuel columns added to the sheet in Nov 2026 (cols 69–75). Native units, no conversion.
# They are classified BEFORE the legacy rules: the legacy `\bUCO\b` rule would otherwise read
# "California LCFS credit based on UCO CI pathway" as UCO fob ARA and overwrite that series.
US_DEFS = {
    "US_CCA":        ("California Carbon Allowance — diesel (CARB, month 1)", "USc/USG"),
    "US_LCFS_SOY":   ("California LCFS credit — soybean oil CI pathway (US Gulf coast)", "USc/USG"),
    "US_LCFS_UCO":   ("California LCFS credit — UCO CI pathway (US Gulf coast)", "USc/USG"),
    "US_SBO_GULF":   ("Soybean oil crude degummed, US Gulf coast del rail (prompt)", "USc/lb"),
    "US_SBO_RBD_WC": ("Soybean oil RBD, US West Coast del rail (prompt)", "USc/lb"),
    "US_RIN_D4":     ("RIN biomass-based diesel (D4), current year", "USc/RIN"),
    "US_B99_NYH":    ("Biodiesel B99 fob NYH barge (balance of month)", "USc/USG"),
}


def classify_us(header: str) -> Optional[str]:
    h = header.upper()
    if "CALIFORNIA CARBON ALLOWANCE" in h:
        return "US_CCA"
    if "LCFS" in h:
        if "UCO" in h:
            return "US_LCFS_UCO"
        if "SOYBEAN" in h:
            return "US_LCFS_SOY"
        return None
    if "SOYBEAN OIL" in h and "US GULF" in h:
        return "US_SBO_GULF"
    if "SOYBEAN OIL" in h and "US WEST COAST" in h:
        return "US_SBO_RBD_WC"
    if h.startswith("RIN ") and "D4" in h:
        return "US_RIN_D4"
    if "B99" in h and "NYH" in h:
        return "US_B99_NYH"
    return None


def classify(header: str) -> Optional[str]:
    return classify_us(header) or _classify_header(header)


def parse_wide(grid: list) -> Optional[pd.DataFrame]:
    """_parse_argus_wide with the US-first classification (same row/date handling)."""
    header_row_idx = next((i for i, row in enumerate(grid[:5]) if sum(1 for c in row if c) > 1), None)
    if header_row_idx is None:
        return None
    header = grid[header_row_idx]
    col_keys = {0: "date"}
    for c in range(1, len(header)):
        key = classify(header[c] or "")
        if key:
            col_keys[c] = key
    records = []
    for row in grid[header_row_idx + 1:]:
        if not row or not row[0]:
            continue
        rec = {}
        for c, key in col_keys.items():
            if key == "date":
                continue
            v = row[c] if c < len(row) else None
            v = str(v).strip() if v is not None else ""
            rec[key] = pd.to_numeric(v, errors="coerce") if v else None
        rec["date"] = pd.to_datetime(row[0], dayfirst=True, errors="coerce")
        records.append(rec)
    df = pd.DataFrame.from_records(records)
    if "date" not in df.columns or df["date"].isna().all():
        return None
    return df.dropna(subset=["date"]).drop_duplicates(subset=["date"]).set_index("date").sort_index()


def lseg_legs(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """The LSEG legs the legacy joined onto the Argus dates (fetch_argus): FCPO/LGO c1–c3 in USD/MT forward-filled,
    GBP=/EUR= and BO-ARGUPR-C1 native forward-filled. `frames` = app.engine.conversions.build_frames output."""
    usd, raw_ffill = frames["usd"], frames["raw_ffill"]
    parts = []
    for prefix, name in (("FCPO", "FCPO"), ("LGO", "GASOIL")):
        cols = [c for c in (f"{prefix}c1", f"{prefix}c2", f"{prefix}c3") if c in usd.columns]
        if cols:
            part = usd[cols].sort_index().ffill()
            part.columns = [f"{name}_M{c[-1]}" for c in cols]
            parts.append(part)
    fx = {"GBP=": "GBPUSD", "EUR=": "EURUSD"}
    have = [c for c in fx if c in raw_ffill.columns]
    if have:
        parts.append(raw_ffill[have].sort_index().ffill().rename(columns=fx))
    if "BO-ARGUPR-C1" in raw_ffill.columns:
        parts.append(raw_ffill[["BO-ARGUPR-C1"]].sort_index().ffill().rename(
            columns={"BO-ARGUPR-C1": "SBO_ARG_LSEG"}))
    if not parts:
        return pd.DataFrame()
    legs = pd.concat(parts, axis=1)
    legs.index = pd.to_datetime(legs.index)
    return legs


def compute(argus_wide: pd.DataFrame, legs: pd.DataFrame) -> dict[str, pd.Series]:
    """fetch_argus: exact-date left joins of each LSEG leg, then _compute_series, plus the US outrights."""
    df = argus_wide
    if not legs.empty:
        for group in (["FCPO_M1", "FCPO_M2", "FCPO_M3"], ["GASOIL_M1", "GASOIL_M2", "GASOIL_M3"],
                      ["GBPUSD", "EURUSD"], ["SBO_ARG_LSEG"]):
            cols = [c for c in group if c in legs.columns]
            if cols:
                df = df.join(legs[cols], how="left")
    out = _compute_series(df)
    for key in US_DEFS:
        if key in df.columns:
            out[f"ARGUS_US_{key}"] = df[key]
    result = {}
    for k, s in out.items():
        s = s.dropna()
        if not s.empty:
            result[k] = s
    return result


def mip_table(legs: pd.DataFrame, quarters: list[tuple[int, int]]) -> list[dict]:
    """compute_mip_table off already-loaded legs (SBO_ARG_LSEG, EURUSD)."""
    if legs.empty or "SBO_ARG_LSEG" not in legs.columns or "EURUSD" not in legs.columns:
        return []
    sbo = legs[["SBO_ARG_LSEG"]]
    fx = legs[["EURUSD"]]
    sbo = sbo[sbo.index.notna()].sort_index()
    fx = fx[fx.index.notna()].sort_index()
    out = []
    for year, q in quarters:
        row = _mip_quarter_from_legs(sbo, fx, year, q)
        if row is not None:
            out.append(row)
    return out


# ── Waste/Bio layout: legacy dashboard.ARGUS_SUBSECTIONS (section → group → series keys) ───────────────────────
SECTIONS: list[tuple[str, str]] = [
    ("calendar", "Calendar Spreads"), ("product", "Product Spreads"), ("outright", "Outright Prices"),
    ("premium", "Biofuel Premium"), ("bunker", "Bunker Market"), ("margin", "European Margin"),
    ("byproduct", "Byproduct"), ("freight", "Freight"), ("tickets", "Tickets"), ("arb", "Arb"),
    ("features", "Features"), ("us", "US Compliance"),
]

_CAL_PRODUCTS = {"UCOME": "UCOME", "FAME 0": "FAME0", "FAME -10": "FAME10", "HVO": "HVO", "HEFA-SPK": "HEFA",
                 "Rapeseed OME": "RSO_OME"}
_PS_LABELS = {
    "HEFA_HVO": "HEFA-SPK vs HVO", "HEFA_UCOME": "HEFA-SPK vs UCOME", "HEFA_RSO_OME": "HEFA-SPK vs Rapeseed OME",
    "HEFA_FAME10": "HEFA-SPK vs FAME -10", "HEFA_FAME0": "HEFA-SPK vs FAME 0", "HVO_UCOME": "HVO vs UCOME",
    "HVO_RSO_OME": "HVO vs Rapeseed OME", "HVO_FAME10": "HVO vs FAME -10", "HVO_FAME0": "HVO vs FAME 0",
    "UCOME_RSO_OME": "UCOME vs Rapeseed OME", "UCOME_FAME10": "UCOME vs FAME -10", "UCOME_FAME0": "UCOME vs FAME 0",
    "RSO_OME_FAME10": "Rapeseed OME vs FAME -10", "RSO_OME_FAME0": "Rapeseed OME vs FAME 0",
    "FAME10_FAME0": "FAME -10 vs FAME 0", "UCOME_UCO": "UCOME vs UCO (China)", "UCOME_FCPO": "UCOME vs FCPO",
    "UCO_FCPO": "UCO (China) vs FCPO", "UCOME_VS_MALACCA": "UCOME vs Malacca (basis)",
    "UCOME_VS_BARGE": "UCOME vs Barge (basis)",
}

LAYOUT: dict[str, dict[str, list[str]]] = {
    "calendar": {label: [f"ARGUS_CAL_{code}_{t}" for t in ("M1M2", "M2M3", "M1M3")]
                 for label, code in _CAL_PRODUCTS.items()},
    # "UCO (China) vs FCPO" has no curve leg, so the legacy formula yields one flat ARGUS_PS_UCO_FCPO (the legacy
    # layout listed _M1.._M3 keys that never existed, leaving that chart empty).
    "product": {**{label: ([f"ARGUS_PS_{k}_M{i}" for i in (1, 2, 3)] if k != "UCO_FCPO" else [f"ARGUS_PS_{k}"])
                   for k, label in _PS_LABELS.items()},
                "UCO ARA vs UCO China (arb)": ["ARGUS_PS_UCO_ARA_VS_CHINA"]},
    "outright": {
        "UCOME": [f"ARGUS_OUT_UCOME_M{i}" for i in (1, 2, 3)] + ["ARGUS_OUT_UCOME_MALACCA",
                                                               "ARGUS_OUT_UCOME_BARGE_PROMPT"],
        "FAME 0": [f"ARGUS_OUT_FAME0_M{i}" for i in (1, 2, 3)] + ["ARGUS_OUT_FAME0_ADVANCED"],
        "FAME -10": [f"ARGUS_OUT_FAME10_M{i}" for i in (1, 2, 3)],
        "HVO": [f"ARGUS_OUT_HVO_M{i}" for i in (1, 2, 3)] + ["ARGUS_OUT_HVO_PROMPT"],
        "HEFA-SPK": [f"ARGUS_OUT_HEFA_M{i}" for i in (1, 2, 3)],
        "Rapeseed": [f"ARGUS_OUT_RSO_OME_M{i}" for i in (1, 2, 3)] + ["ARGUS_OUT_RSO_EUR"],
        "Other feedstocks": ["ARGUS_OUT_POME_PROMPT", "ARGUS_OUT_RED_UCO_HYDROTREATING", "ARGUS_OUT_UCO_ARA",
                             "ARGUS_OUT_UCO_CHINA", "ARGUS_OUT_SME_ARGENTINA", "ARGUS_OUT_POME_REFINED_INDONESIA",
                             "ARGUS_OUT_POME_REFINED_MALAYSIA", "ARGUS_OUT_SBO_ARGENTINA", "ARGUS_OUT_UCOME_CHINA"],
    },
    "premium": {
        "7-28 Day Diff": ["ARGUS_PREM_FAME0_DIFF_GASOIL", "ARGUS_PREM_FAME10_DIFF_GASOIL",
                          "ARGUS_PREM_HVO_DIFF_GASOIL_M1", "ARGUS_PREM_HEFA_DIFF_GASOIL_M1",
                          "ARGUS_PREM_RSO_OME_DIFF_GASOIL", "ARGUS_PREM_SOYA_OME_DIFF_GASOIL",
                          "ARGUS_PREM_FAME0_ADVANCED_DIFF_GASOIL", "ARGUS_PREM_HVO4_DIFF_GASOIL_M1",
                          "ARGUS_PREM_UCOME_DIFF_GASOIL"],
    },
    "bunker": {
        "B24 (Marine UCOME/VLSFO)": ["ARGUS_BUNKER_BUNKER_B24_SINGAPORE", "ARGUS_BUNKER_BUNKER_B24_FUJAIRAH",
                                     "ARGUS_BUNKER_BUNKER_B24_GUANGZHOU"],
        "B30 (Marine UCOME/VLSFO)": ["ARGUS_BUNKER_BUNKER_B30_HOUSTON", "ARGUS_BUNKER_BUNKER_B30_LOSANGELES",
                                     "ARGUS_BUNKER_BUNKER_B30_SINGAPORE"],
    },
    "margin": {
        "BD (UCOME / RME)": ["ARGUS_EUROMARGIN_RPOME_UCOME", "ARGUS_EUROMARGIN_UCO_UCOME",
                             "ARGUS_EUROMARGIN_RSO_RME"],
        "HVO": ["ARGUS_EUROMARGIN_RPOME_HVO", "ARGUS_EUROMARGIN_UCO_HVO"],
        "SAF (HEFA-SPK)": ["ARGUS_EUROMARGIN_RPOME_SAF", "ARGUS_EUROMARGIN_UCO_SAF"],
    },
    "byproduct": {"Byproducts": ["ARGUS_BYPRODUCT_BIONAPHTHA_UCO", "ARGUS_BYPRODUCT_GLYCERINE_CHINA"]},
    "freight": {"Freight": ["ARGUS_FREIGHT_FREIGHT_CHINA_ARA", "ARGUS_FREIGHT_FREIGHT_SEASIA_ARA"]},
    "tickets": {
        "Blend margin — FAME / UCOME": [f"ARGUS_BLEND_{p}_{c}" for p in ("FAME", "UCOME") for c in ("UK", "DE", "NL")],
        "Blend margin — HVO": [f"ARGUS_BLEND_HVO_{c}" for c in ("UK", "DE", "NL")],
        "UK": ["ARGUS_TICKET_TKT_UK_CROP", "ARGUS_TICKET_TKT_UK_OTHER"],
        "Germany": ["ARGUS_TICKET_TKT_DE_ADVANCED", "ARGUS_TICKET_TKT_DE_ADV_SUBQUOTA",
                    "ARGUS_TICKET_TKT_DE_CONVENTIONAL", "ARGUS_TICKET_TKT_DE_OTHER"],
        "Netherlands": ["ARGUS_TICKET_TKT_NL_LRE_G", "ARGUS_TICKET_TKT_NL_LRE_C", "ARGUS_TICKET_TKT_NL_ZRE_G"],
    },
    "arb": {"China UCOME CIF Malaysia": ["ARGUS_ARB_CHINA_UCOME_CIF_MY"],
            "BD Arb vs Arg SME": ["ARGUS_ARB_BD_ARG_SME"]},
    "features": {
        "Feature 1 — $ Value / CFPP": [f"ARGUS_FEAT_CFPP_M{i}" for i in (1, 2, 3)],
        "Feature 2 — $ Value / GHG": [f"ARGUS_FEAT_GHG_M{i}" for i in (1, 2, 3)],
        "Feature 3 — UCOME Synthetic Cap": ([f"ARGUS_FEAT_SYNCAP_M{i}" for i in (1, 2, 3)]
                                            + [f"ARGUS_FEAT_SYNCAP_MULTIPLE_M{i}" for i in (1, 2, 3)]),
        "Advanced Premium": ["ARGUS_PREM_ADVANCED"],
    },
    "us": {"Compliance credits": ["ARGUS_US_US_CCA", "ARGUS_US_US_LCFS_SOY", "ARGUS_US_US_LCFS_UCO",
                                  "ARGUS_US_US_RIN_D4"],
           "US vegoil & biodiesel": ["ARGUS_US_US_SBO_GULF", "ARGUS_US_US_SBO_RBD_WC", "ARGUS_US_US_B99_NYH"]},
}

# Series computed for the waterfalls / calc boxes / Fwd Diff curves but not charted as seasonal cards.
INTERNAL_KEYS = ([f"ARGUS_BLENDTKT_{p}_{c}" for p in BLEND_PRODUCTS for c in BLEND_TICKET]
                 + ["ARGUS_FX_GBPUSD", "ARGUS_FX_EURUSD"]
                 + [f"ARGUS_PREMFWD_{p}_M{i}" for p in CURVE_PRODUCTS for i in (1, 2, 3)])

# Forward-curve sets (legacy ARGUS_CAL_FORWARD_GROUPS, ARGUS_PS_FORWARD_GROUPS, ARGUS_FWDDIFF_PRODUCTS)
CURVE_SETS: dict[str, list[tuple[str, str, list[str]]]] = {
    "calendar": [(code, label, [f"ARGUS_OUT_{code}_M{i}" for i in (1, 2, 3)])
                 for label, code in _CAL_PRODUCTS.items()],
    "product": [(k, label, [f"ARGUS_PS_{k}_M{i}" for i in (1, 2, 3)]) for k, label in _PS_LABELS.items()
                if k != "UCO_FCPO"],
    "fwd_diff": [(k, f"{label} minus Gasoil, by maturity", [f"ARGUS_PREMFWD_{code}_M{i}" for i in (1, 2, 3)])
                 for k, label, code in (("UCOME", "UCOME", "UCOME"), ("RME", "RME", "RSO_OME"),
                                        ("FAME0", "FAME 0", "FAME0"), ("FAME10", "FAME -10", "FAME10"),
                                        ("HVO", "HVO", "HVO"), ("SAF", "SAF", "HEFA"))],
}


def series_specs() -> list[dict]:
    """Catalog entries for every stored Argus series (module waste_bio)."""
    titles, ylabels = _build_titles(), _build_ylabels()
    for key, (label, unit) in US_DEFS.items():
        titles[f"ARGUS_US_{key}"] = label
        ylabels[f"ARGUS_US_{key}"] = unit
    titles.setdefault("ARGUS_OUT_UCOME_CHINA", PRODUCT_LABELS["UCOME_CHINA"])
    specs, seen = [], set()
    for section, _name in SECTIONS:
        for group, keys in LAYOUT.get(section, {}).items():
            for k in keys:
                if k in seen:
                    continue
                seen.add(k)
                specs.append({"key": k, "module": "waste_bio", "section": section, "group": group,
                              "title": titles.get(k, k), "unit": ylabels.get(k, "USD/MT")})
    for k in INTERNAL_KEYS:
        if k not in seen:
            seen.add(k)
            specs.append({"key": k, "module": "waste_bio", "section": "_internal", "group": "internal",
                          "title": titles.get(k, k), "unit": ylabels.get(k, "USD/MT")})
    return specs
