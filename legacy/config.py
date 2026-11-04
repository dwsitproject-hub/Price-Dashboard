from datetime import date

# ── RIC list ──────────────────────────────────────────────────────────────────
RICS = {
    "futures": {
        "FCPO": ["FCPOc1", "FCPOc2", "FCPOc3", "FCPOc4", "FCPOc5", "FCPOc6", "FCPOc7"],
        "BO":   ["BOc1",   "BOc2",   "BOc3",   "BOc4",   "BOc5",   "BOc6",   "BOc7"],
        "LGO":  ["LGOc1",  "LGOc2",  "LGOc3",  "LGOc4",  "LGOc5",  "LGOc6",  "LGOc7"],
        "DCP":  ["DCPc1",  "DCPc2",  "DCPc3",  "DCPc4",  "DCPc5",  "DCPc6",  "DCPc7"],
        "DBY":  ["DBYc1",  "DBYc2",  "DBYc3",  "DBYc4",  "DBYc5",  "DBYc6",  "DBYc7"],
        "S":    ["Sc1",    "Sc2",    "Sc3",    "Sc4",    "Sc5",    "Sc6",    "Sc7"],
        "SM":   ["SMc1",   "SMc2",   "SMc3",   "SMc4",   "SMc5",   "SMc6",   "SMc7"],
        "HO":   ["HOc1",   "HOc2",   "HOc3",   "HOc4",   "HOc5",   "HOc6",   "HOc7"],
        "CL":   ["CLc1",   "CLc2",   "CLc3",   "CLc4",   "CLc5",   "CLc6",   "CLc7"],
        "LCO":  ["LCOc1",  "LCOc2",  "LCOc3",  "LCOc4",  "LCOc5",  "LCOc6",  "LCOc7"],
    },
    "crude": {},
    "fx": ["MYR=", "CNY="],
    # USD-per-unit quotes (GBP=, EUR= = USD per 1 GBP/EUR). Used by the Argus
    # blend-margin calc; kept separate from "fx" so dcp_gsheet_push is unchanged.
    "fx_usd": ["GBP=", "EUR="],
    "malaysia_cash": [
        "CPO-MYSTH-M1", "CPO-MYSTH-M2",
        "POL-MYRBD-M1", "POL-MYRBD-M2",
        "PO-MYRBD-M1",  "PO-MYRBD-M2",
        "PSTR-MYRBD-M1","PSTR-MYRBD-M2",
        "PFAD-MY-M1",   "PFAD-MY-M2",
    ],
    "indonesia_cash": [
        "CPO-ID-M1",    "CPO-ID-M2",
        "POL-IDRBD-M1", "POL-IDRBD-M2",
        "PO-IDRBD-M1",  "PO-IDRBD-M2",
        "PSTR-IDRBD-M1","PSTR-IDRBD-M2",
    ],
    "us_cash": [
        "SOY-CTILL-US",
    ],
    "sa_cash": [
        "SM-ARGUPR-P1",
        "S-ARGUPR-C1",
        "BO-ARGUPR-C1",
    ],
    "china_cash": [
        "JCI-SOA-DALN",
    ],
    "blacksea_cash": [
        "KTS-SOUABS-FOB",
    ],
}

# ── Helpers: flat lists for API calls ─────────────────────────────────────────
FX_RICS = set(RICS["fx"]) | set(RICS["fx_usd"])

FUTURES_RICS = [
    ric for rics in RICS["futures"].values() for ric in rics
]

CRUDE_RICS = [
    ric for rics in RICS["crude"].values() for ric in rics
]

_EXTRA_FCPO = [r for r in [f"FCPOc{i}" for i in range(1, 13)] if r not in FUTURES_RICS]

ALL_RICS = (
    FUTURES_RICS
    + _EXTRA_FCPO
    + CRUDE_RICS
    + RICS["fx"]
    + RICS["fx_usd"]
    + RICS["malaysia_cash"]
    + RICS["indonesia_cash"]
    + RICS["us_cash"]
    + RICS["sa_cash"]
    + RICS["china_cash"]
    + RICS["blacksea_cash"]
)

# RICs whose prices are in MYR and need converting to USD/MT
MYR_RICS = set(RICS["futures"]["FCPO"]) | {
    "CPO-MYSTH-M1", "CPO-MYSTH-M2",
}

# Synthetic FOB Malaysia CPO = (Local Malaysia (PK/PG) CPO, USD/MT + premium) * export-duty factor
CPO_FOB_PREM_USD = 10.0
CPO_FOB_DUTY_MULT = 1.10

# RICs priced in CNY that need converting to USD/MT
CNY_RICS = set(RICS["futures"]["DCP"]) | set(RICS["futures"]["DBY"]) | {"JCI-SOA-DALN"}

# RICs priced in cents/lb that need converting to USD/MT (multiply × 22.0462)
CENTSLB_RICS = set(RICS["futures"]["BO"])
CENTSLB_CONV = 22.0462   # cents/lb → USD/MT

# Unit conversion constants for new instruments
S_CONV  = 0.3674    # cents/bushel → USD/MT
SM_CONV = 1.1023    # USD/short ton → USD/MT
HO_CONV = 264.172   # USD/gallon → USD/MT

# RIC sets for new instrument conversions
S_RICS  = set(RICS["futures"]["S"])
SM_RICS = set(RICS["futures"]["SM"])
HO_RICS = set(RICS["futures"]["HO"])

CL_CONV  = 6.2898   # USD/barrel → USD/MT
CL_RICS  = set(RICS["futures"]["CL"])
LCO_CONV = 6.2898   # USD/barrel → USD/MT
LCO_RICS = set(RICS["futures"]["LCO"])

# FCPO full curve (c1-c12) for SND BMD row — fetched but not used in spreads/OI
FCPO_CURVE_RICS = [f"FCPOc{i}" for i in range(1, 13)]

# ── OI/Volume → Cumulative tab: extended per-product contract ranges ──────────
# "Total OI" for this tab sums every contract month with live Open Interest,
# not just c1-c7 (c1-c12 for FCPO). Ranges below were determined empirically
# 2026-09-28 via a one-time probe pull of c1-c48 per product: each value is the
# last contract-month RIC that LSEG still reports a non-null Open Interest for
# (beyond it, the RIC either returns no rows at all, or price-only rows with
# OI always null). Contract liquidity is NOT monotonic in the c-number for the
# quarterly-cycle products (DCP, BO, SM, S) — e.g. DCPc8 (~287k lots) far
# exceeds DCPc7 (~340 lots) — so every RIC up to the cutoff is included, not
# just the ones with visibly large OI.
OI_CUMULATIVE_RANGE = {
    "FCPO": 17,
    "DCP":  12,
    "BO":   18,
    "SM":   18,
    "S":    17,
    "LGO":  40,
    "HO":   28,   # NYMEX Heating Oil — probed 2026-09-28
    "CL":   87,   # NYMEX WTI — probed 2026-09-28 (far-dated Dec strips carry real OI)
    "LCO":  74,   # ICE Brent — probed 2026-09-28 (same far-dated Dec pattern)
    "DBY":  8,    # Dalian Soybean Oil — probed 2026-09-28
}
OI_CUMULATIVE_RICS = {
    prod: [f"{prod}c{i}" for i in range(1, n + 1)]
    for prod, n in OI_CUMULATIVE_RANGE.items()
}
_OI_CUM_ALL_RICS = [ric for rics in OI_CUMULATIVE_RICS.values() for ric in rics]

# RICs needed for the Cumulative OI tab that aren't already pulled elsewhere
# (RICS["futures"] only goes to c7, or c12 for FCPO via _EXTRA_FCPO above).
_EXTRA_OI_CUM = [
    r for r in _OI_CUM_ALL_RICS if r not in FUTURES_RICS and r not in _EXTRA_FCPO
]
ALL_RICS = ALL_RICS + _EXTRA_OI_CUM

# ── Date range ────────────────────────────────────────────────────────────────
START_DATE = "2015-01-01"
END_DATE   = date.today().isoformat()

# Incremental daily pull: fetch only the trailing LOOKBACK_DAYS window and merge
# it into the existing raw CSV (dedup on RIC + Trade Date, newest row wins).
# The full history (START_DATE) is used only for first-time backfill, when the
# raw CSV is missing, or when fetch.py is run with the --full flag.
LOOKBACK_DAYS = 7

# ── Tradable Premium tab ("Tradeable Prices Pull" Google Sheet) ─────────────────
# Tradable FOB price minus the raw Reuters Indo / Reuters Msia price, per product,
# for the Tradable Prem sub-tab. The sheet is private; the daily pull authenticates
# with a Google service-account key (see scripts/tradable_prem_data.py).
TRADABLE_PREM_SHEET_ID  = "1KBoMLaZa_Z4pUrluCccK7no-H0B9dBBFdydVSD056is"
TRADABLE_PREM_GID       = 0
TRADABLE_PREM_PRODUCTS  = ["CPO", "RPO", "ROL", "RPS", "PFAD"]

# ── Argus tab (Argus biofuels "Prices" Google Sheet) ────────────────────────────
# UCOME / FAME 0 / FAME -10 / HVO / RED SAF (HEFA-SPK) London-close swap curves
# (M1/M2/M3) + UCO China prompt + the two FAME 0 diff-to-gasoil series, all
# already USD/t. Private sheet; same service-account credential as Tradable Prem
# (see scripts/argus_data.py).
ARGUS_SHEET_ID = "1VfaXyEbok2X4rxn4W4tGekWIg_IiRpOjTtT4zyzgksc"
ARGUS_GID      = 78291884

# ── DCP daily price push (Google Sheet) ────────────────────────────────────────
# Writes the Dalian palm-olein settlement curve (DCPc1..DCPc6), converted to
# USD/MT, to a Google Sheet each daily run (scripts/dcp_gsheet_push.py).
# Reuses a Google service-account key with WRITE scope; share the target sheet
# with the service-account email as **Editor**. Leave the ID blank to disable
# (the push then no-ops, exactly like the Tradable Prem tab without its key).
DCP_GSHEET_ID        = "1zleqo_OlJ48AQbEZ87UcOW6Z3mKenCWDjDjG0QBx-Gg"
DCP_GSHEET_GID       = 705804153    # target tab (only this tab is written/cleared)
# Columns written to the "Dalian Px" tab, in order. All output is USD/MT; each
# product is converted per its native unit in dcp_gsheet_push.build_table():
#   DCP, DBY (CNY/MT)  -> divide by CNY= mid
#   FCPO   (MYR/MT)    -> divide by MYR= mid
#   LGO    (USD/MT)    -> already USD/MT, no conversion
# 1 date col + 25 price cols = 26 cols (fills the tab's A:Z exactly).
# Written to the "Dalian Px" Google Sheet tab (dcp_gsheet_push.py). Units are
# MIXED by product group: DCP/DBY native CNY/MT, FCPO native MYR/MT, LGO & BO
# USD/MT (BO converted cents/lb → USD/MT), plus the CNY=/MYR= FX mid rates as
# their own columns.
# Cash-price push (scripts/cash_gsheet_push.py): BO Argentine Upriver FOB c1,
# local Malaysia CPO M1, Black Sea sunflower oil FOB c1 -> a tab in the same workbook.
CASH_GSHEET_TAB        = "Cash Px"
CASH_GSHEET_START      = "2015-01-01"
CASH_GSHEET_FFILL_DAYS = 10   # sunflower assessment is sporadic; hold last print this many days

DCP_GSHEET_CONTRACTS = [
    "DCPc1", "DCPc2", "DCPc3", "DCPc4", "DCPc5", "DCPc6", "DCPc7",
    "DBYc1", "DBYc2", "DBYc3", "DBYc4", "DBYc5", "DBYc6", "DBYc7",
    "FCPOc1", "FCPOc2", "FCPOc3", "FCPOc4", "FCPOc5", "FCPOc6", "FCPOc7",
    "FCPOc8", "FCPOc9", "FCPOc10", "FCPOc11", "FCPOc12",
    "LGOc1", "LGOc2", "LGOc3", "LGOc4", "LGOc5", "LGOc6",
    "BOc1", "BOc2", "BOc3", "BOc4", "BOc5", "BOc6",
    "CNY=", "MYR=",
]