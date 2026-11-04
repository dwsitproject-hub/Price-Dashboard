"""RIC universe, products and unit conversions — ported from legacy/config.py.

This module is the single source of truth for instruments. `app.seed.bootstrap` syncs it into
market.products / market.instruments, and the engine reads conversion rules from here.
"""
from __future__ import annotations

from dataclasses import dataclass

# ── Products (futures) ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class Product:
    code: str
    display_name: str
    oi_display_name: str
    exchange: str
    native_unit: str
    conversion_type: str          # none | fx_div | factor
    conversion_factor: float | None
    conversion_fx_ric: str | None
    oi_contracts: int             # cumulative-OI contract range (legacy OI_CUMULATIVE_RANGE)
    oi_display_order: int
    sort_order: int


CENTSLB_CONV = 22.0462   # cents/lb      → USD/MT
S_CONV = 0.3674          # cents/bushel  → USD/MT
SM_CONV = 1.1023         # USD/short ton → USD/MT
HO_CONV = 264.172        # USD/gallon    → USD/MT
CL_CONV = 6.2898         # USD/barrel    → USD/MT
LCO_CONV = 6.2898        # USD/barrel    → USD/MT

PRODUCTS: dict[str, Product] = {p.code: p for p in [
    Product("FCPO", "MDEX Palm Oil", "FCPO Palm Oil", "Bursa Malaysia", "MYR/MT", "fx_div", None, "MYR=", 17, 1, 1),
    Product("BO", "CBOT Soybean Oil", "CBOT Soybean Oil", "CBOT", "cents/lb", "factor", CENTSLB_CONV, None, 18, 2, 2),
    Product("LGO", "ICE Gas Oil", "ICE Gas Oil", "ICE", "USD/MT", "none", None, None, 40, 3, 3),
    Product("DCP", "Dalian Palm Olein", "DCP Palm Olein", "DCE", "CNY/MT", "fx_div", None, "CNY=", 12, 4, 4),
    Product("DBY", "Dalian Soybean Oil", "Dalian Soybean Oil", "DCE", "CNY/MT", "fx_div", None, "CNY=", 8, 5, 5),
    Product("S", "CBOT Soybean", "CBOT Soybean", "CBOT", "cents/bushel", "factor", S_CONV, None, 17, 10, 6),
    Product("SM", "CBOT Soybean Meal", "CBOT Soybean Meal", "CBOT", "USD/short ton", "factor", SM_CONV, None, 18, 9, 7),
    Product("HO", "NYMEX Heating Oil", "NYMEX Heating Oil", "NYMEX", "USD/gallon", "factor", HO_CONV, None, 28, 7, 8),
    Product("CL", "NYMEX WTI", "NYMEX WTI", "NYMEX", "USD/barrel", "factor", CL_CONV, None, 87, 8, 9),
    Product("LCO", "ICE Brent", "ICE Brent", "ICE", "USD/barrel", "factor", LCO_CONV, None, 74, 6, 10),
]}

FUTURES_CORE_CONTRACTS = 7       # c1..c7 pulled for every product
FCPO_CURVE_CONTRACTS = 12        # FCPO c1..c12 (SND BMD row, legacy _EXTRA_FCPO)

# ── Cash / FX instruments ───────────────────────────────────────────────────


@dataclass(frozen=True)
class CashInstrument:
    ric: str
    category: str
    display_name: str
    native_unit: str
    conversion_type: str = "none"
    conversion_fx_ric: str | None = None
    price_basis: str = "last"


_MYR_CASH = {"CPO-MYSTH-M1", "CPO-MYSTH-M2"}

_CASH_DEFS: list[tuple[str, str, str]] = [
    # (ric, category, display name)  — names from legacy dashboard.py TITLES
    ("CPO-MYSTH-M1", "malaysia_cash", "Local Malaysia (PK/PG) CPO M1"),
    ("CPO-MYSTH-M2", "malaysia_cash", "Local Malaysia (PK/PG) CPO M2"),
    ("POL-MYRBD-M1", "malaysia_cash", "FOB Malaysia RBD Palm Olein M1"),
    ("POL-MYRBD-M2", "malaysia_cash", "FOB Malaysia RBD Palm Olein M2"),
    ("PO-MYRBD-M1", "malaysia_cash", "FOB Malaysia RBD Palm Oil M1"),
    ("PO-MYRBD-M2", "malaysia_cash", "FOB Malaysia RBD Palm Oil M2"),
    ("PSTR-MYRBD-M1", "malaysia_cash", "FOB Malaysia RBD Palm Stearin M1"),
    ("PSTR-MYRBD-M2", "malaysia_cash", "FOB Malaysia RBD Palm Stearin M2"),
    ("PFAD-MY-M1", "malaysia_cash", "FOB Malaysia PFAD M1"),
    ("PFAD-MY-M2", "malaysia_cash", "FOB Malaysia PFAD M2"),
    ("CPO-ID-M1", "indonesia_cash", "FOB Indonesia CPO M1"),
    ("CPO-ID-M2", "indonesia_cash", "FOB Indonesia CPO M2"),
    ("POL-IDRBD-M1", "indonesia_cash", "FOB Indonesia Palm Olein M1"),
    ("POL-IDRBD-M2", "indonesia_cash", "FOB Indonesia Palm Olein M2"),
    ("PO-IDRBD-M1", "indonesia_cash", "FOB Indonesia RBD Palm Oil M1"),
    ("PO-IDRBD-M2", "indonesia_cash", "FOB Indonesia RBD Palm Oil M2"),
    ("PSTR-IDRBD-M1", "indonesia_cash", "FOB Indonesia Stearin M1"),
    ("PSTR-IDRBD-M2", "indonesia_cash", "FOB Indonesia Stearin M2"),
    ("SOY-CTILL-US", "us_cash", "US Soybean Oil FOB Basis (Central Illinois)"),
    ("SM-ARGUPR-P1", "sa_cash", "FOB Argentina Soybean Meal P1"),
    ("S-ARGUPR-C1", "sa_cash", "FOB Argentina Soybeans C1"),
    ("BO-ARGUPR-C1", "sa_cash", "FOB Argentina Soybean Oil C1"),
    ("JCI-SOA-DALN", "china_cash", "China Dalian Cash Soybean Oil"),
    ("KTS-SOUABS-FOB", "blacksea_cash", "Sunflower Oil FOB Black Sea"),
]


def _cash(ric: str, category: str, name: str) -> CashInstrument:
    if ric in _MYR_CASH:
        return CashInstrument(ric, category, name, "MYR/MT", "fx_div", "MYR=")
    if ric == "JCI-SOA-DALN":
        return CashInstrument(ric, category, name, "CNY/MT", "fx_div", "CNY=")
    return CashInstrument(ric, category, name, "USD/MT")


CASH: dict[str, CashInstrument] = {r: _cash(r, c, n) for r, c, n in _CASH_DEFS}

FX: dict[str, CashInstrument] = {
    "MYR=": CashInstrument("MYR=", "fx", "USD/MYR FX Rate", "MYR per USD", price_basis="mid"),
    "CNY=": CashInstrument("CNY=", "fx", "USD/CNY FX Rate", "CNY per USD", price_basis="mid"),
    # USD per 1 GBP/EUR (Argus blend-margin calc, R2)
    "GBP=": CashInstrument("GBP=", "fx_usd", "GBP/USD FX Rate", "USD per GBP", price_basis="mid"),
    "EUR=": CashInstrument("EUR=", "fx_usd", "EUR/USD FX Rate", "USD per EUR", price_basis="mid"),
}
FX_RICS = set(FX)

# Synthetic FOB Malaysia CPO = (Local Malaysia CPO USD/MT + premium) × export-duty factor
CPO_FOB_PREM_USD = 10.0
CPO_FOB_DUTY_MULT = 1.10


def futures_ric(product: str, contract: int) -> str:
    return f"{product}c{contract}"


def core_futures_rics() -> list[str]:
    rics = [futures_ric(p, c) for p in PRODUCTS for c in range(1, FUTURES_CORE_CONTRACTS + 1)]
    rics += [futures_ric("FCPO", c) for c in range(FUTURES_CORE_CONTRACTS + 1, FCPO_CURVE_CONTRACTS + 1)]
    return rics


def oi_extended_rics() -> list[str]:
    core = set(core_futures_rics())
    return [futures_ric(p.code, c) for p in PRODUCTS.values()
            for c in range(1, p.oi_contracts + 1) if futures_ric(p.code, c) not in core]


def all_rics() -> list[str]:
    """Flat RIC list sent to LSEG (legacy config.ALL_RICS order: futures, FX, cash, OI-extended)."""
    return core_futures_rics() + list(FX) + list(CASH) + oi_extended_rics()


def parse_futures_ric(ric: str) -> tuple[str, int] | None:
    for code in sorted(PRODUCTS, key=len, reverse=True):
        prefix = f"{code}c"
        if ric.startswith(prefix) and ric[len(prefix):].isdigit():
            return code, int(ric[len(prefix):])
    return None


def instrument_conversion(ric: str) -> tuple[str, float | None, str | None, str]:
    """(conversion_type, factor, fx_ric, native_unit) for any RIC."""
    fut = parse_futures_ric(ric)
    if fut:
        p = PRODUCTS[fut[0]]
        return p.conversion_type, p.conversion_factor, p.conversion_fx_ric, p.native_unit
    if ric in CASH:
        c = CASH[ric]
        return c.conversion_type, None, c.conversion_fx_ric, c.native_unit
    if ric in FX:
        return "none", None, None, FX[ric].native_unit
    return "none", None, None, "USD/MT"
