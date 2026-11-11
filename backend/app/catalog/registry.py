"""Modules and derived-series registry — a port of legacy dashboard.py TITLES / SUBSECTIONS.

Series keys are identical to the legacy processed-CSV stems (e.g. ``BOPO_c3``, ``MAL_PREM_CPO``)
so golden tests can compare old and new output key by key.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.catalog.instruments import CASH, FX, PRODUCTS, instrument_conversion

# ── Modules (iam.modules) ───────────────────────────────────────────────────


@dataclass(frozen=True)
class Module:
    code: str
    name: str
    description: str
    nav_group: str      # sidebar grouping
    icon: str
    sort_order: int
    kind: str           # seasonal | overview | forward | oi | ta | snd | cftc | ai | news | calendar | atlas


MODULES: list[Module] = [
    Module("overview", "Market Overview", "Latest prices, day-on-day changes and data freshness.",
           "Markets", "gauge", 10, "overview"),
    Module("outright_prices", "Outright Prices", "Futures c3 and regional cash prices, seasonal overlay.",
           "Markets", "line", 20, "seasonal"),
    Module("forward_curves", "Forward Curves", "Futures and product-spread curves c1–c7 by year.",
           "Markets", "curve", 30, "forward"),
    Module("calendar_spreads", "Calendar Spreads", "Inter-month spreads per product and palm cash M1–M2.",
           "Spreads", "calendar", 40, "seasonal"),
    Module("product_spreads", "Product Spreads", "Inter-commodity, cash product and cross-geography spreads.",
           "Spreads", "swap", 50, "seasonal"),
    Module("basis_premiums", "Basis / Hedge Ratios",
           "Cash premiums vs FCPO c3, regional basis and tradable premiums.", "Spreads", "layers", 60, "seasonal"),
    Module("parity", "Parity", "Malaysia minus Indonesia cash parity; India and China import-parity snapshots.",
           "Spreads", "scale", 70, "seasonal"),
    Module("crush", "Crush Spread", "CBOT and South America soybean crush margins.",
           "Spreads", "factory", 80, "seasonal"),
    Module("leading_signals", "Leading Signals", "CME Oilshare — oil's share of crush revenue.",
           "Spreads", "signal", 90, "seasonal"),
    Module("waste_bio", "Waste / Bio", "Argus biofuel and waste-feedstock prices, spreads, margins, tickets and arbs.",
           "Spreads", "leaf", 95, "seasonal"),
    Module("oi_volume", "OI / Volume", "Cumulative open interest and volume per instrument.",
           "Positioning & TA", "bars", 100, "oi"),
    Module("technical_analysis", "Technical Analysis", "K-Means support/resistance levels and SMAs.",
           "Positioning & TA", "target", 110, "ta"),
    Module("cftc", "CFTC", "Commitments of Traders: managed money, swap dealer and producer positions.",
           "Positioning & TA", "users", 120, "cftc"),
    Module("snd", "SND", "Malaysia, Indonesia and India palm-oil supply & demand balances.",
           "Fundamentals", "layers", 130, "snd"),
    Module("hvo_atlas", "HVO / SAF Atlas", "Global renewable diesel and SAF plant atlas.",
           "Fundamentals", "globe", 140, "atlas"),
    Module("ai_analysis", "AI Analysis", "Seven-section quantitative read of any series, narrated by Claude.",
           "Intelligence", "sparkles", 150, "ai"),
    Module("news", "News", "Prices and sentiment extracted from broker, Oil World and energy reports.",
           "Intelligence", "file", 160, "news"),
    Module("calendar", "Calendar", "Scheduled market-moving releases and meetings, next 30 days.",
           "Intelligence", "calendar", 170, "calendar"),
]
MODULE_CODES = [m.code for m in MODULES]

ROLE_DEFAULT_MODULES = {
    "admin": MODULE_CODES,
    "analyst": MODULE_CODES,
    "viewer": ["overview", "outright_prices", "forward_curves", "calendar_spreads", "product_spreads",
               "parity"],
}

# ── Titles (legacy dashboard.py) ────────────────────────────────────────────

PRODUCT_DISPLAY = {p.code: p.display_name for p in PRODUCTS.values()}
PRODUCT_DISPLAY["C"] = "CBOT Corn"

RIC_TITLES: dict[str, str] = {f"{p}c3": f"{name} c3" for p, name in PRODUCT_DISPLAY.items()}
RIC_TITLES.update({ric: c.display_name for ric, c in CASH.items() if ric.endswith("-M2") or not ric[-3:-1] == "-M"})
RIC_TITLES.update({ric: f.display_name for ric, f in FX.items()})
RIC_TITLES["CPO-MYFOB-M2"] = "Synthetic FOB Malaysia CPO M2 ((Local+10)x1.10)"


def ric_display(ric: str) -> str:
    """Legacy _ric_display: TITLES → synthesised M1 from M2 → futures from PRODUCT_DISPLAY → RIC."""
    if ric in RIC_TITLES:
        return RIC_TITLES[ric]
    if ric.endswith("-M1") and (ric[:-3] + "-M2") in RIC_TITLES:
        return RIC_TITLES[ric[:-3] + "-M2"].replace(" M2", " M1")
    for code, display in PRODUCT_DISPLAY.items():
        prefix = f"{code}c"
        if ric.startswith(prefix) and ric[len(prefix):].isdigit():
            return f"{display} c{ric[len(prefix):]}"
    return ric


# ── Series definitions ──────────────────────────────────────────────────────


@dataclass
class SeriesDef:
    key: str
    module: str
    group: str
    title: str
    kind: str
    unit: str = "USD/MT"
    local_unit: str | None = None
    family: str | None = None
    tenor: str | None = None
    definition: dict = field(default_factory=dict)
    sort_order: int = 0
    source: str = "lseg"            # lseg = computed by the price pipeline; otherwise the Google Sheet source code
    section: str | None = None      # sub-view within a module (Waste/Bio sub-sections, Basis → Tradable Prem)


CALENDAR_LEGS = [("c1", "c3"), ("c3", "c5"), ("c5", "c7")]
CALENDAR_GROUPS = [
    ("Futures", ["FCPO", "BO", "LGO", "DCP", "DBY"]),
    ("Soybean", ["S"]), ("Soybean Meal", ["SM"]), ("Heating Oil", ["HO"]),
    ("Brent", ["LCO"]), ("WTI", ["CL"]),
]
CASH_M1M2_STEMS = ["CPO-MYSTH", "CPO-MYFOB", "POL-MYRBD", "PO-MYRBD", "PSTR-MYRBD", "PFAD-MY",
                   "CPO-ID", "POL-IDRBD", "PO-IDRBD", "PSTR-IDRBD"]

# (label, numerator product, denominator product) — legacy PRODUCT_SPREAD_DEFS / _PS_LEG_CODES
PRODUCT_SPREAD_DEFS = [
    ("BOPO", "BO", "FCPO"), ("BOGO", "BO", "LGO"), ("POGO", "FCPO", "LGO"),
    ("DCP-FCPO", "DCP", "FCPO"), ("DCP-BO", "DCP", "BO"), ("DCP-LGO", "DCP", "LGO"),
    ("DBY-FCPO", "DBY", "FCPO"), ("DBY-BO", "DBY", "BO"), ("DBY-LGO", "DBY", "LGO"),
    ("DCP-DBY", "DCP", "DBY"), ("SM-PO", "SM", "FCPO"), ("BOHO", "BO", "HO"),
    ("Brent-WTI", "LCO", "CL"), ("HO-LGO", "HO", "LGO"),
]
PS_CONTRACTS = ["c1", "c2", "c3", "c5", "c7"]

# (key, long leg RIC, short leg RIC) — legacy _SPREAD_LEG_RICS
LEG_SPREADS: dict[str, tuple[str, str]] = {
    "MAL_PREM_CPO": ("CPO-MYSTH-M2", "FCPOc3"), "MAL_PREM_CPO_FOB": ("CPO-MYFOB-M2", "FCPOc3"),
    "MAL_PREM_POL": ("POL-MYRBD-M2", "FCPOc3"), "MAL_PREM_PO": ("PO-MYRBD-M2", "FCPOc3"),
    "MAL_PREM_PSTR": ("PSTR-MYRBD-M2", "FCPOc3"), "MAL_PREM_PFAD": ("PFAD-MY-M2", "FCPOc3"),
    "IDO_PREM_CPO": ("CPO-ID-M2", "FCPOc3"), "IDO_PREM_POL": ("POL-IDRBD-M2", "FCPOc3"),
    "IDO_PREM_PO": ("PO-IDRBD-M2", "FCPOc3"), "IDO_PREM_PSTR": ("PSTR-IDRBD-M2", "FCPOc3"),
    "MALINDO_CPO": ("CPO-MYSTH-M1", "CPO-ID-M1"), "MALINDO_CPO_FOB": ("CPO-MYFOB-M1", "CPO-ID-M1"),
    "MALINDO_POL": ("POL-MYRBD-M1", "POL-IDRBD-M1"), "MALINDO_PO": ("PO-MYRBD-M1", "PO-IDRBD-M1"),
    "MALINDO_PSTR": ("PSTR-MYRBD-M1", "PSTR-IDRBD-M1"),
    "MAL_PROD_OLEIN_STEARIN": ("POL-MYRBD-M2", "PSTR-MYRBD-M2"),
    "MAL_PROD_OLEIN_PFAD": ("POL-MYRBD-M2", "PFAD-MY-M2"),
    "IDO_PROD_OLEIN_STEARIN": ("POL-IDRBD-M2", "PSTR-IDRBD-M2"),
    "SA_PREM_BO": ("BO-ARGUPR-C1", "BOc1"), "SA_BASIS_SOY": ("S-ARGUPR-C1", "Sc1"),
    "SA_BASIS_SM": ("SM-ARGUPR-P1", "SMc1"), "SA_ARGSBO_FCPO": ("BO-ARGUPR-C1", "FCPOc1"),
    "CN_BASIS_SOY_OIL": ("JCI-SOA-DALN", "DBYc1"),
    "SA-CN_BO": ("BO-ARGUPR-C1", "JCI-SOA-DALN"), "SA-SUN_BO": ("BO-ARGUPR-C1", "KTS-SOUABS-FOB"),
    "CPO-SUN": ("CPO-MYSTH-M1", "KTS-SOUABS-FOB"), "SA-CPO_BO": ("BO-ARGUPR-C1", "CPO-MYSTH-M1"),
    "CPOFOB-SUN": ("CPO-MYFOB-M1", "KTS-SOUABS-FOB"), "SA-CPOFOB_BO": ("BO-ARGUPR-C1", "CPO-MYFOB-M1"),
}

OUTRIGHT_GROUPS = [
    ("Futures", [f"{p}c3" for p in ["FCPO", "BO", "LGO", "DCP", "DBY", "S", "SM", "HO", "CL", "LCO"]]),
    ("Cash - Malaysia", ["CPO-MYSTH-M2", "CPO-MYFOB-M2", "POL-MYRBD-M2", "PO-MYRBD-M2", "PSTR-MYRBD-M2",
                         "PFAD-MY-M2"]),
    ("Cash - Indonesia", ["CPO-ID-M2", "POL-IDRBD-M2", "PO-IDRBD-M2", "PSTR-IDRBD-M2"]),
    ("Cash - US", ["SOY-CTILL-US"]),
    ("Cash - South America", ["SM-ARGUPR-P1", "S-ARGUPR-C1", "BO-ARGUPR-C1"]),
    ("Cash - China", ["JCI-SOA-DALN"]),
    ("Cash - Black Sea", ["KTS-SOUABS-FOB"]),
]


def _build() -> list[SeriesDef]:
    out: list[SeriesDef] = []
    order = 0

    def add(sd: SeriesDef) -> None:
        nonlocal order
        order += 1
        sd.sort_order = order
        out.append(sd)

    # Calendar spreads (process.py §1) — local units = product native unit
    for group, products in CALENDAR_GROUPS:
        for p in products:
            name = PRODUCT_DISPLAY[p]
            for a, b in CALENDAR_LEGS:
                title = (f"{name} {a.upper()} - {b.upper()}" if p == "FCPO" else f"{name} {a}-{b}")
                add(SeriesDef(f"{p}_{a}{b}", "calendar_spreads", group, title, "calendar",
                              local_unit=PRODUCTS[p].native_unit, family=p, tenor=f"{a}{b}",
                              definition={"op": "diff", "a": f"{p}{a}", "b": f"{p}{b}"}))
    for stem in CASH_M1M2_STEMS:
        base = ric_display(f"{stem}-M2")
        base = base[:-3] if base.endswith(" M2") else base
        if stem == "CPO-MYFOB":
            base = "Synthetic FOB Malaysia CPO"
        add(SeriesDef(f"{stem}_M1M2", "calendar_spreads", "Cash", f"{base} M1 - M2", "calendar",
                      tenor="M1M2", definition={"op": "diff", "a": f"{stem}-M1", "b": f"{stem}-M2"}))

    # Product spreads (process.py §2) — one series per label × tenor (contract picker)
    for label, a, b in PRODUCT_SPREAD_DEFS:
        for c in PS_CONTRACTS:
            title = f"{PRODUCT_DISPLAY[a]} - {PRODUCT_DISPLAY[b]} {c.upper()}"
            if label == "DCP-DBY" and c == "c3":
                title = "Palm Olein - Soybean Oil c3 (Dalian)"
            add(SeriesDef(f"{label}_{c}", "product_spreads", "Futures", title, "product", family=label,
                          tenor=c, definition={"op": "diff", "a": f"{a}{c}", "b": f"{b}{c}"}))

    def leg_series(key: str, module: str, group: str, kind: str) -> None:
        a, b = LEG_SPREADS[key]
        add(SeriesDef(key, module, group, f"{ric_display(a)} - {ric_display(b)}", kind,
                      definition={"op": "diff", "a": a, "b": b}))

    for k in ["MAL_PROD_OLEIN_STEARIN", "MAL_PROD_OLEIN_PFAD"]:
        leg_series(k, "product_spreads", "Malaysia", "cash_product")
    leg_series("IDO_PROD_OLEIN_STEARIN", "product_spreads", "Indonesia", "cash_product")
    for k in ["SA-CN_BO", "SA-SUN_BO", "CPO-SUN", "CPOFOB-SUN", "SA-CPO_BO", "SA-CPOFOB_BO"]:
        leg_series(k, "product_spreads", "Cross-Geography", "cross")

    # Basis & premiums (process.py §3, §4, §8–§10)
    for k in ["MAL_PREM_CPO", "MAL_PREM_CPO_FOB", "MAL_PREM_POL", "MAL_PREM_PO", "MAL_PREM_PSTR",
              "MAL_PREM_PFAD"]:
        leg_series(k, "basis_premiums", "Malaysia", "premium")
    for k in ["IDO_PREM_CPO", "IDO_PREM_POL", "IDO_PREM_PO", "IDO_PREM_PSTR"]:
        leg_series(k, "basis_premiums", "Indonesia", "premium")
    add(SeriesDef("US_BASIS_SOY_OIL", "basis_premiums", "US", "US Soybean Oil FOB Basis (Central Illinois)",
                  "basis", definition={"op": "value", "a": "SOY-CTILL-US"}))
    for k in ["SA_PREM_BO", "SA_BASIS_SOY", "SA_BASIS_SM", "SA_ARGSBO_FCPO"]:
        leg_series(k, "basis_premiums", "South America", "basis")
    leg_series("CN_BASIS_SOY_OIL", "basis_premiums", "China", "basis")

    # Parity → Malindo (process.py §5)
    for k in ["MALINDO_CPO", "MALINDO_CPO_FOB", "MALINDO_POL", "MALINDO_PO", "MALINDO_PSTR"]:
        leg_series(k, "parity", "Malaysia vs Indonesia", "malindo")

    # Crush (process.py §7, §7b) — raw units
    add(SeriesDef("CRUSH_c3", "crush", "CBOT Soybean Crush", "CBOT Crush Spread C3 ($/bushel)", "crush",
                  unit="$/bushel", definition={"op": "crush", "contract": "c3"}))
    add(SeriesDef("CRUSH_c1", "crush", "CBOT Soybean Crush", "CBOT Crush Spread C1 ($/bushel)", "crush",
                  unit="$/bushel", definition={"op": "crush", "contract": "c1"}))
    add(SeriesDef("SA_CRUSH", "crush", "South America", "SA Crush Spread ($/bushel)", "crush",
                  unit="$/bushel", definition={"op": "sa_crush"}))

    # Leading signals (process.py §12)
    add(SeriesDef("OIL_SHARE_c1", "leading_signals", "CBOT Soybean Crush", "CBOT Soybean Oilshare C1 (%)",
                  "oilshare", unit="%", definition={"op": "oilshare", "contract": "c1"}))
    add(SeriesDef("SA_OIL_SHARE", "leading_signals", "South America", "SA Oilshare — Up River FOB (%)",
                  "oilshare", unit="%", definition={"op": "sa_oilshare"}))

    # Outright prices — USD/MT value plus native-unit local value
    for group, rics in OUTRIGHT_GROUPS:
        for ric in rics:
            native = instrument_conversion(ric)[3]
            add(SeriesDef(ric, "outright_prices", group, ric_display(ric), "outright",
                          local_unit=native if native != "USD/MT" else None,
                          definition={"op": "outright", "a": ric}))
    return out


def _sheet_series(start: int) -> list[SeriesDef]:
    """Series read from the Google Sheet workbooks (app.sheets, docs/07-SHEET-SOURCES.md)."""
    from app.catalog.sheet_series import sheet_specs
    out = []
    for i, spec in enumerate(sheet_specs(), start=start + 1):
        out.append(SeriesDef(spec["key"], spec["module"], spec["group"], spec["title"], "sheet",
                             unit=spec.get("unit", "USD/MT"), local_unit=spec.get("local_unit"),
                             definition={"op": "sheet", "source": spec["source"], "section": spec.get("section")},
                             sort_order=i, source=spec["source"], section=spec.get("section")))
    return out


SERIES: list[SeriesDef] = _build()
SERIES += _sheet_series(len(SERIES))
LSEG_SERIES_KEYS: list[str] = [s.key for s in SERIES if s.source == "lseg"]
SERIES_BY_KEY: dict[str, SeriesDef] = {s.key: s for s in SERIES}

# Product-spread forward curves (legacy _build_product_spread_curves)
PS_FAMILIES = [label for label, _, _ in PRODUCT_SPREAD_DEFS]
FORWARD_CURVE_PRODUCTS = ["FCPO", "BO", "LGO", "DCP", "DBY", "S", "SM", "HO", "LCO", "CL"]

# Technical analysis instruments (legacy TA_INSTRUMENTS + TA_SPREAD_INSTRUMENTS)
TA_PRICE_INSTRUMENTS = ([f"{p}c3" for p in ["FCPO", "BO", "LGO", "DCP", "DBY", "S", "SM", "HO", "CL", "LCO"]]
                        + ["SOY-CTILL-US", "SM-ARGUPR-P1", "S-ARGUPR-C1", "BO-ARGUPR-C1", "JCI-SOA-DALN",
                           "KTS-SOUABS-FOB", "MYR=", "CNY="])
TA_SPREAD_INSTRUMENTS = ([f"{label}_c3" for label in PS_FAMILIES]
                         + ["CRUSH_c3", "SA_CRUSH", "SA-CN_BO", "SA-SUN_BO", "CPO-SUN", "SA-CPO_BO",
                            "US_BASIS_SOY_OIL", "SA_PREM_BO", "SA_BASIS_SOY", "SA_BASIS_SM", "CN_BASIS_SOY_OIL"])

OVERVIEW_TILES = ["FCPOc3", "BOc3", "LGOc3", "DCPc3", "DBYc3", "CLc3", "LCOc3", "Sc3", "SMc3", "HOc3",
                  "MYR=", "CNY="]
