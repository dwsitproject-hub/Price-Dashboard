import sys
import os
import re
import glob
import shutil
import logging
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from datetime import date as _date
from sklearn.cluster import KMeans
from kneed import KneeLocator
from dash import Dash, Input, Output, State, dcc, html, dash_table, ALL, ctx
from dash.exceptions import PreventUpdate
from flask import send_file, abort

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import config
from gsheet_data import (
    fetch_india_data, INDIA_OUTRIGHT_KEYS, INDIA_SPREAD_KEYS,
    INDIA_TITLES, INDIA_SPREAD_DEFS,
)
from tradable_prem_data import load_tradable_prem
from argus_data import load_argus
import argus_data as _argus_mod
from calendar_data import load_calendar
from cftc_data import load_cftc, build_cftc_layout
from parity_data import (
    fetch_china_parity, fetch_india_parity,
    CHINA_PARITY_KEY, CHINA_PARITY_TITLE,
    INDIA_PARITY_KEYS, INDIA_PARITY_TITLES,
)
import hedging_data

PROC_DIR = ROOT / "data" / "processed"

# ── Margins tab: embed the standalone plant-margin dashboard HTML ─────────────
# The plant-margin dashboard is a self-contained Claude-artifact HTML kept in the
# sibling Biofuels/Margin folder. At startup we copy the newest dated file into
# assets/ (auto-served at /assets/…), injecting a small shim that hides its own
# internal tab bar and lets the parent page drive its internal tab via postMessage.
_MARGIN_SRC_DIR = ROOT.parent / "Biofuels" / "Margin"
_MARGIN_ASSET = ROOT / "assets" / "margin" / "plant_margin_dashboard.html"

# Injected shim (runs AFTER the artifact HTML's own scripts): (1) hide the HTML's own
# tab bar, (2) let the parent drive its internal tab via postMessage, (3) restore native
# file downloads. The HTML is a Claude-artifact export whose bridge installs a global
# click interceptor that hijacks every <a download> click (forwarding it to a parent
# frame that doesn't exist here) — so we override the app's global dl() to stop the
# event before it reaches that interceptor, letting the browser download normally.
_MARGIN_SHIM = """
<style>#tabs{display:none !important;}</style>
<script>
(function(){
  var TABS=['overview','vars','theory','flows','summary','rules'];
  function selectTab(t){
    if(TABS.indexOf(t)<0) return;
    var bar=document.getElementById('tabs');
    if(bar)[].forEach.call(bar.children,function(x){x.classList.toggle('on',x.dataset.t===t);});
    TABS.forEach(function(k){var s=document.getElementById('tab-'+k);
      if(s)s.classList.toggle('hidden',k!==t);});
    if(t==='flows'&&window.renderFlows)window.renderFlows();
    if(t==='rules'&&window.renderRules)window.renderRules();
  }
  window.addEventListener('message',function(e){
    if(e&&e.data&&e.data.type==='margins-select')selectTab(e.data.tab);
  });
  // Restore native downloads: replace the app's global dl() with one whose anchor
  // stops propagation (capture phase) so the artifact bridge's window-level click
  // interceptor never sees it and cannot preventDefault the download.
  window.dl = function(name,text,mime){
    var b=new Blob([text],{type:mime});
    var u=URL.createObjectURL(b);
    var a=document.createElement('a');
    a.href=u; a.download=name;
    a.addEventListener('click', function(ev){ ev.stopImmediatePropagation(); }, true);
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(function(){ URL.revokeObjectURL(u); }, 1000);
  };
})();
</script>
"""


def prepare_margins_asset():
    """Copy the newest plant_margin_dashboard_*.html into assets/, injecting the shim.
    Returns a cache-bust token (the source date), or None if no source file found.
    Fails soft — a missing/broken source logs a WARNING and never blocks startup."""
    try:
        cands = glob.glob(str(_MARGIN_SRC_DIR / "plant_margin_dashboard_*.html"))
        if not cands:
            logging.warning("prepare_margins_asset: no source HTML in %s", _MARGIN_SRC_DIR)
            return None

        # newest by embedded YYYYMMDD (fallback mtime)
        def _key(p):
            m = re.search(r"(\d{8})", os.path.basename(p))
            return (m.group(1) if m else "", os.path.getmtime(p))

        src = max(cands, key=_key)
        with open(src, encoding="utf-8") as fh:
            html_txt = fh.read()
        if "margins-select" not in html_txt:            # idempotent inject
            html_txt = html_txt.replace("</body>", _MARGIN_SHIM + "</body>", 1)
        os.makedirs(os.path.dirname(_MARGIN_ASSET), exist_ok=True)
        with open(_MARGIN_ASSET, "w", encoding="utf-8") as fh:
            fh.write(html_txt)
        m = re.search(r"(\d{8})", os.path.basename(src))
        return m.group(1) if m else "1"
    except Exception as e:  # noqa: BLE001 — never let this block dashboard startup
        logging.warning("prepare_margins_asset failed: %s", e)
        return None


_MARGIN_TOKEN = prepare_margins_asset()

# ── HVO/SAF Atlas tab: embed the standalone plant-atlas HTML ──────────────────
# Static reference map of global HVO / renewable-diesel / SAF plants, authored as a
# self-contained Claude-artifact HTML in the sibling Biofuels/Claude folder. Unlike
# the LSEG series this is NOT refreshed daily — it is copied verbatim at startup and
# only changes when the source file is re-saved. No shim is injected: the atlas has
# no internal tab bar, no file downloads and no artifact bridge to work around.
# Note: it pulls d3 / topojson / datamaps from cdnjs, so the browser needs internet.
_HVO_SRC = ROOT.parent / "Biofuels" / "Claude" / "hvo_saf_global_plant_atlas.html"
_HVO_ASSET = ROOT / "assets" / "hvo" / "hvo_saf_global_plant_atlas.html"


def prepare_hvo_asset():
    """Copy the plant-atlas HTML into assets/. Returns a cache-bust token (source
    mtime) or None if the source is missing. Fails soft — never blocks startup."""
    try:
        if not os.path.exists(_HVO_SRC):
            logging.warning("prepare_hvo_asset: source HTML not found at %s", _HVO_SRC)
            return None
        os.makedirs(os.path.dirname(_HVO_ASSET), exist_ok=True)
        shutil.copyfile(_HVO_SRC, _HVO_ASSET)   # byte-exact — nothing is injected
        return str(int(os.path.getmtime(_HVO_SRC)))
    except Exception as e:  # noqa: BLE001 — never let this block dashboard startup
        logging.warning("prepare_hvo_asset failed: %s", e)
        return None


_HVO_TOKEN = prepare_hvo_asset()

# ── Human-readable chart titles ───────────────────────────────────────────────

TITLES = {
    # Section 1 — Calendar spreads
    "FCPO_c1c3":  "MDEX Palm Oil C1 - C3",      "FCPO_c3c5":  "MDEX Palm Oil C3 - C5",      "FCPO_c5c7":  "MDEX Palm Oil C5 - C7",
    "BO_c1c3":    "CBOT Soybean Oil c1-c3",     "BO_c3c5":    "CBOT Soybean Oil c3-c5",     "BO_c5c7":    "CBOT Soybean Oil c5-c7",
    "LGO_c1c3":   "ICE Gas Oil c1-c3",          "LGO_c3c5":   "ICE Gas Oil c3-c5",          "LGO_c5c7":   "ICE Gas Oil c5-c7",
    "DCP_c1c3":   "Dalian Palm Olein c1-c3",    "DCP_c3c5":   "Dalian Palm Olein c3-c5",    "DCP_c5c7":   "Dalian Palm Olein c5-c7",
    "DBY_c1c3":   "Dalian Soybean Oil c1-c3",   "DBY_c3c5":   "Dalian Soybean Oil c3-c5",   "DBY_c5c7":   "Dalian Soybean Oil c5-c7",
    "S_c1c3":     "CBOT Soybean c1-c3",         "S_c3c5":     "CBOT Soybean c3-c5",         "S_c5c7":     "CBOT Soybean c5-c7",
    "SM_c1c3":    "CBOT Soybean Meal c1-c3",    "SM_c3c5":    "CBOT Soybean Meal c3-c5",    "SM_c5c7":    "CBOT Soybean Meal c5-c7",
    "HO_c1c3":    "NYMEX Heating Oil c1-c3",    "HO_c3c5":    "NYMEX Heating Oil c3-c5",    "HO_c5c7":    "NYMEX Heating Oil c5-c7",
    # Section 2 — Product spreads (c3)
    "SPREAD_BO_FCPO_c3":  "CBOT Soybean Oil - MDEX Palm Oil C3",
    "SPREAD_BO_LGO_c3":   "CBOT Soybean Oil - ICE Gas Oil C3",
    "SPREAD_FCPO_LGO_c3": "MDEX Palm Oil - ICE Gas Oil C3",
    "SPREAD_DCP_FCPO_c3": "DCE Palm Olein - MDEX Palm Oil C3",
    "SPREAD_DCP_LGO_c3":  "DCE Palm Olein - ICE Gas Oil C3",
    "SPREAD_DBY_FCPO_c3": "DCE Soybean Oil - MDEX Palm Oil C3",
    "SPREAD_DBY_LGO_c3":  "DCE Soybean Oil - ICE Gas Oil C3",
    "SMPOc3":             "CBOT Soybean Meal - MDEX Palm Oil C3",
    "BOHOc3":             "CBOT Soybean Oil - NYMEX Heating Oil C3",
    # Section 3 — Cash premiums Malaysia
    "MAL_PREM_CPO":  "CPO M2 Premium",
    "MAL_PREM_POL":  "Olein M2 Premium",
    "MAL_PREM_PO":   "Palm Oil M2 Premium",
    "MAL_PREM_PSTR": "Stearin M2 Premium",
    "MAL_PREM_PFAD": "PFAD M2 Premium",
    # Section 3 — Cash premiums Indonesia
    "IDO_PREM_CPO":  "CPO M2 Premium",
    "IDO_PREM_POL":  "Olein M2 Premium",
    "IDO_PREM_PO":   "Palm Oil M2 Premium",
    "IDO_PREM_PSTR": "Stearin M2 Premium",
    # Section 2b — Cash product spreads
    "MAL_PROD_OLEIN_STEARIN": "Olein M2 - Stearin M2",
    "MAL_PROD_OLEIN_PFAD":    "Olein M2 - PFAD M2",
    "IDO_PROD_OLEIN_STEARIN": "Olein M2 - Stearin M2",
    # Section 4 — Malindo
    "MALINDO_CPO":  "CPO M1",
    "MALINDO_POL":  "Olein M1",
    "MALINDO_PO":   "Palm Oil M1",
    "MALINDO_PSTR": "Stearin M1",
    # DCP-DBY spread
    "DCP-DBY_c3": "Palm Olein - Soybean Oil c3 (Dalian)",
    # Section 7 — Crush spread
    "CRUSH_c3": "CBOT Crush Spread C3 ($/bushel)",
    "CRUSH_c1": "CBOT Crush Spread C1 ($/bushel)",
    "SA_CRUSH": "SA Crush Spread ($/bushel)",
    # Leading Signals
    "OIL_SHARE_c1": "CBOT Soybean Oilshare C1 (%)",
    "SA_OIL_SHARE": "SA Oilshare — Up River FOB (%)",
    # Section 8 — US Cash Premium/Basis
    "US_BASIS_SOY_OIL": "US Soybean Oil FOB Basis (Central Illinois)",
    # Section 9 — SA Cash Premium/Basis
    "SA_PREM_BO": "South America Soybean Oil c1 - Soybean Oil c1",
    "SA_BASIS_SOY": "South America Soybeans c1 - CBOT Soybeans c1",
    "SA_BASIS_SM": "South America Soybean Meal - CBOT Soybean Meal c1",
    "SA_ARGSBO_FCPO": "FOB Argentina Soybean Oil c1 - MDEX Palm Oil c1",
    # Section 10 — China Cash Premium/Basis
    "CN_BASIS_SOY_OIL": "China Dalian Soybean Oil - Dalian Soybean Oil c1",
    # Section 11 — Cross-Geography product spreads
    "SA-CN_BO": "South America Soybean Oil c1 - China Dalian Soybean Oil",
    "SA-SUN_BO": "South America Soybean Oil c1 - Sunflower Oil FOB Black Sea",
    "CPO-SUN": "CPO Malaysia M1 - Sunflower Oil FOB Black Sea",
    "SA-CPO_BO": "South America Soybean Oil c1 - CPO Malaysia M1",
    "CPOFOB-SUN": "Synthetic FOB Malaysia CPO M1 - Sunflower Oil FOB Black Sea",
    "SA-CPOFOB_BO": "South America Soybean Oil c1 - Synthetic FOB Malaysia CPO M1",
    "CPO-MYFOB_M1M2": "Synthetic FOB Malaysia CPO M1 - M2",
    # Brent calendar spreads
    "LCO_c1c3": "ICE Brent c1-c3", "LCO_c3c5": "ICE Brent c3-c5", "LCO_c5c7": "ICE Brent c5-c7",
    # WTI calendar spreads
    "CL_c1c3": "NYMEX WTI c1-c3",  "CL_c3c5": "NYMEX WTI c3-c5",  "CL_c5c7": "NYMEX WTI c5-c7",
    # Brent-WTI spread
    "BRENTWTIc3": "ICE Brent - NYMEX WTI c3",
    # Section 5 — Outright prices: futures
    "FCPOc3": "MDEX Palm Oil c3",
    "BOc3":   "CBOT Soybean Oil c3",
    "LGOc3":  "ICE Gas Oil c3",
    "DCPc3":  "Dalian Palm Olein c3",
    "DBYc3":  "Dalian Soybean Oil c3",
    "Cc3":    "CBOT Corn c3",
    "Sc3":    "CBOT Soybean c3",
    "SMc3":   "CBOT Soybean Meal c3",
    "HOc3":   "NYMEX Heating Oil c3",
    "CLc3":   "NYMEX WTI c3",
    "LCOc3":  "ICE Brent c3",
    # FX rates
    "MYR=":   "USD/MYR FX Rate",
    "CNY=":   "USD/CNY FX Rate",
    # Section 5 — Outright prices: Malaysia cash
    "CPO-MYSTH-M2":  "Local Malaysia (PK/PG) CPO M2",
    "CPO-MYFOB-M2":  "Synthetic FOB Malaysia CPO M2 ((Local+10)x1.10)",
    "POL-MYRBD-M2":  "FOB Malaysia RBD Palm Olein M2",
    "PO-MYRBD-M2":   "FOB Malaysia RBD Palm Oil M2",
    "PSTR-MYRBD-M2": "FOB Malaysia RBD Palm Stearin M2",
    "PFAD-MY-M2":    "FOB Malaysia PFAD M2",
    # Section 5 — Outright prices: Indonesia cash
    "CPO-ID-M2":     "FOB Indonesia CPO M2",
    "POL-IDRBD-M2":  "FOB Indonesia Palm Olein M2",
    "PO-IDRBD-M2":   "FOB Indonesia RBD Palm Oil M2",
    "PSTR-IDRBD-M2": "FOB Indonesia Stearin M2",
    # Section 5 — Outright prices: US / SA / China / Black Sea cash
    "SOY-CTILL-US":   "US Soybean Oil FOB Basis (Central Illinois)",
    "SM-ARGUPR-P1":   "FOB Argentina Soybean Meal P1",
    "S-ARGUPR-C1":    "FOB Argentina Soybeans C1",
    "BO-ARGUPR-C1":   "FOB Argentina Soybean Oil C1",
    "JCI-SOA-DALN":   "China Dalian Cash Soybean Oil",
    "KTS-SOUABS-FOB": "Sunflower Oil FOB Black Sea",
}

# ── Product spread labels (Section 2) ────────────────────────────────────────

PRODUCT_SPREAD_LABELS = [
    "BOPO", "BOGO", "POGO",
    "DCP-FCPO", "DCP-BO", "DCP-LGO",
    "DBY-FCPO", "DBY-BO", "DBY-LGO",
    "DCP-DBY",
    "SM-PO", "BOHO", "Brent-WTI", "HO-LGO",
]
_PS_CONTRACTS = ["c1", "c2", "c3", "c5", "c7"]
_PS_LABELS_SET = set(PRODUCT_SPREAD_LABELS)

# Single source of truth: short product code -> display name. Used to derive
# outright titles (elsewhere in TITLES) and the product-spread leg names below.
PRODUCT_DISPLAY = {
    "FCPO": "MDEX Palm Oil",
    "BO":   "CBOT Soybean Oil",
    "LGO":  "ICE Gas Oil",
    "DCP":  "Dalian Palm Olein",
    "DBY":  "Dalian Soybean Oil",
    "S":    "CBOT Soybean",
    "SM":   "CBOT Soybean Meal",
    "HO":   "NYMEX Heating Oil",
    "CL":   "NYMEX WTI",
    "LCO":  "ICE Brent",
    "C":    "CBOT Corn",
}

# Product-spread legs (long code -> (numerator_code, denominator_code)).
# Display names are looked up from PRODUCT_DISPLAY, so renaming a leg
# propagates to every spread that references it.
_PS_LEG_CODES = {
    "BOPO":      ("BO",   "FCPO"),
    "BOGO":      ("BO",   "LGO"),
    "POGO":      ("FCPO", "LGO"),
    "BOHO":      ("BO",   "HO"),
    "DCP-FCPO":  ("DCP",  "FCPO"),
    "DCP-BO":    ("DCP",  "BO"),
    "DCP-LGO":   ("DCP",  "LGO"),
    "DBY-FCPO":  ("DBY",  "FCPO"),
    "DBY-BO":    ("DBY",  "BO"),
    "DBY-LGO":   ("DBY",  "LGO"),
    "DCP-DBY":   ("DCP",  "DBY"),
    "SM-PO":     ("SM",   "FCPO"),
    "Brent-WTI": ("LCO",  "CL"),
    "HO-LGO":    ("HO",   "LGO"),
}

_PS_DISPLAY_NAMES = {
    code: f"{PRODUCT_DISPLAY[a]} - {PRODUCT_DISPLAY[b]}"
    for code, (a, b) in _PS_LEG_CODES.items()
}

# Generate titles for all product-spread × contract combos
for _label in PRODUCT_SPREAD_LABELS:
    _display = _PS_DISPLAY_NAMES.get(_label, _label)
    for _c in _PS_CONTRACTS:
        TITLES[f"{_label}_{_c}"] = f"{_display} {_c.upper()}"

# Explicit override for DCP-DBY c3 (the only tenor with data)
TITLES["DCP-DBY_c3"] = "Palm Olein - Soybean Oil c3 (Dalian)"


# ── Cash premium / basis / Malindo: derive titles from constituent RIC labels ─
#
# Each cash premium or basis chart is a RIC-minus-RIC spread. Rather than
# hard-code a title, we look up each leg's display name in TITLES (falling back
# to PRODUCT_DISPLAY for futures and to M1/M2-swapped entries for cash) and
# render "{leg_a} - {leg_b}". Renaming an outright propagates everywhere.

def _ric_display(ric: str) -> str:
    if ric in TITLES:
        return TITLES[ric]
    # Cash RICs often exist only as M2 — synthesize M1 by substitution.
    if ric.endswith("-M1") and (ric[:-3] + "-M2") in TITLES:
        return TITLES[ric[:-3] + "-M2"].replace(" M2", " M1")
    # Futures like BOc1 / DBYc1 — derive from PRODUCT_DISPLAY.
    for _code, _display in PRODUCT_DISPLAY.items():
        _prefix = f"{_code}c"
        if ric.startswith(_prefix) and ric[len(_prefix):].isdigit():
            return f"{_display} c{ric[len(_prefix):]}"
    return ric


# (spread_key) -> (long_leg_ric, short_leg_ric) — spread = long - short.
_SPREAD_LEG_RICS = {
    # Malaysia cash premium vs FCPOc3
    "MAL_PREM_CPO":  ("CPO-MYSTH-M2",  "FCPOc3"),
    "MAL_PREM_CPO_FOB": ("CPO-MYFOB-M2", "FCPOc3"),
    "MAL_PREM_POL":  ("POL-MYRBD-M2",  "FCPOc3"),
    "MAL_PREM_PO":   ("PO-MYRBD-M2",   "FCPOc3"),
    "MAL_PREM_PSTR": ("PSTR-MYRBD-M2", "FCPOc3"),
    "MAL_PREM_PFAD": ("PFAD-MY-M2",    "FCPOc3"),
    # Indonesia cash premium vs FCPOc3
    "IDO_PREM_CPO":  ("CPO-ID-M2",     "FCPOc3"),
    "IDO_PREM_POL":  ("POL-IDRBD-M2",  "FCPOc3"),
    "IDO_PREM_PO":   ("PO-IDRBD-M2",   "FCPOc3"),
    "IDO_PREM_PSTR": ("PSTR-IDRBD-M2", "FCPOc3"),
    # Malindo (Malaysia - Indonesia, M1)
    "MALINDO_CPO":   ("CPO-MYSTH-M1",  "CPO-ID-M1"),
    "MALINDO_CPO_FOB": ("CPO-MYFOB-M1", "CPO-ID-M1"),
    "MALINDO_POL":   ("POL-MYRBD-M1",  "POL-IDRBD-M1"),
    "MALINDO_PO":    ("PO-MYRBD-M1",   "PO-IDRBD-M1"),
    "MALINDO_PSTR":  ("PSTR-MYRBD-M1", "PSTR-IDRBD-M1"),
    # Cash product spreads
    "MAL_PROD_OLEIN_STEARIN": ("POL-MYRBD-M2", "PSTR-MYRBD-M2"),
    "MAL_PROD_OLEIN_PFAD":    ("POL-MYRBD-M2", "PFAD-MY-M2"),
    "IDO_PROD_OLEIN_STEARIN": ("POL-IDRBD-M2", "PSTR-IDRBD-M2"),
    # SA basis spreads
    "SA_PREM_BO":       ("BO-ARGUPR-C1", "BOc1"),
    "SA_BASIS_SOY":     ("S-ARGUPR-C1",  "Sc1"),
    "SA_BASIS_SM":      ("SM-ARGUPR-P1", "SMc1"),
    # China basis
    "CN_BASIS_SOY_OIL": ("JCI-SOA-DALN", "DBYc1"),
    # Cross-geography product spreads
    "SA-CN_BO":  ("BO-ARGUPR-C1", "JCI-SOA-DALN"),
    "SA-SUN_BO": ("BO-ARGUPR-C1", "KTS-SOUABS-FOB"),
    "CPO-SUN":   ("CPO-MYSTH-M1", "KTS-SOUABS-FOB"),
    "SA-CPO_BO": ("BO-ARGUPR-C1", "CPO-MYSTH-M1"),
    "CPOFOB-SUN":   ("CPO-MYFOB-M1", "KTS-SOUABS-FOB"),
    "SA-CPOFOB_BO": ("BO-ARGUPR-C1", "CPO-MYFOB-M1"),
}
for _code, (_a, _b) in _SPREAD_LEG_RICS.items():
    TITLES[_code] = f"{_ric_display(_a)} - {_ric_display(_b)}"

# US basis is a single reported series, not a spread — keep descriptive title.
TITLES["US_BASIS_SOY_OIL"] = "US Soybean Oil FOB Basis (Central Illinois)"


# ── Y-axis unit overrides (for non-outright tabs) ─────────────────────────────

YLABEL_OVERRIDES = {
    "CRUSH_c3": "$/bushel",
    "CRUSH_c1": "$/bushel",
    "SA_CRUSH": "$/bushel",
    "OIL_SHARE_c1": "%",
    "SA_OIL_SHARE": "%",
}

# ── Sections with subsections ─────────────────────────────────────────────────

SUBSECTIONS = {
    "Calendar Spreads": {
        "Futures": [
            "FCPO_c1c3", "FCPO_c3c5", "FCPO_c5c7",
            "BO_c1c3",   "BO_c3c5",   "BO_c5c7",
            "LGO_c1c3",  "LGO_c3c5",  "LGO_c5c7",
            "DCP_c1c3",  "DCP_c3c5",  "DCP_c5c7",
            "DBY_c1c3",  "DBY_c3c5",  "DBY_c5c7",
        ],
        "Soybean": [
            "S_c1c3", "S_c3c5", "S_c5c7",
        ],
        "Soybean Meal": [
            "SM_c1c3", "SM_c3c5", "SM_c5c7",
        ],
        "Heating Oil": [
            "HO_c1c3", "HO_c3c5", "HO_c5c7",
        ],
        "Brent": [
            "LCO_c1c3", "LCO_c3c5", "LCO_c5c7",
        ],
        "WTI": [
            "CL_c1c3", "CL_c3c5", "CL_c5c7",
        ],
        "Cash": [
            "CPO-MYSTH_M1M2", "CPO-MYFOB_M1M2", "POL-MYRBD_M1M2", "PO-MYRBD_M1M2",
            "PSTR-MYRBD_M1M2", "PFAD-MY_M1M2",
            "CPO-ID_M1M2", "POL-IDRBD_M1M2", "PO-IDRBD_M1M2",
            "PSTR-IDRBD_M1M2",
        ],
    },
    "Product Spreads": {
        "Futures": PRODUCT_SPREAD_LABELS,
        "Malaysia": [
            "MAL_PROD_OLEIN_STEARIN",
            "MAL_PROD_OLEIN_PFAD",
        ],
        "Indonesia": [
            "IDO_PROD_OLEIN_STEARIN",
        ],
        "Cross-Geography": [
            "SA-CN_BO",
            "SA-SUN_BO",
            "CPO-SUN",
            "CPOFOB-SUN",
            "SA-CPO_BO",
            "SA-CPOFOB_BO",
        ],
        "India Local": INDIA_SPREAD_KEYS,
    },
    "Basis/Hedge Ratios": {
        "Malaysia": [
            "MAL_PREM_CPO", "MAL_PREM_CPO_FOB", "MAL_PREM_POL", "MAL_PREM_PO",
            "MAL_PREM_PSTR","MAL_PREM_PFAD",
        ],
        "Indonesia": [
            "IDO_PREM_CPO", "IDO_PREM_POL",
            "IDO_PREM_PO",  "IDO_PREM_PSTR",
        ],
        "US": [
            "US_BASIS_SOY_OIL",
        ],
        "South America": [
            "SA_PREM_BO",
            "SA_BASIS_SOY",
            "SA_BASIS_SM",
            "SA_ARGSBO_FCPO",
        ],
        "China": [
            "CN_BASIS_SOY_OIL",
        ],
    },
    "Malindo Spreads": {
        "Malaysia vs Indonesia": [
            "MALINDO_CPO", "MALINDO_CPO_FOB", "MALINDO_POL",
            "MALINDO_PO",  "MALINDO_PSTR",
        ],
    },
    "Crush Spread": {
        "CBOT Soybean Crush": [
            "CRUSH_c3", "CRUSH_c1",
        ],
        "South America": [
            "SA_CRUSH",
        ],
    },
    "Leading Signals": {
        "CBOT Soybean Crush": [
            "OIL_SHARE_c1",
        ],
        "South America": [
            "SA_OIL_SHARE",
        ],
    },
    "Outright Prices": {
        "Futures": [
            "FCPOc3", "BOc3", "LGOc3", "DCPc3", "DBYc3",
            "Sc3", "SMc3", "HOc3", "CLc3", "LCOc3",
        ],
        "Spreads": [
            "DCP-DBY_c3",
        ],
        "Cash - Malaysia": [
            "CPO-MYSTH-M2", "CPO-MYFOB-M2", "POL-MYRBD-M2", "PO-MYRBD-M2",
            "PSTR-MYRBD-M2","PFAD-MY-M2",
        ],
        "Cash - Indonesia": [
            "CPO-ID-M2", "POL-IDRBD-M2", "PO-IDRBD-M2", "PSTR-IDRBD-M2",
        ],
        "Cash - US": [
            "SOY-CTILL-US",
        ],
        "Cash - South America": [
            "SM-ARGUPR-P1", "S-ARGUPR-C1", "BO-ARGUPR-C1",
        ],
        "Cash - China": [
            "JCI-SOA-DALN",
        ],
        "Cash - Black Sea": [
            "KTS-SOUABS-FOB",
        ],
        "India Local": INDIA_OUTRIGHT_KEYS,
    },
}

# Flat SECTIONS for data lookups and ALL_YEARS computation
SECTIONS = {
    tab: [name for names in subs.values() for name in names]
    for tab, subs in SUBSECTIONS.items()
}

# ── Argus tab: 5 sub-tabs, each its own chart grid (like SUBSECTIONS, but keyed
# by the argus-sub-tabs value rather than by section-tabs). Data comes from the
# Argus "Prices" Google Sheet (argus_data.py) — see ARGUS_DATA below.
# Rebuilt 2026-09-08 for the reshaped source Excel (6 curve products + a
# Biofuel Premium sub-tab for the "diff to gasoil" quotes).
ARGUS_SUBSECTIONS = {
    "ar-calendar": {
        "UCOME":         ["ARGUS_CAL_UCOME_M1M2",   "ARGUS_CAL_UCOME_M2M3",   "ARGUS_CAL_UCOME_M1M3"],
        "FAME 0":        ["ARGUS_CAL_FAME0_M1M2",   "ARGUS_CAL_FAME0_M2M3",   "ARGUS_CAL_FAME0_M1M3"],
        "FAME -10":      ["ARGUS_CAL_FAME10_M1M2",  "ARGUS_CAL_FAME10_M2M3",  "ARGUS_CAL_FAME10_M1M3"],
        "HVO":           ["ARGUS_CAL_HVO_M1M2",     "ARGUS_CAL_HVO_M2M3",     "ARGUS_CAL_HVO_M1M3"],
        "HEFA-SPK":      ["ARGUS_CAL_HEFA_M1M2",    "ARGUS_CAL_HEFA_M2M3",    "ARGUS_CAL_HEFA_M1M3"],
        "Rapeseed OME":  ["ARGUS_CAL_RSO_OME_M1M2", "ARGUS_CAL_RSO_OME_M2M3", "ARGUS_CAL_RSO_OME_M1M3"],
    },
    "ar-product": {
        # All 15 pairwise combinations of the 6 curve products (added
        # 2026-09-24), each computed higher-precedence-minus-lower per
        # SAF/HEFA-SPK > HVO > UCOME > RME/Rapeseed OME > FAME -10 > FAME 0
        # — see PRODUCT_SPREAD_DEFS in argus_data.py.
        "HEFA-SPK vs HVO":          [f"ARGUS_PS_HEFA_HVO_M{i}"       for i in (1, 2, 3)],
        "HEFA-SPK vs UCOME":        [f"ARGUS_PS_HEFA_UCOME_M{i}"     for i in (1, 2, 3)],
        "HEFA-SPK vs Rapeseed OME": [f"ARGUS_PS_HEFA_RSO_OME_M{i}"   for i in (1, 2, 3)],
        "HEFA-SPK vs FAME -10":     [f"ARGUS_PS_HEFA_FAME10_M{i}"    for i in (1, 2, 3)],
        "HEFA-SPK vs FAME 0":       [f"ARGUS_PS_HEFA_FAME0_M{i}"     for i in (1, 2, 3)],
        "HVO vs UCOME":             [f"ARGUS_PS_HVO_UCOME_M{i}"      for i in (1, 2, 3)],
        "HVO vs Rapeseed OME":      [f"ARGUS_PS_HVO_RSO_OME_M{i}"    for i in (1, 2, 3)],
        "HVO vs FAME -10":          [f"ARGUS_PS_HVO_FAME10_M{i}"     for i in (1, 2, 3)],
        "HVO vs FAME 0":            [f"ARGUS_PS_HVO_FAME0_M{i}"      for i in (1, 2, 3)],
        "UCOME vs Rapeseed OME":    [f"ARGUS_PS_UCOME_RSO_OME_M{i}"  for i in (1, 2, 3)],
        "UCOME vs FAME -10":        [f"ARGUS_PS_UCOME_FAME10_M{i}"   for i in (1, 2, 3)],
        "UCOME vs FAME 0":          [f"ARGUS_PS_UCOME_FAME0_M{i}"    for i in (1, 2, 3)],
        "Rapeseed OME vs FAME -10": [f"ARGUS_PS_RSO_OME_FAME10_M{i}" for i in (1, 2, 3)],
        "Rapeseed OME vs FAME 0":   [f"ARGUS_PS_RSO_OME_FAME0_M{i}"  for i in (1, 2, 3)],
        "FAME -10 vs FAME 0":       [f"ARGUS_PS_FAME10_FAME0_M{i}"   for i in (1, 2, 3)],

        # Spreads outside the 6-product precedence rule above (unchanged).
        "UCOME vs UCO (China)":     [f"ARGUS_PS_UCOME_UCO_M{i}"    for i in (1, 2, 3)],
        "UCOME vs FCPO":            [f"ARGUS_PS_UCOME_FCPO_M{i}"   for i in (1, 2, 3)],
        "UCO (China) vs FCPO":      [f"ARGUS_PS_UCO_FCPO_M{i}"     for i in (1, 2, 3)],
        "UCOME vs Malacca (basis)": [f"ARGUS_PS_UCOME_VS_MALACCA_M{i}" for i in (1, 2, 3)],
        "UCOME vs Barge (basis)":   [f"ARGUS_PS_UCOME_VS_BARGE_M{i}"   for i in (1, 2, 3)],
        "UCO ARA vs UCO China (arb)": ["ARGUS_PS_UCO_ARA_VS_CHINA"],
    },
    "ar-outright": {
        "UCOME":            [f"ARGUS_OUT_UCOME_M{i}"  for i in (1, 2, 3)] + ["ARGUS_OUT_UCOME_MALACCA", "ARGUS_OUT_UCOME_BARGE_PROMPT"],
        "FAME 0":           [f"ARGUS_OUT_FAME0_M{i}"   for i in (1, 2, 3)] + ["ARGUS_OUT_FAME0_ADVANCED"],
        "FAME -10":         [f"ARGUS_OUT_FAME10_M{i}"  for i in (1, 2, 3)],
        "HVO":              [f"ARGUS_OUT_HVO_M{i}"     for i in (1, 2, 3)] + ["ARGUS_OUT_HVO_PROMPT"],
        "HEFA-SPK":         [f"ARGUS_OUT_HEFA_M{i}"    for i in (1, 2, 3)],
        "Rapeseed":         [f"ARGUS_OUT_RSO_OME_M{i}" for i in (1, 2, 3)] + ["ARGUS_OUT_RSO_EUR"],
        "Other feedstocks": ["ARGUS_OUT_POME_PROMPT", "ARGUS_OUT_RED_UCO_HYDROTREATING",
                              "ARGUS_OUT_UCO_ARA", "ARGUS_OUT_UCO_CHINA", "ARGUS_OUT_SME_ARGENTINA",
                              "ARGUS_OUT_POME_REFINED_INDONESIA", "ARGUS_OUT_POME_REFINED_MALAYSIA",
                              "ARGUS_OUT_SBO_ARGENTINA"],
    },
    "ar-premium": {
        "7-28 Day Diff": ["ARGUS_PREM_FAME0_DIFF_GASOIL", "ARGUS_PREM_FAME10_DIFF_GASOIL",
                           "ARGUS_PREM_HVO_DIFF_GASOIL_M1", "ARGUS_PREM_HEFA_DIFF_GASOIL_M1",
                           "ARGUS_PREM_RSO_OME_DIFF_GASOIL", "ARGUS_PREM_SOYA_OME_DIFF_GASOIL",
                           "ARGUS_PREM_FAME0_ADVANCED_DIFF_GASOIL", "ARGUS_PREM_HVO4_DIFF_GASOIL_M1",
                           "ARGUS_PREM_UCOME_DIFF_GASOIL"],
        "Fwd Diff": ["ARGUS_FWDDIFF_UCOME", "ARGUS_FWDDIFF_RME", "ARGUS_FWDDIFF_FAME0",
                     "ARGUS_FWDDIFF_FAME10", "ARGUS_FWDDIFF_HVO", "ARGUS_FWDDIFF_SAF"],
    },
    "ar-bunker": {
        "B24 (Marine UCOME/VLSFO)": ["ARGUS_BUNKER_BUNKER_B24_SINGAPORE", "ARGUS_BUNKER_BUNKER_B24_FUJAIRAH",
                                      "ARGUS_BUNKER_BUNKER_B24_GUANGZHOU"],
        "B30 (Marine UCOME/VLSFO)": ["ARGUS_BUNKER_BUNKER_B30_HOUSTON", "ARGUS_BUNKER_BUNKER_B30_LOSANGELES",
                                      "ARGUS_BUNKER_BUNKER_B30_SINGAPORE"],
    },
    "ar-margin": {
        "BD (UCOME / RME)": ["ARGUS_EUROMARGIN_RPOME_UCOME", "ARGUS_EUROMARGIN_UCO_UCOME",
                              "ARGUS_EUROMARGIN_RSO_RME"],
        "HVO":              ["ARGUS_EUROMARGIN_RPOME_HVO", "ARGUS_EUROMARGIN_UCO_HVO"],
        "SAF (HEFA-SPK)":   ["ARGUS_EUROMARGIN_RPOME_SAF", "ARGUS_EUROMARGIN_UCO_SAF"],
    },
    "ar-byproduct": {
        "Byproducts": ["ARGUS_BYPRODUCT_BIONAPHTHA_UCO", "ARGUS_BYPRODUCT_GLYCERINE_CHINA"],
    },
    "ar-freight": {
        "Freight": ["ARGUS_FREIGHT_FREIGHT_CHINA_ARA", "ARGUS_FREIGHT_FREIGHT_SEASIA_ARA"],
    },
    # Compliance tickets (added 2026-10-08): UK first, then Germany, then Netherlands.
    "ar-tickets": {
        # Blend margin first (added 2026-10-12): ticket value − (product − gasoil),
        # M1, USD/t. Columns of the 3-wide grid are UK | Germany | Netherlands.
        "FAME/UCOME ticket equivalent — blend margin: FAME (top row), UCOME (bottom row); UK (p/litre of fuel) | Germany (USD/t) | Netherlands (USD/t)":
            [f"ARGUS_BLEND_{p}_{c}" for p in ("FAME", "UCOME") for c in ("UK", "DE", "NL")],
        "HVO (renewable diesel) ticket equivalent — blend margin: UK (p/litre of fuel) | Germany (USD/t) | Netherlands (USD/t)":
            [f"ARGUS_BLEND_HVO_{c}" for c in ("UK", "DE", "NL")],
        "UK": ["ARGUS_TICKET_TKT_UK_CROP", "ARGUS_TICKET_TKT_UK_OTHER"],
        "Germany": ["ARGUS_TICKET_TKT_DE_ADVANCED", "ARGUS_TICKET_TKT_DE_ADV_SUBQUOTA",
                                "ARGUS_TICKET_TKT_DE_CONVENTIONAL", "ARGUS_TICKET_TKT_DE_OTHER"],
        "Netherlands": ["ARGUS_TICKET_TKT_NL_LRE_G", "ARGUS_TICKET_TKT_NL_LRE_C",
                                  "ARGUS_TICKET_TKT_NL_ZRE_G"],
    },
    # Arb tab: bypassed entirely by the is_argus/"ar-arb" early-return branch
    # in update_charts (quarterly MIP tables, not a chart grid — see
    # _argus_arb_tile()). Left empty rather than removed so argus_sub ==
    # "ar-arb" still resolves to a (harmless, unused) group if that branch
    # is ever skipped.
    "ar-arb": {},
    "ar-features": {
        "Feature 1 — $ Value / CFPP":         [f"ARGUS_FEAT_CFPP_M{i}" for i in (1, 2, 3)],
        "Feature 2 — $ Value / GHG":          [f"ARGUS_FEAT_GHG_M{i}"  for i in (1, 2, 3)],
        "Feature 3 — UCOME Synthetic Cap":    [f"ARGUS_FEAT_SYNCAP_M{i}" for i in (1, 2, 3)]
                                               + [f"ARGUS_FEAT_SYNCAP_MULTIPLE_M{i}" for i in (1, 2, 3)],
        "Advanced Premium":                   ["ARGUS_PREM_ADVANCED"],
    },
}

# Fwd Diff sub-section of Biofuel Premium (added 2026-09-21): each product's
# M1/M2/M3 minus Gasoil at the same maturity, rendered as a forward-curve-
# style chart (x = M1/M2/M3, one line per year) rather than the usual
# seasonal overlay — see make_argus_fwd_diff_chart / _argus_fwd_diff_wide.
# data_key -> (display label, [ARGUS_DATA series keys for M1, M2, M3]).
ARGUS_FWDDIFF_PRODUCTS = {
    "ARGUS_FWDDIFF_UCOME":  ("UCOME",    [f"ARGUS_PREMFWD_UCOME_M{i}"  for i in (1, 2, 3)]),
    "ARGUS_FWDDIFF_RME":    ("RME",      [f"ARGUS_PREMFWD_RSO_OME_M{i}" for i in (1, 2, 3)]),
    "ARGUS_FWDDIFF_FAME0":  ("FAME 0",   [f"ARGUS_PREMFWD_FAME0_M{i}"  for i in (1, 2, 3)]),
    "ARGUS_FWDDIFF_FAME10": ("FAME -10", [f"ARGUS_PREMFWD_FAME10_M{i}" for i in (1, 2, 3)]),
    "ARGUS_FWDDIFF_HVO":    ("HVO",      [f"ARGUS_PREMFWD_HVO_M{i}"    for i in (1, 2, 3)]),
    "ARGUS_FWDDIFF_SAF":    ("SAF",      [f"ARGUS_PREMFWD_HEFA_M{i}"   for i in (1, 2, 3)]),
}

# Argus → Calendar Spreads → Forward Curve (mirrors the top-level Calendar
# Spreads → Forward Curve tab, but for the 6 Argus curve products): outright
# M1/M2/M3 by product, one chart per product, one line per selected year.
# Same display names/order as ARGUS_SUBSECTIONS["ar-calendar"] above.
_ARGUS_CAL_PRODUCT_CODES = {
    "UCOME": "UCOME", "FAME 0": "FAME0", "FAME -10": "FAME10",
    "HVO": "HVO", "HEFA-SPK": "HEFA", "Rapeseed OME": "RSO_OME",
}
ARGUS_CAL_FORWARD_GROUPS = {
    label: [f"ARGUS_OUT_{code}_M{i}" for i in (1, 2, 3)]
    for label, code in _ARGUS_CAL_PRODUCT_CODES.items()
}

# Argus → Product Spreads → Forward Curve (mirrors the top-level Product
# Spreads → Forward Curve tab): reuses ARGUS_SUBSECTIONS["ar-product"]
# directly — every entry with a genuine 3-maturity M1/M2/M3 curve (i.e. not
# the single flat "UCO ARA vs UCO China (arb)" spread, which has no
# maturity dimension to plot) is forward-curve-able as-is.
ARGUS_PS_FORWARD_GROUPS = {
    label: keys for label, keys in ARGUS_SUBSECTIONS["ar-product"].items()
    if len(keys) == 3
}

# Outright Prices helpers
OUTRIGHT_FUTURES_SET = set(SUBSECTIONS["Outright Prices"]["Futures"])
OUTRIGHT_CASH_MYR    = {"CPO-MYSTH-M2"}   # only this cash RIC needs MYR conversion

OUTRIGHT_UNITS = {
    "FCPOc3": "MYR/MT",
    "BOc3":   "cents/lb",
    "LGOc3":  "USD/MT",
    "DCPc3":  "CNY/MT",
    "DBYc3":  "CNY/MT",
    "Sc3":    "cents/bushel",
    "SMc3":   "USD/short ton",
    "HOc3":   "USD/gallon",
    "CLc3":   "USD/barrel",
    "LCOc3":  "USD/barrel",
    "DCP-DBY_c3": "USD/MT",
    "SOY-CTILL-US":   "USD/MT",
    "SM-ARGUPR-P1":   "USD/MT",
    "S-ARGUPR-C1":    "USD/MT",
    "BO-ARGUPR-C1":   "USD/MT",
    "JCI-SOA-DALN":   "CNY/MT",
    "KTS-SOUABS-FOB": "USD/MT",
}   # cash instruments default to "USD/MT"

# ── Local-unit mapping for reverse conversion (USD/MT → native) ──────────────
# Documented record of each instrument's native currency/unit.
SPREAD_LOCAL_UNITS = {
    "FCPO": {"unit": "MYR/MT",        "type": "fx",     "fx_ric": "MYR="},
    "BO":   {"unit": "cents/lb",      "type": "factor", "divisor": 22.0462},
    "LGO":  {"unit": "USD/MT",        "type": "none"},
    "DCP":  {"unit": "CNY/MT",        "type": "fx",     "fx_ric": "CNY="},
    "DBY":  {"unit": "CNY/MT",        "type": "fx",     "fx_ric": "CNY="},
    "S":    {"unit": "cents/bushel",  "type": "factor", "divisor": 0.3674},
    "SM":   {"unit": "USD/short ton", "type": "factor", "divisor": 1.1023},
    "HO":   {"unit": "USD/gallon",    "type": "factor", "divisor": 264.172},
    "CL":   {"unit": "USD/barrel",    "type": "factor", "divisor": 6.2898},
    "LCO":  {"unit": "USD/barrel",    "type": "factor", "divisor": 6.2898},
}

OUTRIGHT_RICS = SECTIONS["Outright Prices"]


# ── Data loading ──────────────────────────────────────────────────────────────

def load_all_spreads() -> dict[str, pd.DataFrame]:
    data = {}
    for csv in sorted(PROC_DIR.glob("*.csv")):
        try:
            df = pd.read_csv(csv, parse_dates=["date"])
        except (ValueError, KeyError):
            continue
        if "date" not in df.columns:   # skip non-timeseries files (e.g. oi_summary)
            continue
        df = df.dropna(subset=["date"])   # drop blank/NaT-date rows (else year → NaN)
        df["year"]       = df["date"].dt.year
        df["day_of_year"] = df["date"].dt.dayofyear
        df["month_day"]  = df["date"].dt.strftime("%m-%d")
        data[csv.stem] = df
    print(f"Loaded {len(data)} spread files.")
    for key in ("FCPO_c1c3", "MAL_PREM_CPO"):
        if key in data:
            latest = data[key]["date"].max().strftime("%Y-%m-%d")
            print(f"  [{key}] latest date in memory: {latest}")
    return data


def load_outright_prices() -> dict[str, pd.DataFrame]:
    """
    Load outright prices for Section 6 instruments from all_instruments_raw.csv.
    - Futures: no conversion — show in local currency/unit.
    - CPO-MYSTH-M2 (MYR cash): divide by MYR= mid → USD/MT.
    - All other cash: already USD/MT.
    """
    raw_path = ROOT / "data" / "raw" / "all_instruments_raw.csv"
    if not raw_path.exists():
        print("WARNING: all_instruments_raw.csv not found — Outright Prices tab empty.")
        return {}

    needed = set(OUTRIGHT_RICS) | {"MYR="}
    df = pd.read_csv(raw_path, parse_dates=["Trade Date"])
    df = df.rename(columns={"Trade Date": "date"})
    df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    df = df[df["RIC"].isin(needed)]

    rows = []
    for _, row in df.iterrows():
        ric = row["RIC"]
        if ric == "MYR=":
            bid = pd.to_numeric(row.get("Bid"), errors="coerce")
            ask = pd.to_numeric(row.get("Ask"), errors="coerce")
            price = (bid + ask) / 2 if pd.notna(bid) and pd.notna(ask) else None
        else:
            last = pd.to_numeric(row.get("Last"), errors="coerce")
            sett = pd.to_numeric(row.get("Settlement Price"), errors="coerce")
            price = last if pd.notna(last) else sett
        rows.append({"date": row["date"], "RIC": ric, "price": price})

    long_df = pd.DataFrame(rows).drop_duplicates(subset=["date", "RIC"])
    wide    = long_df.pivot(index="date", columns="RIC", values="price").sort_index()

    if "MYR=" in wide.columns:
        wide["MYR="] = wide["MYR="].ffill()
    myr = wide["MYR="] if "MYR=" in wide.columns else None

    outright_data = {}
    for ric in OUTRIGHT_RICS:
        if ric not in wide.columns:
            print(f"  SKIP outright {ric} (no data)")
            continue
        series = wide[ric].copy()
        if ric in OUTRIGHT_CASH_MYR and myr is not None:
            series = series / myr          # MYR cash → USD/MT
        # Futures remain in local unit — no conversion
        series = series.dropna()
        if series.empty:
            continue
        ric_df = series.reset_index()
        ric_df.columns = ["date", "spread_value"]
        ric_df["year"]       = ric_df["date"].dt.year
        ric_df["day_of_year"] = ric_df["date"].dt.dayofyear
        ric_df["month_day"]  = ric_df["date"].dt.strftime("%m-%d")
        outright_data[ric] = ric_df

    print(f"Loaded {len(outright_data)} outright price series.")
    return outright_data


# ── Unit conversion helpers (local ↔ USD/MT) ────────────────────────────────

def _load_fx_rates() -> pd.DataFrame:
    """Load MYR= and CNY= mid rates from raw data, forward-filled."""
    raw_path = ROOT / "data" / "raw" / "all_instruments_raw.csv"
    if not raw_path.exists():
        return pd.DataFrame()
    df = pd.read_csv(raw_path, parse_dates=["Trade Date"])
    df = df.rename(columns={"Trade Date": "date"})
    df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    fx = df[df["RIC"].isin({"MYR=", "CNY="})].copy()
    fx["Bid"] = pd.to_numeric(fx["Bid"], errors="coerce")
    fx["Ask"] = pd.to_numeric(fx["Ask"], errors="coerce")
    fx["price"] = (fx["Bid"] + fx["Ask"]) / 2
    fx = fx.drop_duplicates(subset=["date", "RIC"])
    wide = fx.pivot(index="date", columns="RIC", values="price").sort_index().ffill()
    print(f"FX rates loaded: {len(wide)} rows, cols={list(wide.columns)}")
    return wide


def _fx_merge(df: pd.DataFrame, fx_rates: pd.DataFrame, fx_col: str) -> pd.Series:
    """Merge FX rate onto a spread DataFrame by date, return the rate series."""
    fx = fx_rates[[fx_col]].reset_index()
    fx.columns = ["date", fx_col]
    merged = df[["date"]].merge(fx, on="date", how="left")
    return merged[fx_col].ffill().bfill()


def _build_spreads_local(spreads: dict, fx_rates: pd.DataFrame) -> dict:
    """Pre-compute local-unit versions of calendar spreads."""
    cal_keys = set()
    for sub_keys in SUBSECTIONS["Calendar Spreads"].values():
        cal_keys.update(sub_keys)

    result = {}
    for key in cal_keys:
        if key not in spreads:
            continue
        prefix = key.split("_")[0]
        info = SPREAD_LOCAL_UNITS.get(prefix)
        if info is None or info["type"] == "none":
            result[key] = spreads[key]
            continue

        df = spreads[key].copy()
        if info["type"] == "factor":
            df["spread_value"] = df["spread_value"] / info["divisor"]
        elif info["type"] == "fx":
            fx_col = info["fx_ric"]
            if fx_rates.empty or fx_col not in fx_rates.columns:
                result[key] = spreads[key]
                continue
            rate = _fx_merge(df, fx_rates, fx_col)
            df["spread_value"] = df["spread_value"] * rate.values
        result[key] = df

    print(f"Pre-computed {len(result)} local-unit calendar spreads.")
    return result


# ── Forward Curve data ────────────────────────────────────────────────────────

FORWARD_CURVE_PRODUCTS = [
    "FCPO", "BO", "LGO", "DCP", "DBY",
    "S", "SM", "HO", "LCO", "CL",
]

FORWARD_CURVE_TITLES = {
    "FCPO": "MDEX Palm Oil",
    "BO":   "CBOT Soybean Oil",
    "LGO":  "ICE Gas Oil",
    "DCP":  "Dalian Palm Olein",
    "DBY":  "Dalian Soybean Oil",
    "S":    "CBOT Soybean",
    "SM":   "CBOT Soybean Meal",
    "HO":   "NYMEX Heating Oil",
    "LCO":  "ICE Brent",
    "CL":   "NYMEX WTI",
}

_CONTRACT_ORDER = ["c1", "c2", "c3", "c4", "c5", "c6", "c7"]


def load_forward_curves(fx_rates: pd.DataFrame) -> tuple[dict, dict]:
    """
    Load per-product forward curve time series (c1..c7) from raw data.

    Returns two dicts keyed by product (FCPO, BO, ...):
      - FORWARD_CURVES:       columns c1..c7 in USD/MT
      - FORWARD_CURVES_LOCAL: columns c1..c7 in native units
    Each DataFrame has a DatetimeIndex. Missing tenors are omitted columns.
    """
    raw_path = ROOT / "data" / "raw" / "all_instruments_raw.csv"
    usd_out: dict[str, pd.DataFrame] = {}
    local_out: dict[str, pd.DataFrame] = {}
    if not raw_path.exists():
        print("WARNING: all_instruments_raw.csv not found — Forward Curve empty.")
        return usd_out, local_out

    needed = set()
    for product in FORWARD_CURVE_PRODUCTS:
        needed.update(config.RICS["futures"][product])

    df = pd.read_csv(raw_path, parse_dates=["Trade Date"])
    df = df.rename(columns={"Trade Date": "date"})
    df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    df = df[df["RIC"].isin(needed)].copy()
    df["Last"] = pd.to_numeric(df.get("Last"), errors="coerce")
    df["Settlement Price"] = pd.to_numeric(df.get("Settlement Price"), errors="coerce")
    df["price"] = np.where(df["Last"].notna(), df["Last"], df["Settlement Price"])
    df = df.drop_duplicates(subset=["date", "RIC"])
    wide = df.pivot(index="date", columns="RIC", values="price").sort_index().ffill()

    for product in FORWARD_CURVE_PRODUCTS:
        rics = config.RICS["futures"][product]
        col_map = {ric[len(product):]: ric for ric in rics}

        present = {c: col_map[c] for c in _CONTRACT_ORDER if col_map.get(c) in wide.columns}
        if not present:
            print(f"  SKIP forward curve {product} (no contract columns in raw data)")
            continue

        local_df = wide[list(present.values())].rename(columns={v: k for k, v in present.items()})
        local_df = local_df.dropna(how="all")
        local_out[product] = local_df

        # USD/MT conversion — same rules used in convert_currencies().
        usd_df = local_df.copy()
        if product == "FCPO" and "MYR=" in fx_rates.columns:
            rate = fx_rates["MYR="].reindex(usd_df.index).ffill().bfill()
            usd_df = usd_df.div(rate, axis=0)
        elif product in ("DCP", "DBY") and "CNY=" in fx_rates.columns:
            rate = fx_rates["CNY="].reindex(usd_df.index).ffill().bfill()
            usd_df = usd_df.div(rate, axis=0)
        elif product == "BO":
            usd_df = usd_df * 22.0462
        elif product == "S":
            usd_df = usd_df * config.S_CONV
        elif product == "SM":
            usd_df = usd_df * config.SM_CONV
        elif product == "HO":
            usd_df = usd_df * config.HO_CONV
        elif product == "CL":
            usd_df = usd_df * config.CL_CONV
        elif product == "LCO":
            usd_df = usd_df * config.LCO_CONV
        # LGO is already USD/MT — no conversion
        usd_out[product] = usd_df

    print(f"Loaded {len(usd_out)} forward-curve series.")
    return usd_out, local_out


def _build_outrights_usdmt(outrights: dict, fx_rates: pd.DataFrame) -> dict:
    """Pre-compute USD/MT versions of outright futures prices."""
    result = {}
    for ric, df in outrights.items():
        conv = _TA_CONV.get(ric)
        if conv is None:
            result[ric] = df
            continue
        conv_type, factor = conv
        converted = df.copy()
        if conv_type == "mul":
            converted["spread_value"] = converted["spread_value"] * factor
        elif conv_type in ("myr", "cny"):
            fx_col = "MYR=" if conv_type == "myr" else "CNY="
            if fx_rates.empty or fx_col not in fx_rates.columns:
                result[ric] = df
                continue
            rate = _fx_merge(converted, fx_rates, fx_col)
            converted["spread_value"] = converted["spread_value"] / rate.values
        result[ric] = converted

    print(f"Pre-computed {len(result)} USD/MT outright price series.")
    return result


def _build_ta_wide_usdmt(ta_wide: pd.DataFrame, fx_rates: pd.DataFrame) -> pd.DataFrame:
    """Pre-compute USD/MT version of TA wide data."""
    if ta_wide.empty:
        return ta_wide

    result = ta_wide.copy()

    # Bring in MYR=/CNY= rates for converting MYR/CNY-priced instruments. Skip
    # any FX column already present (added as a displayed TA instrument) to avoid
    # a merge-suffix collision.
    if not fx_rates.empty:
        fx = fx_rates.reset_index()
        fx.columns = ["Date"] + list(fx_rates.columns)
        merge_cols = ["Date"] + [c for c in fx_rates.columns if c not in result.columns]
        if len(merge_cols) > 1:
            result = result.merge(fx[merge_cols], on="Date", how="left")
        for col in fx_rates.columns:
            if col in result.columns:
                result[col] = result[col].ffill().bfill()

    for ric, (conv_type, factor) in _TA_CONV.items():
        if ric not in result.columns:
            continue
        if conv_type == "mul":
            result[ric] = result[ric] * factor
        elif conv_type == "myr" and "MYR=" in result.columns:
            result[ric] = result[ric] / result["MYR="]
        elif conv_type == "cny" and "CNY=" in result.columns:
            result[ric] = result[ric] / result["CNY="]

    # Drop FX rate columns merged purely for conversion. Keep MYR=/CNY= when they
    # are themselves displayed TA instruments (shown as the raw rate, no convert).
    for col in ("MYR=", "CNY="):
        if col in result.columns and col not in TA_INSTRUMENTS:
            result = result.drop(columns=[col])

    print(f"Pre-computed TA USD/MT wide: {len(result)} rows")
    return result


# ── Color palette ─────────────────────────────────────────────────────────────

def year_color(year: int, all_years: list[int]) -> str:
    """Blue (oldest) → orange (newest)."""
    if not all_years:
        return "grey"
    idx = all_years.index(year)
    n   = max(len(all_years) - 1, 1)
    t   = idx / n
    r = int(30  + t * (255 - 30))
    g = int(100 + t * (140 - 100))
    b = int(180 - t * (180 - 30))
    return f"rgb({r},{g},{b})"


def year_width(year: int, all_years: list[int]) -> float:
    if not all_years:
        return 1.0
    most_recent = all_years[-1]
    if year == most_recent:
        return 2.5
    elif year == most_recent - 1:
        return 1.5
    return 1.0


def year_opacity(year: int, all_years: list[int]) -> float:
    if not all_years:
        return 1.0
    most_recent = all_years[-1]
    if year == most_recent:
        return 1.0
    age = most_recent - year
    return max(0.3, 1.0 - age * 0.07)


# ── Chart builder ─────────────────────────────────────────────────────────────

def make_chart(
    name: str,
    df: pd.DataFrame,
    selected_years: list[int],
    ylabel: str = "USD/MT",
) -> go.Figure:
    available_years = sorted(df["year"].unique().tolist())
    years_to_plot   = sorted(set(available_years) & set(selected_years))

    fig = go.Figure()

    for year in years_to_plot:
        yr_df   = df[df["year"] == year].sort_values("day_of_year")
        color   = year_color(year, years_to_plot)
        width   = year_width(year, years_to_plot)
        opacity = year_opacity(year, years_to_plot)

        x_dates = pd.to_datetime(
            yr_df["month_day"].apply(lambda d: f"2000-{d}"),
            format="%Y-%m-%d",
            errors="coerce",
        )

        fig.add_trace(go.Scatter(
            x=x_dates,
            y=yr_df["spread_value"],
            mode="lines",
            name=str(year),
            line=dict(color=color, width=width),
            opacity=opacity,
            hovertemplate=f"<b>{year}:</b> %{{y:.2f}} {ylabel}<extra></extra>",
        ))

    fig.update_layout(
        title=dict(text=TITLES.get(name, name), font=dict(size=13)),
        xaxis=dict(
            tickformat="%b",
            dtick="M1",
            ticklabelmode="period",
            showgrid=True,
            gridcolor="#e8e8e8",
            hoverformat="%d/%b",
        ),
        yaxis=dict(
            title=ylabel,
            showgrid=True,
            gridcolor="#e8e8e8",
            zeroline=True,
            zerolinecolor="#cccccc",
        ),
        legend=dict(orientation="v", x=1.01, y=1, font=dict(size=10)),
        margin=dict(l=50, r=80, t=40, b=40),
        plot_bgcolor="white",
        paper_bgcolor="white",
        hovermode="x unified",
        hoverlabel=dict(font=dict(size=10)),
        height=320,
    )
    return fig


def make_forward_curve_chart(
    product: str,
    curve_df: pd.DataFrame,
    selected_years: list[int],
    snapshot_md: str,
    ylabel: str,
) -> go.Figure:
    """
    Forward-curve snapshot chart: x-axis = c1..c7, one line per selected year,
    each sampled at (year-snapshot_md) using an as-of (<=) lookup.
    """
    fig = go.Figure()
    if curve_df is None or curve_df.empty:
        fig.update_layout(
            title=dict(text=f"{FORWARD_CURVE_TITLES.get(product, product)} — no data",
                       font=dict(size=13)),
            height=320,
        )
        return fig

    contracts = [c for c in _CONTRACT_ORDER if c in curve_df.columns]
    available_years = sorted({d.year for d in curve_df.index})
    years_to_plot = sorted(set(available_years) & set(selected_years))

    for year in years_to_plot:
        try:
            target = pd.Timestamp(f"{year}-{snapshot_md}")
        except ValueError:
            # e.g. 02-29 on a non-leap year — fall back to the prior day
            target = pd.Timestamp(f"{year}-02-28")
        yr_slice = curve_df.loc[curve_df.index.year == year]
        yr_slice = yr_slice.loc[yr_slice.index <= target]
        if yr_slice.empty:
            continue
        snap_date = yr_slice.index.max()
        row = yr_slice.loc[snap_date, contracts]
        if row.isna().all():
            continue

        color   = year_color(year, years_to_plot)
        width   = year_width(year, years_to_plot)
        opacity = year_opacity(year, years_to_plot)

        fig.add_trace(go.Scatter(
            x=contracts,
            y=row.values,
            mode="lines+markers",
            name=f"{year} ({snap_date.strftime('%Y-%m-%d')})",
            line=dict(color=color, width=width),
            marker=dict(size=6),
            opacity=opacity,
            hovertemplate=(
                f"<b>{year}</b><br>"
                f"As of {snap_date.strftime('%d %b %Y')}<br>"
                "%{x}: %{y:.2f} " + ylabel + "<extra></extra>"
            ),
        ))

    fig.update_layout(
        title=dict(
            text=f"{FORWARD_CURVE_TITLES.get(product, product)} Forward Curve",
            font=dict(size=13),
        ),
        xaxis=dict(
            type="category",
            categoryorder="array",
            categoryarray=contracts,
            showgrid=True,
            gridcolor="#e8e8e8",
        ),
        yaxis=dict(
            title=ylabel,
            showgrid=True,
            gridcolor="#e8e8e8",
            zeroline=False,
        ),
        legend=dict(orientation="v", x=1.01, y=1, font=dict(size=10)),
        margin=dict(l=50, r=80, t=40, b=40),
        plot_bgcolor="white",
        paper_bgcolor="white",
        hovermode="x unified",
        height=320,
    )
    return fig


_YTD_LEG_COLORS = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#8c564b"]


def _add_front_month_trace(fig: go.Figure, yr_df: pd.DataFrame, front_col: str,
                           front_label: str, ylabel: str) -> bool:
    """Overlay the front-month outright price on a secondary (right) y-axis
    of a YTD spread chart, for context — "is this spread move happening
    while the front month is rallying/selling off, or flat". Muted grey
    dashed line so it reads as context, not another spread leg. Returns
    True if a trace was added (caller still needs to add the yaxis2 layout
    even on an empty/no-data chart title, so this just reports success)."""
    px = yr_df[front_col].dropna() if front_col in yr_df.columns else pd.Series(dtype=float)
    if px.empty:
        return False
    fig.add_trace(go.Scatter(
        x=px.index, y=px.values, mode="lines", name=f"{front_label} Price",
        line=dict(color="#888888", width=1.5, dash="dot"),
        yaxis="y2",
        hovertemplate=f"<b>{front_label} Price:</b> %{{y:,.2f}} {ylabel}<extra></extra>",
    ))
    return True


def make_ytd_spread_chart(
    product: str,
    curve_df: pd.DataFrame,
    year: int,
    ylabel: str,
) -> go.Figure:
    """
    YTD calendar-spread chart: x-axis = actual trading dates within `year`,
    one line per c1-cN leg (c1-c2 .. c1-c7, left y-axis) so the term-
    structure of the calendar spread can be compared on a single shared
    y-axis scale, plus the C1 outright price overlaid on a secondary
    (right) y-axis for context.
    """
    title = FORWARD_CURVE_TITLES.get(product, product)
    fig = go.Figure()

    if curve_df is None or curve_df.empty or "c1" not in curve_df.columns or year is None:
        fig.update_layout(
            title=dict(text=f"{title} — no data", font=dict(size=13)), height=320)
        return fig

    yr_df = curve_df.loc[curve_df.index.year == year]
    if yr_df.empty:
        fig.update_layout(
            title=dict(text=f"{title} — no data for {year}", font=dict(size=13)),
            height=320)
        return fig

    for i, leg in enumerate(_CONTRACT_ORDER[1:]):  # c2..c7
        if leg not in yr_df.columns:
            continue
        spread = (yr_df["c1"] - yr_df[leg]).dropna()
        if spread.empty:
            continue
        leg_label = f"C1-{leg.upper()}"
        fig.add_trace(go.Scatter(
            x=spread.index,
            y=spread.values,
            mode="lines",
            name=leg_label,
            line=dict(color=_YTD_LEG_COLORS[i % len(_YTD_LEG_COLORS)], width=1.8),
            hovertemplate=f"<b>{leg_label}:</b> %{{y:.2f}} {ylabel}<extra></extra>",
        ))

    has_front_month = _add_front_month_trace(fig, yr_df, "c1", "C1", ylabel)

    fig.update_layout(
        title=dict(text=f"{title} — {year}", font=dict(size=13)),
        xaxis=dict(
            tickformat="%b", dtick="M1", showgrid=True, gridcolor="#e8e8e8",
            hoverformat="%d %b %Y",
        ),
        yaxis=dict(
            title=ylabel, showgrid=True, gridcolor="#e8e8e8",
            zeroline=True, zerolinecolor="#cccccc",
        ),
        yaxis2=dict(
            title=f"C1 Price ({ylabel})", overlaying="y", side="right",
            showgrid=False, zeroline=False,
        ) if has_front_month else {},
        # Legend moved below the chart (horizontal, wraps automatically) —
        # a right-side vertical legend would otherwise sit on top of the
        # C1-price y-axis's own ticks/title in that same right margin.
        legend=dict(orientation="h", x=0, y=-0.26, font=dict(size=9)),
        margin=dict(l=50, r=70, t=40, b=70),
        plot_bgcolor="white",
        paper_bgcolor="white",
        hovermode="x unified",
        hoverlabel=dict(font=dict(size=10)),
        height=340,
    )
    return fig


def make_argus_ytd_chart(
    title: str,
    curve_df: pd.DataFrame,
    year: int,
    ylabel: str = "USD/MT",
) -> go.Figure:
    """
    Argus → Calendar Spreads → YTD: same idiom as make_ytd_spread_chart
    (x-axis = actual trading dates within `year`, one line per leg off the
    front month, left y-axis), just M1-M2/M1-M3 instead of C1-C2..C1-C7
    since Argus curves only go out to M3, plus the M1 outright price
    overlaid on a secondary (right) y-axis for context.
    """
    fig = go.Figure()

    if curve_df is None or curve_df.empty or "M1" not in curve_df.columns or year is None:
        fig.update_layout(
            title=dict(text=f"{title} — no data", font=dict(size=13)), height=320)
        return fig

    yr_df = curve_df.loc[curve_df.index.year == year]
    if yr_df.empty:
        fig.update_layout(
            title=dict(text=f"{title} — no data for {year}", font=dict(size=13)),
            height=320)
        return fig

    for i, leg in enumerate(["M2", "M3"]):
        if leg not in yr_df.columns:
            continue
        spread = (yr_df["M1"] - yr_df[leg]).dropna()
        if spread.empty:
            continue
        leg_label = f"M1-{leg}"
        fig.add_trace(go.Scatter(
            x=spread.index,
            y=spread.values,
            mode="lines",
            name=leg_label,
            line=dict(color=_YTD_LEG_COLORS[i % len(_YTD_LEG_COLORS)], width=1.8),
            hovertemplate=f"<b>{leg_label}:</b> %{{y:.2f}} {ylabel}<extra></extra>",
        ))

    has_front_month = _add_front_month_trace(fig, yr_df, "M1", "M1", ylabel)

    fig.update_layout(
        title=dict(text=f"{title} — {year}", font=dict(size=13)),
        xaxis=dict(
            tickformat="%b", dtick="M1", showgrid=True, gridcolor="#e8e8e8",
            hoverformat="%d %b %Y",
        ),
        yaxis=dict(
            title=ylabel, showgrid=True, gridcolor="#e8e8e8",
            zeroline=True, zerolinecolor="#cccccc",
        ),
        yaxis2=dict(
            title=f"M1 Price ({ylabel})", overlaying="y", side="right",
            showgrid=False, zeroline=False,
        ) if has_front_month else {},
        # Legend moved below the chart (horizontal, wraps automatically) —
        # a right-side vertical legend would otherwise sit on top of the
        # M1-price y-axis's own ticks/title in that same right margin.
        legend=dict(orientation="h", x=0, y=-0.26, font=dict(size=9)),
        margin=dict(l=50, r=70, t=40, b=70),
        plot_bgcolor="white",
        paper_bgcolor="white",
        hovermode="x unified",
        hoverlabel=dict(font=dict(size=10)),
        height=340,
    )
    return fig


def make_ps_forward_curve_chart(
    label: str,
    curve_df: pd.DataFrame,
    selected_years: list[int],
    snapshot_md: str,
) -> go.Figure:
    """
    Product Spreads → Forward Curve: same idiom as the Calendar Spreads
    Forward Curve chart, but x-axis = the product-spread contract legs
    (c1/c2/c3/c5/c7 — only these are computed, see process.py Section 2)
    instead of the full c1..c7 outright curve, and the series is a spread
    (BO - FCPO, etc.) rather than an outright price.
    """
    title = _PS_DISPLAY_NAMES.get(label, label)
    fig = go.Figure()
    if curve_df is None or curve_df.empty:
        fig.update_layout(
            title=dict(text=f"{title} — no data", font=dict(size=13)), height=320)
        return fig

    contracts = [c for c in _PS_CONTRACTS if c in curve_df.columns]
    available_years = sorted({d.year for d in curve_df.index})
    years_to_plot = sorted(set(available_years) & set(selected_years))

    for year in years_to_plot:
        try:
            target = pd.Timestamp(f"{year}-{snapshot_md}")
        except ValueError:
            target = pd.Timestamp(f"{year}-02-28")
        yr_slice = curve_df.loc[curve_df.index.year == year]
        yr_slice = yr_slice.loc[yr_slice.index <= target]
        if yr_slice.empty:
            continue
        snap_date = yr_slice.index.max()
        row = yr_slice.loc[snap_date, contracts]
        if row.isna().all():
            continue

        color   = year_color(year, years_to_plot)
        width   = year_width(year, years_to_plot)
        opacity = year_opacity(year, years_to_plot)

        fig.add_trace(go.Scatter(
            x=contracts,
            y=row.values,
            mode="lines+markers",
            name=f"{year} ({snap_date.strftime('%Y-%m-%d')})",
            line=dict(color=color, width=width),
            marker=dict(size=6),
            opacity=opacity,
            hovertemplate=(
                f"<b>{year}</b><br>"
                f"As of {snap_date.strftime('%d %b %Y')}<br>"
                "%{x}: %{y:.2f} USD/MT<extra></extra>"
            ),
        ))

    fig.update_layout(
        title=dict(text=f"{title} Forward Curve", font=dict(size=13)),
        xaxis=dict(
            type="category", categoryorder="array", categoryarray=contracts,
            showgrid=True, gridcolor="#e8e8e8",
        ),
        yaxis=dict(
            title="USD/MT", showgrid=True, gridcolor="#e8e8e8", zeroline=True,
            zerolinecolor="#cccccc",
        ),
        legend=dict(orientation="v", x=1.01, y=1, font=dict(size=10)),
        margin=dict(l=50, r=80, t=40, b=40),
        plot_bgcolor="white",
        paper_bgcolor="white",
        hovermode="x unified",
        height=320,
    )
    return fig


def _snapshot_style(idx: int, n: int) -> tuple[str, float, float]:
    """Recency styling for a Parity snapshot chart: idx 0 = most recent
    ('Today', drawn boldest/orange), idx n-1 = oldest (faintest/blue) —
    same blue→orange ramp as year_color, just keyed by row recency instead
    of calendar year."""
    t = (n - 1 - idx) / max(n - 1, 1)
    r = int(30  + t * (255 - 30))
    g = int(100 + t * (140 - 100))
    b = int(180 - t * (180 - 30))
    color   = f"rgb({r},{g},{b})"
    width   = 2.5 if idx == 0 else (1.5 if idx == 1 else 1.0)
    opacity = max(0.35, 1.0 - idx * 0.15)
    return color, width, opacity


def make_snapshot_curve_chart(title: str, records: list, ylabel: str = "USD/MT") -> go.Figure:
    """Parity tab (Ind/China) forward-curve snapshot: x-axis = the sheet's
    own maturity labels (Spot/M1..M6, category order), one line per row of
    the sheet's rolling snapshot table (Today, D-1..D-4 or Today, W-1..W-4).
    `records` is [{"label": str, "x": [...], "y": [...]}, ...], Today first
    — same idiom as make_forward_curve_chart, but the trace dimension is
    "which snapshot row" instead of "which year"."""
    fig = go.Figure()
    if not records:
        fig.update_layout(
            title=dict(text=f"{title} — no data", font=dict(size=13)),
            height=320,
        )
        return fig

    # Category order = union of maturity labels across rows, in first-seen
    # order (a row missing a trailing maturity, e.g. a #DIV/0! M4, still
    # plots — it just won't extend as far right).
    category_order = []
    for rec in records:
        for lbl in rec["x"]:
            if lbl not in category_order:
                category_order.append(lbl)

    n = len(records)
    for idx, rec in enumerate(records):
        if not rec["x"]:
            continue
        color, width, opacity = _snapshot_style(idx, n)
        fig.add_trace(go.Scatter(
            x=rec["x"],
            y=rec["y"],
            mode="lines+markers",
            name=rec["label"],
            line=dict(color=color, width=width),
            marker=dict(size=6),
            opacity=opacity,
            hovertemplate=(
                f"<b>{rec['label']}</b><br>"
                "%{x}: %{y:.2f} " + ylabel + "<extra></extra>"
            ),
        ))

    fig.update_layout(
        title=dict(text=title, font=dict(size=13)),
        xaxis=dict(
            type="category",
            categoryorder="array",
            categoryarray=category_order,
            showgrid=True,
            gridcolor="#e8e8e8",
        ),
        yaxis=dict(
            title=ylabel,
            showgrid=True,
            gridcolor="#e8e8e8",
            zeroline=False,
        ),
        legend=dict(orientation="v", x=1.01, y=1, font=dict(size=10)),
        margin=dict(l=50, r=80, t=40, b=40),
        plot_bgcolor="white",
        paper_bgcolor="white",
        hovermode="x unified",
        height=320,
    )
    return fig


def _argus_fwd_diff_wide(series_keys: list[str]) -> pd.DataFrame:
    """3 ARGUS_DATA chart_dfs (one per maturity, each date+spread_value) ->
    one wide date-indexed DataFrame with columns M1/M2/M3, for
    make_argus_fwd_diff_chart. A maturity missing from ARGUS_DATA (e.g. the
    known HEFA-SPK M2 liquidity gap) simply produces a shorter/absent column
    — never an error."""
    cols = {}
    for i, key in enumerate(series_keys, start=1):
        cdf = ARGUS_DATA.get(key)
        if cdf is None or cdf.empty:
            continue
        cols[f"M{i}"] = cdf.set_index("date")["spread_value"]
    return pd.DataFrame(cols).sort_index() if cols else pd.DataFrame()


def _argus_fwd_diff_snapshot_rows(
    curve_df: pd.DataFrame,
    selected_years: list[int],
    snapshot_md: str,
) -> tuple[list[str], list[tuple[int, pd.Timestamp, pd.Series]]]:
    """Shared as-of (<=) year-snapshot lookup used by both
    make_argus_fwd_diff_chart and its companion table, so the two always
    show identical numbers. Returns (contracts present, [(year, snap_date,
    row) ...]) for years with at least one non-NaN maturity."""
    contracts = [c for c in ("M1", "M2", "M3") if c in curve_df.columns]
    if curve_df.empty or not contracts:
        return contracts, []

    available_years = sorted({d.year for d in curve_df.index})
    years_to_plot = sorted(set(available_years) & set(selected_years))

    rows = []
    for year in years_to_plot:
        try:
            target = pd.Timestamp(f"{year}-{snapshot_md}")
        except ValueError:
            target = pd.Timestamp(f"{year}-02-28")
        yr_slice = curve_df.loc[curve_df.index.year == year]
        yr_slice = yr_slice.loc[yr_slice.index <= target]
        if yr_slice.empty:
            continue
        snap_date = yr_slice.index.max()
        row = yr_slice.loc[snap_date, contracts]
        if row.isna().all():
            continue
        rows.append((year, snap_date, row))
    return contracts, rows


def make_argus_fwd_diff_chart(
    label: str,
    curve_df: pd.DataFrame,
    selected_years: list[int],
    snapshot_md: str,
    ylabel: str = "USD/MT",
) -> go.Figure:
    """Forward-curve-style chart for one Argus product's maturity-matched
    diff to Gasoil: x-axis = M1/M2/M3 (category), one line per selected
    year, each sampled at (year-snapshot_md) using an as-of (<=) lookup —
    same idiom as make_forward_curve_chart, just M1-M3 instead of c1-c7."""
    fig = go.Figure()
    contracts, snap_rows = _argus_fwd_diff_snapshot_rows(curve_df, selected_years, snapshot_md)
    if curve_df.empty or not contracts:
        fig.update_layout(
            title=dict(text=f"{label} minus Gasoil — no data", font=dict(size=13)),
            height=320,
        )
        return fig

    available_years = sorted({d.year for d in curve_df.index})
    years_to_plot = sorted(set(available_years) & set(selected_years))

    for year, snap_date, row in snap_rows:
        color   = year_color(year, years_to_plot)
        width   = year_width(year, years_to_plot)
        opacity = year_opacity(year, years_to_plot)

        fig.add_trace(go.Scatter(
            x=contracts,
            y=row.values,
            mode="lines+markers",
            name=f"{year} ({snap_date.strftime('%Y-%m-%d')})",
            line=dict(color=color, width=width),
            marker=dict(size=6),
            opacity=opacity,
            hovertemplate=(
                f"<b>{year}</b><br>"
                f"As of {snap_date.strftime('%d %b %Y')}<br>"
                "%{x}: %{y:.2f} " + ylabel + "<extra></extra>"
            ),
        ))

    fig.update_layout(
        title=dict(text=f"{label} minus Gasoil, by maturity", font=dict(size=13)),
        xaxis=dict(
            type="category",
            categoryorder="array",
            categoryarray=contracts,
            showgrid=True,
            gridcolor="#e8e8e8",
        ),
        yaxis=dict(
            title=ylabel,
            showgrid=True,
            gridcolor="#e8e8e8",
            zeroline=True,
            zerolinecolor="#cccccc",
        ),
        legend=dict(orientation="v", x=1.01, y=1, font=dict(size=10)),
        margin=dict(l=50, r=80, t=40, b=40),
        plot_bgcolor="white",
        paper_bgcolor="white",
        hovermode="x unified",
        height=320,
    )
    return fig


def make_argus_forward_curve_chart(
    title: str,
    curve_df: pd.DataFrame,
    selected_years: list[int],
    snapshot_md: str,
    ylabel: str = "USD/MT",
) -> go.Figure:
    """Generic Argus forward-curve chart: x-axis = M1/M2/M3 (category), one
    line per selected year, each sampled at (year-snapshot_md) via an as-of
    (<=) lookup — same idiom as make_forward_curve_chart / the outright
    Calendar-Spreads Forward Curve tab, just with a caller-supplied title
    instead of the "minus Gasoil" wording make_argus_fwd_diff_chart is
    hardcoded for. Reused for both Argus Calendar Spreads (outright by
    maturity) and Argus Product Spreads (spread by maturity) Forward Curve
    sub-tabs."""
    fig = go.Figure()
    contracts, snap_rows = _argus_fwd_diff_snapshot_rows(curve_df, selected_years, snapshot_md)
    if curve_df.empty or not contracts:
        fig.update_layout(
            title=dict(text=f"{title} — no data", font=dict(size=13)), height=320)
        return fig

    available_years = sorted({d.year for d in curve_df.index})
    years_to_plot = sorted(set(available_years) & set(selected_years))

    for year, snap_date, row in snap_rows:
        color   = year_color(year, years_to_plot)
        width   = year_width(year, years_to_plot)
        opacity = year_opacity(year, years_to_plot)

        fig.add_trace(go.Scatter(
            x=contracts,
            y=row.values,
            mode="lines+markers",
            name=f"{year} ({snap_date.strftime('%Y-%m-%d')})",
            line=dict(color=color, width=width),
            marker=dict(size=6),
            opacity=opacity,
            hovertemplate=(
                f"<b>{year}</b><br>"
                f"As of {snap_date.strftime('%d %b %Y')}<br>"
                "%{x}: %{y:.2f} " + ylabel + "<extra></extra>"
            ),
        ))

    fig.update_layout(
        title=dict(text=f"{title} Forward Curve", font=dict(size=13)),
        xaxis=dict(
            type="category", categoryorder="array", categoryarray=contracts,
            showgrid=True, gridcolor="#e8e8e8",
        ),
        yaxis=dict(
            title=ylabel, showgrid=True, gridcolor="#e8e8e8", zeroline=True,
            zerolinecolor="#cccccc",
        ),
        legend=dict(orientation="v", x=1.01, y=1, font=dict(size=10)),
        margin=dict(l=50, r=80, t=40, b=40),
        plot_bgcolor="white",
        paper_bgcolor="white",
        hovermode="x unified",
        height=320,
    )
    return fig


# Extended qualitative palette for the Forward-Curve "compare all
# instruments" chart below — cardinality there is open-ended (up to ~25
# outright products in one section), so unlike the dataviz skill's small
# fixed categorical set we cycle a larger qualitative list; identity is
# carried by the always-on legend + hover label, not by hue adjacency alone.
_COMPARE_COLORS = [
    "#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300",
    "#4a3aa7", "#e34948", "#17becf", "#bcbd22", "#8c564b", "#7f7f7f",
    "#aec7e8", "#ffbb78", "#98df8a", "#ff9896", "#c5b0d5", "#c49c94",
    "#f7b6d2", "#9edae5", "#393b79", "#637939", "#8c6d31", "#843c39",
]


def _fwd_snapshot_row(curve_df: pd.DataFrame, contracts: list, year, snapshot_md: str):
    """As-of (<=) snapshot lookup: the latest row within `year` on/before
    <year>-<snapshot_md>. Returns (snap_date, row) or (None, None) if there's
    nothing to plot — same logic as the per-product Forward Curve charts
    above, factored out so the compare-chart can reuse it per instrument."""
    if curve_df is None or curve_df.empty or year is None:
        return None, None
    try:
        target = pd.Timestamp(f"{year}-{snapshot_md}")
    except ValueError:
        target = pd.Timestamp(f"{year}-02-28")
    yr_slice = curve_df.loc[curve_df.index.year == year]
    yr_slice = yr_slice.loc[yr_slice.index <= target]
    if yr_slice.empty:
        return None, None
    snap_date = yr_slice.index.max()
    row = yr_slice.loc[snap_date, contracts]
    if row.isna().all():
        return None, None
    return snap_date, row


def make_fwd_compare_chart(
    title: str,
    items: list,
    contract_order: list,
    year,
    snapshot_md: str,
    ylabel: str = "USD/MT",
) -> go.Figure:
    """One chart overlaying EVERY instrument/product in a Forward-Curve
    section for a single selected year — x = tenor (shared across the
    section), one trace per instrument, so curve level and shape are
    directly comparable across the whole section at a glance. `items` =
    [(label, curve_df), ...]. Always plotted in the section's common unit
    (USD/MT) regardless of the Local-unit toggle elsewhere — mixed native
    units would make a cross-instrument comparison meaningless."""
    fig = go.Figure()
    if year is None or not items:
        fig.update_layout(
            title=dict(text=f"{title} — select a year", font=dict(size=14)),
            height=360, plot_bgcolor="white", paper_bgcolor="white",
        )
        return fig

    plotted = 0
    for i, (label, curve_df) in enumerate(items):
        if curve_df is None or curve_df.empty:
            continue
        contracts = [c for c in contract_order if c in curve_df.columns]
        if not contracts:
            continue
        snap_date, row = _fwd_snapshot_row(curve_df, contracts, year, snapshot_md)
        if snap_date is None:
            continue
        fig.add_trace(go.Scatter(
            x=contracts, y=row.values, mode="lines+markers", name=label,
            line=dict(color=_COMPARE_COLORS[i % len(_COMPARE_COLORS)], width=2),
            marker=dict(size=6),
            hovertemplate=(f"<b>{label}</b><br>as of {snap_date:%d %b %Y}<br>"
                           "%{x}: %{y:.2f} " + ylabel + "<extra></extra>"),
        ))
        plotted += 1

    fig.update_layout(
        title=dict(
            text=f"{title} — {year}" if plotted else f"{title} — no data for {year}",
            font=dict(size=15),
        ),
        xaxis=dict(type="category", categoryorder="array", categoryarray=contract_order,
                   showgrid=True, gridcolor="#e8e8e8"),
        yaxis=dict(title=ylabel, showgrid=True, gridcolor="#e8e8e8", zeroline=False),
        legend=dict(orientation="v", x=1.01, y=1, font=dict(size=10)),
        margin=dict(l=55, r=140, t=44, b=40),
        plot_bgcolor="white", paper_bgcolor="white",
        hovermode="x unified",
        height=420,
    )
    return fig


def make_argus_fwd_diff_table(
    label: str,
    curve_df: pd.DataFrame,
    selected_years: list[int],
    snapshot_md: str,
    ylabel: str = "USD/MT",
) -> html.Div:
    """Tabular companion to make_argus_fwd_diff_chart — same as-of
    year-snapshot values (via _argus_fwd_diff_snapshot_rows), one row per
    year (most recent first) so the numbers behind the chart's lines can be
    read precisely, columns M1/M2/M3. Carries the same title text as the
    chart (label passed above it, e.g. when this is switched into via the
    left nav there's no chart on screen to caption it)."""
    title = html.Div(f"{label} minus Gasoil, by maturity", style=_FWDDIFF_TITLE_STYLE)

    contracts, snap_rows = _argus_fwd_diff_snapshot_rows(curve_df, selected_years, snapshot_md)
    if curve_df.empty or not contracts or not snap_rows:
        return html.Div([
            title,
            html.Div(f"{label} minus Gasoil: no data.",
                     style={"padding": "12px", "color": "#aaa", "fontSize": "12px"}),
        ], style={
            "backgroundColor": "white",
            "borderRadius": "8px",
            "boxShadow": "0 1px 4px rgba(0,0,0,0.08)",
            "padding": "12px 16px",
        })

    header = html.Tr(
        [html.Th("Year", style=_FWDDIFF_TH_STYLE),
         html.Th("As of", style=_FWDDIFF_TH_STYLE)]
        + [html.Th(f"{c} ({ylabel})", style={**_FWDDIFF_TH_STYLE, "textAlign": "right"})
           for c in contracts]
    )

    body_rows = []
    for i, (year, snap_date, row) in enumerate(sorted(snap_rows, key=lambda r: r[0], reverse=True)):
        bg = "#f7f8fa" if i % 2 == 0 else "#ffffff"
        cells = [
            html.Td(str(year), style={**_FWDDIFF_TD_STYLE, "backgroundColor": bg, "fontWeight": "600"}),
            html.Td(snap_date.strftime("%Y-%m-%d"), style={**_FWDDIFF_TD_STYLE, "backgroundColor": bg}),
        ]
        for c in contracts:
            v = row.get(c)
            text = f"{v:.2f}" if pd.notna(v) else "—"
            cells.append(html.Td(text, style={**_FWDDIFF_TD_STYLE, "backgroundColor": bg, "textAlign": "right"}))
        body_rows.append(html.Tr(cells))

    table = html.Table([html.Thead(header), html.Tbody(body_rows)], style={
        "borderCollapse": "collapse",
        "width": "100%",
        "fontFamily": "Calibri, Arial, sans-serif",
        "fontSize": "12px",
    })
    return html.Div([title, html.Div(table, style={"overflowX": "auto"})], style={
        "backgroundColor": "white",
        "borderRadius": "8px",
        "boxShadow": "0 1px 4px rgba(0,0,0,0.08)",
        "padding": "12px 16px",
    })


_FWDDIFF_TITLE_STYLE = {
    "fontWeight": "700",
    "fontSize": "13px",
    "color": "#333",
    "marginBottom": "10px",
}

_FWDDIFF_TH_STYLE = {
    "padding": "8px 10px",
    "textAlign": "left",
    "fontWeight": "700",
    "fontSize": "11px",
    "color": "#555",
    "backgroundColor": "#f2f3f4",
    "borderBottom": "2px solid #d5d5d5",
    "textTransform": "uppercase",
    "letterSpacing": "0.05em",
}

_FWDDIFF_TD_STYLE = {
    "padding": "6px 10px",
    "borderBottom": "1px solid #eee",
}


# ── Technical Analysis helpers ────────────────────────────────────────────────

TA_INSTRUMENTS = [
    "FCPOc3", "BOc3", "LGOc3", "DCPc3", "DBYc3",
    "Sc3", "SMc3", "HOc3", "CLc3", "LCOc3",
    "SOY-CTILL-US", "SM-ARGUPR-P1", "S-ARGUPR-C1", "BO-ARGUPR-C1",
    "JCI-SOA-DALN", "KTS-SOUABS-FOB",
    "MYR=", "CNY=",   # FX rates (priced off Bid/Ask mid; shown as the raw rate)
]
TA_SPREAD_INSTRUMENTS = (
    [f"{label}_c3" for label in PRODUCT_SPREAD_LABELS]
    + ["CRUSH_c3", "SA_CRUSH", "SA-CN_BO", "SA-SUN_BO", "CPO-SUN", "SA-CPO_BO",
       "US_BASIS_SOY_OIL", "SA_PREM_BO", "SA_BASIS_SOY", "SA_BASIS_SM", "CN_BASIS_SOY_OIL"]
)
_TA_YEAR_OPTIONS = list(range(2015, _date.today().year + 1))
_TA_DEFAULT_YEAR = 2020

_TA_CONV = {
    "FCPOc3": ("myr", None),      # MYR/MT       → USD/MT
    "BOc3":   ("mul", 22.0462),   # cents/lb     → USD/MT
    "DCPc3":  ("cny", None),      # CNY/MT       → USD/MT
    "DBYc3":  ("cny", None),      # CNY/MT       → USD/MT
    "Sc3":    ("mul", 0.3674),    # cents/bushel → USD/MT
    "SMc3":   ("mul", 1.1023),    # USD/short ton → USD/MT
    "HOc3":   ("mul", 264.172),   # USD/gallon   → USD/MT
    "CLc3":   ("mul", 6.2898),    # USD/barrel   → USD/MT
    "LCOc3":  ("mul", 6.2898),    # USD/barrel   → USD/MT
    # LGOc3: already USD/MT
    "JCI-SOA-DALN": ("cny", None),   # CNY/MT → USD/MT
}


def _ta_load_wide() -> pd.DataFrame:
    """
    Load all_instruments_raw.csv once at startup. Resolve Last/Settlement prices,
    forward-fill, and return a wide DataFrame with columns: Date, FCPOc3, BOc3, ...
    Prices are kept in their native units (MYR/MT, cents/lb, CNY/MT, etc.) —
    no currency or unit conversion is applied.
    """
    raw_path = ROOT / "data" / "raw" / "all_instruments_raw.csv"
    if not raw_path.exists():
        return pd.DataFrame(columns=["Date"] + TA_INSTRUMENTS)

    needed = set(TA_INSTRUMENTS)
    df = pd.read_csv(raw_path, parse_dates=["Trade Date"])
    df = df.rename(columns={"Trade Date": "date"})
    df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    df = df[df["RIC"].isin(needed)].copy()

    df["Last"] = pd.to_numeric(df.get("Last"), errors="coerce")
    df["Settlement Price"] = pd.to_numeric(df.get("Settlement Price"), errors="coerce")
    df["price"] = np.where(df["Last"].notna(), df["Last"], df["Settlement Price"])

    # FX RICs (MYR=, CNY=) quote Bid/Ask only — resolve their price as the mid.
    fx_mask = df["RIC"].isin(config.FX_RICS)
    if fx_mask.any():
        bid = pd.to_numeric(df.get("Bid"), errors="coerce")
        ask = pd.to_numeric(df.get("Ask"), errors="coerce")
        df.loc[fx_mask, "price"] = (bid + ask) / 2

    df = df.drop_duplicates(subset=["date", "RIC"])
    wide = df.pivot(index="date", columns="RIC", values="price").sort_index()

    ta_cols = [c for c in TA_INSTRUMENTS if c in wide.columns]
    wide = wide[ta_cols].ffill()
    wide = wide.reset_index().rename(columns={"date": "Date"})
    print(f"TA wide loaded: {len(wide)} rows, {len(ta_cols)} instruments")
    return wide


def _ta_load_spread_wide() -> pd.DataFrame:
    """
    Load product spread c3 series from data/processed/ CSVs.
    Returns a wide DataFrame with columns: Date, BOPO_c3, BOGO_c3, ...
    Values are already in USD/MT — no conversion needed.
    """
    frames = {}
    for key in TA_SPREAD_INSTRUMENTS:
        csv_path = PROC_DIR / f"{key}.csv"
        if not csv_path.exists():
            print(f"  SKIP TA spread {key} (file not found)")
            continue
        df = pd.read_csv(csv_path, parse_dates=["date"])
        df = df.rename(columns={"spread_value": key})
        frames[key] = df.set_index("date")[key]

    if not frames:
        print("TA spread wide: no data loaded.")
        return pd.DataFrame(columns=["Date"] + TA_SPREAD_INSTRUMENTS)

    wide = pd.DataFrame(frames).sort_index().ffill()
    wide = wide.reset_index().rename(columns={"date": "Date"})
    loaded = [c for c in TA_SPREAD_INSTRUMENTS if c in wide.columns]
    print(f"TA spread wide loaded: {len(wide)} rows, {len(loaded)} instruments")
    return wide


# ── Verbatim from AutomatedTechnicalAnalysisPaperMarkets.py ──────────────────

def calculate_local_extrema(data, high_col, low_col, window_size=30, step_size=5):
    if len(data) < window_size:
        return np.array([]).reshape(-1, 1), np.array([]).reshape(-1, 1)

    local_minimas, local_maximas = [], []
    for i in range(0, len(data) - window_size + 1, step_size):
        low_window  = data[low_col].iloc[i:i + window_size]
        high_window = data[high_col].iloc[i:i + window_size]
        local_minimas.append(low_window.min())
        local_maximas.append(high_window.max())

    return np.array(local_minimas).reshape(-1, 1), np.array(local_maximas).reshape(-1, 1)


def find_optimal_clusters(data, max_k=6):
    if len(data) == 0:
        return 4

    wcss = []
    for k in range(1, max_k + 1):
        kmeans = KMeans(n_clusters=k, random_state=0, n_init=10)
        kmeans.fit(data)
        wcss.append(kmeans.inertia_)

    kneedle = KneeLocator(range(1, max_k + 1), wcss, curve="convex", direction="decreasing")
    return kneedle.elbow if kneedle.elbow else 3


def calculate_ranked_levels(data, high_col, low_col):
    local_minimas, local_maximas = calculate_local_extrema(data, high_col, low_col)

    optimal_k_min = find_optimal_clusters(local_minimas)
    optimal_k_max = find_optimal_clusters(local_maximas)

    kmeans_min = KMeans(n_clusters=optimal_k_min, random_state=0, n_init=10).fit(local_minimas)
    kmeans_max = KMeans(n_clusters=optimal_k_max, random_state=0, n_init=10).fit(local_maximas)

    levels = np.concatenate([
        kmeans_min.cluster_centers_.flatten(),
        kmeans_max.cluster_centers_.flatten(),
    ])
    return np.sort(levels)


def create_instrument_plot(data, ranked_levels, instrument_name,
                           open_col, high_col, low_col, last_col,
                           chart_type='line', ylabel='USD/MT'):
    if len(data) == 0:
        return go.Figure()

    data = data.copy()
    data[f'{instrument_name}_SMA_50']  = data[last_col].rolling(window=50).mean()
    data[f'{instrument_name}_SMA_100'] = data[last_col].rolling(window=100).mean()
    data[f'{instrument_name}_SMA_200'] = data[last_col].rolling(window=200).mean()

    trace_price = go.Scatter(
        x=data['Date'], y=data[last_col],
        mode='lines', name='Price',
    )

    level_traces = [
        go.Scatter(
            x=data['Date'], y=[lvl] * len(data),
            mode='lines', name=f'Level {i + 1}',
            line=dict(color='purple', width=2, dash='dash'),
        )
        for i, lvl in enumerate(ranked_levels)
    ]

    trace_sma_50  = go.Scatter(x=data['Date'], y=data[f'{instrument_name}_SMA_50'],
                               mode='lines', name='50D SMA',  line=dict(color='orange', width=2))
    trace_sma_100 = go.Scatter(x=data['Date'], y=data[f'{instrument_name}_SMA_100'],
                               mode='lines', name='100D SMA', line=dict(color='yellow', width=2))
    trace_sma_200 = go.Scatter(x=data['Date'], y=data[f'{instrument_name}_SMA_200'],
                               mode='lines', name='200D SMA', line=dict(color='cyan',   width=2))

    layout = go.Layout(
        title=f'{instrument_name} — Price, S/R Levels, SMAs ({ylabel})',
        xaxis=dict(title='Date', rangeslider=dict(visible=False), fixedrange=False),
        yaxis=dict(title=ylabel, fixedrange=False),
        height=400,
        dragmode='zoom',
    )

    return go.Figure(
        data=[trace_price] + level_traces + [trace_sma_50, trace_sma_100, trace_sma_200],
        layout=layout,
    )


# ── OI/Volume ─────────────────────────────────────────────────────────────────
# (The old per-contract "Individual" sub-tab — signal scorecards, roll-masked
# OI/price charts, oi_analysis.csv/oi_summary.csv — was removed 2026-09-28 per
# trader's explicit ask, leaving just the Cumulative view below as a single
# flat tab. oi_analyze.py/oi_summary.py still run in update.py in case this
# is wanted again, but nothing in this file reads their output any more.)


# ── OI/Volume → Cumulative sub-tab ──────────────────────────────────────────

def _oi_cumulative_load() -> dict:
    path = PROC_DIR / "oi_cumulative.csv"
    if not path.exists():
        print("WARNING: oi_cumulative.csv not found — Cumulative OI sub-tab will be empty.")
        return {}
    df = pd.read_csv(path, parse_dates=["date"])
    return {prod: g.sort_values("date").reset_index(drop=True)
            for prod, g in df.groupby("product")}


# Fallback (median%, p75%) used only if a product is missing from
# oi_arrow_thresholds.csv (e.g. before the pipeline has run once for it).
_OI_ARROW_FALLBACK = (1.0, 2.0)


def _oi_arrow_thresholds_load() -> dict:
    path = PROC_DIR / "oi_arrow_thresholds.csv"
    if not path.exists():
        print("WARNING: oi_arrow_thresholds.csv not found — using fixed 1%/2% arrow thresholds.")
        return {}
    df = pd.read_csv(path)
    return {r["product"]: (r["median_pct"], r["p75_pct"]) for _, r in df.iterrows()}


def _oi_arrow(pct, product: str) -> tuple:
    """Day-on-day price-move indicator, thresholds per-instrument: below that
    product's own trailing-3yr median abs % move = 1 arrow, median-to-75th
    percentile = 2, above the 75th percentile = 3 — per trader's explicit ask
    to judge "how big is this move" against each instrument's own volatility
    rather than one fixed % for every product. Up = green, down = red."""
    if pd.isna(pct) or pct == 0:
        return "–", "#9e9e9e"
    median, p75 = OI_ARROW_THRESHOLDS.get(product, _OI_ARROW_FALLBACK)
    a = abs(pct)
    n = 1 if a < median else (2 if a < p75 else 3)
    glyph = "▲" if pct > 0 else "▼"
    color = "#2ca02c" if pct > 0 else "#d62728"
    return glyph * n, color


# |OI/Vol %| above this is flagged as a likely data anomaly rather than a
# real trading fact — you cannot durably open more new contracts in a day
# than the day's total volume, so a ratio past 100% almost always means a
# vendor OI correction/rollover artifact (see the 2026-09-25 FCPO case in
# CLAUDE.md). Flagged cells are muted rather than shown at face value.
_OI_VOL_FLAG_THRESHOLD = 100.0

# |OI/Vol %| above this (but at/below the anomaly threshold above) is a
# genuinely large share of the day's volume going into fresh positioning —
# worth a trader's attention, not hidden like the anomaly tier. The whole
# row gets a light amber shade plus a bolder/coloured cell, per trader's
# explicit ask for something that "calls for the viewer's attention".
_OI_VOL_NOTABLE_THRESHOLD = 10.0


def _fmt_local_price(val) -> str:
    """Format a native-unit price: 2 decimals for small-magnitude quotes
    (USD/gallon, USD/barrel, cents/lb, ...) where rounding to a whole number
    would destroy the meaningful precision, whole number otherwise."""
    if pd.isna(val):
        return "n/a"
    return f"{val:,.2f}" if abs(val) < 200 else f"{val:,.0f}"


# Display names for the Cumulative tab headers — trader's explicit naming to
# avoid ambiguity between exchange product codes.
OI_CUMULATIVE_DISPLAY_NAMES = {
    "FCPO": "FCPO Palm Oil",
    "DCP":  "DCP Palm Olein",
    "BO":   "CBOT Soybean Oil",
    "SM":   "CBOT Soybean Meal",
    "S":    "CBOT Soybean",
    "LGO":  "ICE Gas Oil",
    "HO":   "NYMEX Heating Oil",
    "CL":   "NYMEX WTI",
    "LCO":  "ICE Brent",
    "DBY":  "Dalian Soybean Oil",
}

# Display order for the OI/Volume tab, per trader's explicit ask: FCPO Palm
# Oil, CBOT Soybean Oil, ICE Gas Oil lead (1-3), then Dalian Palm Olein/
# Dalian Soybean Oil (4-5), the rest follow.
OI_CUMULATIVE_LAYOUT_ORDER = [
    "FCPO", "BO", "LGO",
    "DCP", "DBY",
    "LCO", "HO", "CL",
    "SM", "S",
]


def _oi_cumulative_table(product: str) -> html.Div:
    display_name = OI_CUMULATIVE_DISPLAY_NAMES.get(product, product)
    df = OI_CUMULATIVE.get(product, pd.DataFrame())
    if df.empty:
        return html.Div([
            html.H3(display_name, style={**_SUBHEADER_STYLE, "marginTop": "24px"}),
            html.Div(
                "No cumulative OI data yet — run the daily update.",
                style={"padding": "16px", "color": "#aaa", "border": "1px dashed #ccc",
                       "borderRadius": "8px"},
            ),
        ])

    tail = df.tail(30).sort_values("date", ascending=False)
    unit = SPREAD_LOCAL_UNITS.get(product, {}).get("unit", "")

    th_s = {
        "padding": "5px 12px", "textAlign": "right", "fontWeight": "600",
        "fontSize": "11px", "color": "#555", "borderBottom": "1px solid #e0e0e0",
    }
    td_s = {"padding": "4px 12px", "textAlign": "right", "fontSize": "12px"}

    header = html.Thead(html.Tr([
        html.Th("Date",                style={**th_s, "textAlign": "left"}),
        html.Th("Total OI (lots)",     style=th_s),
        html.Th("Delta OI",            style=th_s),
        html.Th("Total Volume (lots)", style=th_s),
        html.Th("Delta Volume",        style=th_s),
        html.Th("OI/Vol %",            style=th_s),
        html.Th(f"Avg Px ({unit})" if unit else "Avg Px", style=th_s),
        html.Th("Px Chg",              style=th_s),
    ]))

    def _chg_style(val):
        color = ("#9e9e9e" if pd.isna(val) or val == 0
                 else ("#2ca02c" if val > 0 else "#d62728"))
        return "n/a" if pd.isna(val) else f"{val:+,.0f}", color

    rows = []
    for _, r in tail.iterrows():
        oi_chg_str, oi_chg_color = _chg_style(r["oi_chg"])
        vol_chg_str, vol_chg_color = _chg_style(r["volume_chg"])
        glyph, glyph_color = _oi_arrow(r["price_chg_pct"], product)
        vol_str = "n/a" if pd.isna(r["total_volume"]) else f"{r['total_volume']:,.0f}"
        px_str = _fmt_local_price(r["avg_price"])
        pct_title = f"{r['price_chg_pct']:+.2f}%" if pd.notna(r["price_chg_pct"]) else "n/a"

        ov_pct = r["oi_vol_pct"]
        row_bg = None
        if pd.isna(ov_pct):
            ov_str, ov_style, ov_title = "n/a", {**td_s, "color": "#9e9e9e"}, None
        elif abs(ov_pct) > _OI_VOL_FLAG_THRESHOLD:
            # Flagged as a likely data anomaly (see _OI_VOL_FLAG_THRESHOLD) —
            # muted almost to invisible rather than shown as a real number,
            # value kept in the tooltip for anyone who wants to check it.
            ov_str = "–"
            ov_style = {**td_s, "color": "#dcdcdc"}
            ov_title = (f"OI/Vol = {ov_pct:+.0f}% (flagged: |ratio|>"
                        f"{_OI_VOL_FLAG_THRESHOLD:.0f}%, likely a data anomaly — see CLAUDE.md)")
        elif abs(ov_pct) > _OI_VOL_NOTABLE_THRESHOLD:
            # A genuinely large share of the day's volume went into fresh
            # positioning — shade the whole row so it catches the eye.
            ov_str = f"{ov_pct:+.0f}%"
            ov_style = {**td_s, "color": "#b26a00", "fontWeight": "700"}
            ov_title = (f"OI/Vol = {ov_pct:+.0f}% — over "
                        f"{_OI_VOL_NOTABLE_THRESHOLD:.0f}% of the day's volume became new Open Interest")
            row_bg = "#fff6d9"
        else:
            ov_str = f"{ov_pct:+.0f}%"
            ov_style = {**td_s, "color": "#555"}
            ov_title = None

        row_style = {"backgroundColor": row_bg} if row_bg else {}
        rows.append(html.Tr([
            html.Td(r["date"].strftime("%Y-%m-%d"), style={**td_s, "textAlign": "left"}),
            html.Td(f"{r['total_oi']:,.0f}", style=td_s),
            html.Td(oi_chg_str, style={**td_s, "color": oi_chg_color, "fontWeight": "600"}),
            html.Td(vol_str, style=td_s),
            html.Td(vol_chg_str, style={**td_s, "color": vol_chg_color, "fontWeight": "600"}),
            html.Td(ov_str, title=ov_title, style=ov_style),
            html.Td(px_str, style=td_s),
            html.Td(glyph, title=pct_title,
                    style={**td_s, "color": glyph_color, "fontWeight": "700", "letterSpacing": "1px"}),
        ], style=row_style))

    # 90-trading-day rolling average of Total OI (lots), vs the latest day's OI.
    avg_oi = df.tail(90)["total_oi"].mean()
    latest_oi = df["total_oi"].iloc[-1]
    if pd.notna(avg_oi) and avg_oi > 0 and pd.notna(latest_oi):
        vs_avg = (latest_oi / avg_oi - 1) * 100
        vs_color = "#2ca02c" if vs_avg > 0 else ("#d62728" if vs_avg < 0 else "#9e9e9e")
        avg_line = html.Div([
            html.Span("90d avg OI: ", style={"color": "#777"}),
            html.Span(f"{avg_oi:,.0f} lots", style={"fontWeight": "700", "color": "#333"}),
            html.Span(f"  (latest {vs_avg:+.1f}% vs avg)",
                      style={"color": vs_color, "fontWeight": "600"}),
        ], style={"fontSize": "12px", "margin": "0 0 8px 0"})
    else:
        avg_line = html.Div("90d avg OI: n/a",
                            style={"fontSize": "12px", "color": "#9e9e9e", "margin": "0 0 8px 0"})

    return html.Div([
        html.H3(display_name, style={**_SUBHEADER_STYLE, "marginTop": "24px"}),
        avg_line,
        html.Table(
            [header, html.Tbody(rows)],
            style={"borderCollapse": "collapse", "fontSize": "12px", "width": "100%"},
        ),
    ], style={
        "backgroundColor": "white", "border": "1px solid #e0e0e0", "borderRadius": "8px",
        "overflow": "hidden", "boxShadow": "0 1px 4px rgba(0,0,0,0.08)",
        "padding": "12px 16px", "marginBottom": "16px",
        "flex": "1 1 680px", "maxWidth": "820px", "minWidth": "540px",
    })


# ── App layout ────────────────────────────────────────────────────────────────

_CONTROLS_STYLE = {
    "padding": "12px 24px",
    "borderBottom": "1px solid #e0e0e0",
    "backgroundColor": "#ffffff",
    "display": "flex",
    "alignItems": "flex-end",
    "gap": "16px",
}

_SUBHEADER_STYLE = {
    "margin": "0 0 10px 0",
    "color": "#555",
    "fontSize": "11px",
    "fontWeight": "700",
    "textTransform": "uppercase",
    "letterSpacing": "0.1em",
    "borderBottom": "1px solid #d5d5d5",
    "paddingBottom": "5px",
}

_GRID_STYLE = {
    "display": "grid",
    "gridTemplateColumns": "repeat(3, 1fr)",
    "gap": "16px",
}

# ── Left vertical navigation (sidebar) ────────────────────────────────────────
# The horizontal dcc.Tabs are retained (hidden) as the canonical state holders so
# every downstream callback keeps reading `.value` unchanged. The sidebar is a
# custom accordion that mirrors those values and writes back to them on click.
# `NAV_SUBTABS[section]` maps a top-level section to its sub-tab group:
#   tabs_id  — the hidden dcc.Tabs whose value this group drives
#   btype    — pattern-matching id "type" for the sub-tab buttons
#   items    — [(label, value), ...] in display order
#   nested   — optional {parent_value: {btype, tabs_id, items, nested?}} for a
#              deeper level; recurses (a nested entry may itself carry its own
#              "nested" dict — see Argus → Biofuel Premium → Fwd Diff → Graph/Tabular
#              for a 4th-level example) via _render_nav_level below.
NAV_SUBTABS = {
    "Calendar Spreads": {
        "tabs_id": "calspreads-sub-tabs", "btype": "nav-calspreads",
        "items": [("Historical", "cs-calendar"), ("Forward Curve", "cs-forward"),
                  ("YTD", "cs-ytd")],
    },
    "Basis/Hedge Ratios": {
        "tabs_id": "cashprem-sub-tabs", "btype": "nav-cashprem",
        "items": [("Prices", "cp-prices"), ("Hedge Ratios", "cp-ratios"),
                  ("Tradable Prem", "cp-tradable")],
        "nested": {
            "cp-ratios": {
                "tabs_id": "hedge-sub-tabs", "btype": "nav-hedge",
                "items": [("Tabular", "hr-tabular"), ("Graphs", "hr-graphs")],
            },
        },
    },
    "SND": {
        "tabs_id": "snd-sub-tabs", "btype": "nav-snd",
        "items": [("Graphs", "snd-graphs"), ("Exports", "snd-exports"),
                  ("Tabular", "snd-tabular")],
    },
    "Margins": {
        "tabs_id": "margins-sub-tabs", "btype": "nav-margins",
        "items": [("Overview", "mg-overview"), ("Variables", "mg-vars"),
                  ("Yield & Price Theory", "mg-theory"), ("Flows + overwrites", "mg-flows"),
                  ("Summary", "mg-summary"), ("Rules", "mg-rules")],
    },
    "Argus": {
        "tabs_id": "argus-sub-tabs", "btype": "nav-argus",
        "items": [("Calendar Spreads", "ar-calendar"), ("Product Spreads", "ar-product"),
                  ("Outright Prices", "ar-outright"), ("Biofuel Premium", "ar-premium"),
                  ("Bunker Market", "ar-bunker"), ("European Margin", "ar-margin"),
                  ("Byproduct", "ar-byproduct"), ("Freight", "ar-freight"),
                  ("Tickets", "ar-tickets"), ("Arb", "ar-arb"),
                  ("Features", "ar-features")],
        "nested": {
            "ar-calendar": {
                "tabs_id": "argus-cal-sub-tabs", "btype": "nav-argus-cal",
                "items": [("Historical", "ar-cal-hist"), ("Forward Curve", "ar-cal-forward"),
                          ("YTD", "ar-cal-ytd")],
            },
            "ar-product": {
                "tabs_id": "argus-prod-sub-tabs", "btype": "nav-argus-prod",
                "items": [("Seasonal", "ar-prod-seasonal"), ("Forward Curve", "ar-prod-forward")],
            },
            "ar-premium": {
                "tabs_id": "premium-sub-tabs", "btype": "nav-premium",
                "items": [("7-28 Day Diff", "prem-7to28"), ("Fwd Diff", "prem-fwd")],
                "nested": {
                    "prem-fwd": {
                        "tabs_id": "fwddiff-sub-tabs", "btype": "nav-fwddiff",
                        "items": [("Graph", "fd-graph"), ("Tabular", "fd-tabular")],
                    },
                },
            },
        },
    },
    "Parity": {
        "tabs_id": "parity-sub-tabs", "btype": "nav-parity",
        "items": [("Malindo", "pa-malindo"), ("Ind/China", "pa-indchina")],
    },
    "Product Spreads": {
        "tabs_id": "prodspreads-sub-tabs", "btype": "nav-prodspreads",
        "items": [("Seasonal", "ps-seasonal"), ("Forward Curve", "ps-forward")],
    },
    "Outright Prices": {
        "tabs_id": "outright-sub-tabs", "btype": "nav-outright",
        "items": [("Flat Price", "out-flat"), ("Forward Curve", "out-forward")],
    },
}

_SIDEBAR_WIDTH = "224px"

_SIDEBAR_STYLE = {
    "width": _SIDEBAR_WIDTH,
    "minWidth": _SIDEBAR_WIDTH,
    "backgroundColor": "#1a1a2e",
    "borderRight": "1px solid #dfe3ea",
    "minHeight": "calc(100vh - 65px)",
    "padding": "12px 0",
    "boxSizing": "border-box",
}

_SIDEBAR_STYLE_COLLAPSED = {**_SIDEBAR_STYLE, "display": "none"}

_MAIN_CONTENT_STYLE = {"flex": "1", "minWidth": "0"}

_NAV_TOGGLE_STYLE = {
    "background": "transparent",
    "border": "1px solid #cfd6e0",
    "borderRadius": "6px",
    "color": "#1a1a2e",
    "fontSize": "18px",
    "lineHeight": "1",
    "padding": "4px 12px",
    "cursor": "pointer",
}


def _nav_section_button(label: str, section: str, active: bool) -> html.Button:
    return html.Button(
        label,
        id={"type": "nav-section", "index": section},
        n_clicks=0,
        style={
            "display": "block", "width": "100%", "textAlign": "left",
            "padding": "10px 18px", "border": "none", "cursor": "pointer",
            "fontSize": "13px",
            "fontWeight": "700" if active else "500",
            "color": "#ffffff" if active else "#c7ccd8",
            "backgroundColor": "#33344f" if active else "transparent",
            "borderLeft": ("3px solid #6c8cff" if active else "3px solid transparent"),
        },
    )


def _nav_sub_button(label: str, value: str, btype: str, active: bool,
                    depth: int) -> html.Button:
    # depth 1 = sub-tab, depth 2+ = sub-sub-tab (deeper indent, smaller marker)
    indent = 18 + depth * 16
    marker = "•  " if depth == 1 else "◦  " if depth == 2 else "–  "
    return html.Button(
        marker + label,
        id={"type": btype, "index": value},
        n_clicks=0,
        style={
            "display": "block", "width": "100%", "textAlign": "left",
            "padding": "7px 18px", "paddingLeft": f"{indent}px",
            "border": "none", "cursor": "pointer",
            "fontSize": "12.5px",
            "fontWeight": "700" if active else "400",
            "color": "#ffffff" if active else "#9aa1b4",
            "backgroundColor": "#2a2b45" if active else "transparent",
            "borderLeft": ("3px solid #6c8cff" if active else "3px solid transparent"),
        },
    )


def _render_nav_level(cfg: dict, active_sub: dict, depth: int) -> list:
    """Render one sub-tab level's buttons, then recurse into whichever
    item's own "nested" config (if any) is currently active — supports
    arbitrary nesting depth (Argus → Biofuel Premium → Fwd Diff → Graph/
    Tabular is 2 nested levels deep, i.e. depth 2/3 here)."""
    children = []
    cur = active_sub.get(cfg["tabs_id"])
    for label, value in cfg["items"]:
        sub_active = (value == cur)
        children.append(_nav_sub_button(label, value, cfg["btype"], sub_active, depth=depth))
        nested = cfg.get("nested", {}).get(value)
        if nested and sub_active:
            children.extend(_render_nav_level(nested, active_sub, depth=depth + 1))
    return children


def build_left_nav(section, cs_sub, cp_sub, snd_sub, hedge_sub, margins_sub, argus_sub,
                   premium_sub, parity_sub, fwddiff_sub, ps_sub, argus_cal_sub, argus_prod_sub,
                   out_sub) -> list:
    """Render the accordion nav. Sub-items appear only under the active section
    (and each deeper level only under its currently active parent)."""
    # "Malindo Spreads" stays a SUBSECTIONS key (data/year logic keys off it)
    # but is no longer its own top-level nav entry — it now lives under
    # Parity → Malindo, so it's excluded here and "Parity" added instead.
    order = [s for s in SUBSECTIONS.keys() if s != "Malindo Spreads"] + [
        "Technical Analysis", "OI/Volume", "SND", "AI", "News", "Calendar", "CFTC", "Margins",
        "HVO/SAF Atlas", "Argus", "Parity"]
    active_sub = {
        "calspreads-sub-tabs": cs_sub,
        "cashprem-sub-tabs":   cp_sub,
        "snd-sub-tabs":        snd_sub,
        "hedge-sub-tabs":      hedge_sub,
        "margins-sub-tabs":    margins_sub,
        "argus-sub-tabs":      argus_sub,
        "premium-sub-tabs":    premium_sub,
        "fwddiff-sub-tabs":    fwddiff_sub,
        "parity-sub-tabs":     parity_sub,
        "prodspreads-sub-tabs": ps_sub,
        "argus-cal-sub-tabs":  argus_cal_sub,
        "argus-prod-sub-tabs": argus_prod_sub,
        "outright-sub-tabs":   out_sub,
    }
    children = []
    for sec in order:
        is_active = (sec == section)
        children.append(_nav_section_button(sec, sec, is_active))
        cfg = NAV_SUBTABS.get(sec)
        if not (is_active and cfg):
            continue
        children.extend(_render_nav_level(cfg, active_sub, depth=1))
    return children


def build_layout(all_years: list[int]) -> html.Div:
    all_years = [y for y in all_years if pd.notna(y)]   # guard against NaT-derived NaN years
    default_years = sorted(all_years)[-3:]
    return html.Div([
        # Header (full width) — sidebar toggle + title
        html.Div([
            html.Button("☰", id="nav-toggle", n_clicks=0,
                        title="Show/hide navigation", style=_NAV_TOGGLE_STYLE),
            html.H2(
                "Price Dashboard",
                style={"margin": "0", "color": "#1a1a2e", "fontWeight": "600"},
            ),
        ], style={
            "padding": "16px 24px",
            "borderBottom": "2px solid #e0e0e0",
            "backgroundColor": "#f8f9fa",
            "display": "flex",
            "alignItems": "center",
            "gap": "16px",
        }),

        dcc.Store(id="nav-collapsed", data=False),
        html.Div(id="nav-resize-sink", style={"display": "none"}),

        # Body: left vertical nav + main content column (flex row)
        html.Div([

        # ── Left sidebar (accordion nav, rendered by callback) ──
        html.Div([
            html.Div(id="left-nav"),
        ], id="left-sidebar", style=_SIDEBAR_STYLE),

        # ── Main content column ──
        html.Div([

        # Unit toggle bar (visible for all tabs except OI/Volume)
        html.Div([
            html.Label("Units", style={
                "fontWeight": "600", "fontSize": "13px", "marginRight": "12px",
            }),
            dcc.RadioItems(
                id="unit-toggle",
                options=[
                    {"label": " $/TN", "value": "usd_mt"},
                    {"label": " Local", "value": "local"},
                ],
                value="usd_mt",
                inline=True,
                inputStyle={"marginRight": "4px"},
                labelStyle={
                    "marginRight": "18px", "fontSize": "13px",
                    "cursor": "pointer",
                },
            ),
        ], id="unit-toggle-bar", style={
            "padding": "8px 24px",
            "borderBottom": "1px solid #e0e0e0",
            "backgroundColor": "#ffffff",
            "display": "flex",
            "alignItems": "center",
        }),

        # Controls bar (hidden when Technical Analysis tab is active)
        html.Div([
            html.Div([
                html.Label("Years", style={"fontWeight": "600", "marginBottom": "6px", "display": "block"}),
                dcc.Dropdown(
                    id="year-picker",
                    options=[{"label": str(y), "value": y} for y in reversed(all_years)],
                    value=default_years,
                    multi=True,
                    clearable=False,
                    style={"minWidth": "420px"},
                ),
            ]),
            html.Div([
                html.Label("Contract", style={"fontWeight": "600", "marginBottom": "6px", "display": "block"}),
                dcc.Dropdown(
                    id="ps-contract-picker",
                    options=[{"label": c, "value": c} for c in _PS_CONTRACTS],
                    value="c3",
                    multi=False,
                    clearable=False,
                    style={"minWidth": "120px"},
                ),
            ], id="ps-contract-wrapper", style={"display": "none"}),
        ], id="main-controls", style=_CONTROLS_STYLE),

        # Single-year picker for Calendar Spreads → YTD (one year at a time,
        # unlike the multi-select "Years" picker above which YTD ignores).
        html.Div([
            html.Div([
                html.Label("Year", style={"fontWeight": "600", "marginBottom": "6px", "display": "block"}),
                dcc.Dropdown(
                    id="ytd-year-picker",
                    options=[{"label": str(y), "value": y} for y in reversed(all_years)],
                    value=(sorted(all_years)[-1] if all_years else None),
                    multi=False,
                    clearable=False,
                    style={"minWidth": "160px"},
                ),
            ]),
        ], id="ytd-year-picker-wrapper", style={**_CONTROLS_STYLE, "display": "none"}),

        # Single-year picker for the "compare every instrument" chart shown
        # at the top of every Forward Curve sub-tab (Calendar Spreads,
        # Outright Prices, Product Spreads, Argus → Calendar/Product
        # Spreads) — separate from the multi-select "Years" picker above,
        # which drives the per-instrument small-multiples grid underneath.
        html.Div([
            html.Div([
                html.Label("Compare Year", style={"fontWeight": "600", "marginBottom": "6px", "display": "block"}),
                dcc.Dropdown(
                    id="fwd-compare-year-picker",
                    options=[{"label": str(y), "value": y} for y in reversed(all_years)],
                    value=(sorted(all_years)[-1] if all_years else None),
                    multi=False,
                    clearable=False,
                    style={"minWidth": "160px"},
                ),
            ]),
        ], id="fwd-compare-year-wrapper", style={**_CONTROLS_STYLE, "display": "none"}),

        # ── Hidden tab-state holders ──────────────────────────────────────────
        # These dcc.Tabs remain the canonical selection state (every downstream
        # callback reads their `.value`). They are hidden; the left sidebar nav
        # renders the visible navigation and writes back into these values.
        html.Div([

        # Section tabs
        dcc.Tabs(
            id="section-tabs",
            value=list(SUBSECTIONS.keys())[0],
            children=[
                # "Malindo Spreads" stays in SUBSECTIONS (data/year logic
                # keys off it) but is no longer its own top-level tab — it
                # now lives under Parity → Malindo.
                dcc.Tab(label=section, value=section)
                for section in SUBSECTIONS if section != "Malindo Spreads"
            ] + [
                dcc.Tab(label="Technical Analysis", value="Technical Analysis"),
                dcc.Tab(label="OI/Volume",          value="OI/Volume"),
                dcc.Tab(label="SND",                value="SND"),
                dcc.Tab(label="AI",                 value="AI"),
                dcc.Tab(label="News",               value="News"),
                dcc.Tab(label="Calendar",           value="Calendar"),
                dcc.Tab(label="CFTC",               value="CFTC"),
                dcc.Tab(label="Margins",            value="Margins"),
                dcc.Tab(label="HVO/SAF Atlas",      value="HVO/SAF Atlas"),
                dcc.Tab(label="Argus",              value="Argus"),
                dcc.Tab(label="Parity",             value="Parity"),
            ],
            style={"margin": "0 24px", "marginTop": "12px"},
        ),

        # Cash Premium/Basis sub-tabs (visible only when that section is active)
        html.Div([
            dcc.Tabs(id="cashprem-sub-tabs", value="cp-prices", children=[
                dcc.Tab(label="Prices",        value="cp-prices"),
                dcc.Tab(label="Hedge Ratios",  value="cp-ratios"),
                dcc.Tab(label="Tradable Prem", value="cp-tradable"),
            ]),
        ], id="cashprem-sub-tabs-wrapper",
           style={"display": "none", "padding": "8px 24px 0"}),

        # Argus sub-tabs (visible only when that section is active)
        html.Div([
            dcc.Tabs(id="argus-sub-tabs", value="ar-calendar", children=[
                dcc.Tab(label="Calendar Spreads", value="ar-calendar"),
                dcc.Tab(label="Product Spreads",  value="ar-product"),
                dcc.Tab(label="Outright Prices",  value="ar-outright"),
                dcc.Tab(label="Biofuel Premium",  value="ar-premium"),
                dcc.Tab(label="Bunker Market",     value="ar-bunker"),
                dcc.Tab(label="European Margin",   value="ar-margin"),
                dcc.Tab(label="Byproduct",         value="ar-byproduct"),
                dcc.Tab(label="Freight",           value="ar-freight"),
                dcc.Tab(label="Tickets",           value="ar-tickets"),
                dcc.Tab(label="Arb",               value="ar-arb"),
                dcc.Tab(label="Features",         value="ar-features"),
            ]),
        ], id="argus-sub-tabs-wrapper",
           style={"display": "none", "padding": "8px 24px 0"}),

        # Parity sub-tabs (visible only when that section is active)
        html.Div([
            dcc.Tabs(id="parity-sub-tabs", value="pa-malindo", children=[
                dcc.Tab(label="Malindo",   value="pa-malindo"),
                dcc.Tab(label="Ind/China", value="pa-indchina"),
            ]),
        ], id="parity-sub-tabs-wrapper",
           style={"display": "none", "padding": "8px 24px 0"}),

        # Biofuel Premium nested sub-tabs (3rd level, under Argus → Biofuel
        # Premium) — state-only, same as the other tabs in this block; the
        # left sidebar nav renders the visible switcher.
        html.Div([
            dcc.Tabs(id="premium-sub-tabs", value="prem-7to28", children=[
                dcc.Tab(label="7-28 Day Diff", value="prem-7to28"),
                dcc.Tab(label="Fwd Diff",      value="prem-fwd"),
            ]),
        ], id="premium-sub-tabs-wrapper", style={"display": "none"}),

        # Fwd Diff nested sub-tabs (4th level, under Argus → Biofuel Premium →
        # Fwd Diff) — Graph | Tabular, same as every other hidden state-only
        # tab holder here; the left sidebar nav renders the visible switcher.
        html.Div([
            dcc.Tabs(id="fwddiff-sub-tabs", value="fd-graph", children=[
                dcc.Tab(label="Graph",    value="fd-graph"),
                dcc.Tab(label="Tabular",  value="fd-tabular"),
            ]),
        ], id="fwddiff-sub-tabs-wrapper", style={"display": "none"}),

        # Argus Calendar Spreads nested sub-tabs (3rd level, under Argus →
        # Calendar Spreads) — Historical | Forward Curve | YTD, mirroring the
        # top-level Calendar Spreads tab. State-only, same as the other
        # nested tabs in this block; the left sidebar nav renders the
        # visible switcher.
        html.Div([
            dcc.Tabs(id="argus-cal-sub-tabs", value="ar-cal-hist", children=[
                dcc.Tab(label="Historical",    value="ar-cal-hist"),
                dcc.Tab(label="Forward Curve", value="ar-cal-forward"),
                dcc.Tab(label="YTD",           value="ar-cal-ytd"),
            ]),
        ], id="argus-cal-sub-tabs-wrapper", style={"display": "none"}),

        # Argus Product Spreads nested sub-tabs (3rd level, under Argus →
        # Product Spreads) — Seasonal | Forward Curve, mirroring the
        # top-level Product Spreads tab.
        html.Div([
            dcc.Tabs(id="argus-prod-sub-tabs", value="ar-prod-seasonal", children=[
                dcc.Tab(label="Seasonal",      value="ar-prod-seasonal"),
                dcc.Tab(label="Forward Curve", value="ar-prod-forward"),
            ]),
        ], id="argus-prod-sub-tabs-wrapper", style={"display": "none"}),

        # Calendar Spreads sub-tabs (visible only when that section is active)
        html.Div([
            dcc.Tabs(id="calspreads-sub-tabs", value="cs-calendar", children=[
                dcc.Tab(label="Historical",    value="cs-calendar"),
                dcc.Tab(label="Forward Curve", value="cs-forward"),
                dcc.Tab(label="YTD",           value="cs-ytd"),
            ]),
        ], id="calspreads-sub-tabs-wrapper",
           style={"display": "none", "padding": "8px 24px 0"}),

        # Product Spreads sub-tabs (visible only when that section is active)
        html.Div([
            dcc.Tabs(id="prodspreads-sub-tabs", value="ps-seasonal", children=[
                dcc.Tab(label="Seasonal",      value="ps-seasonal"),
                dcc.Tab(label="Forward Curve", value="ps-forward"),
            ]),
        ], id="prodspreads-sub-tabs-wrapper",
           style={"display": "none", "padding": "8px 24px 0"}),

        # Outright Prices sub-tabs — hidden state holder only (the visible
        # switcher is the left-sidebar accordion, like Argus's nested groups).
        html.Div([
            dcc.Tabs(id="outright-sub-tabs", value="out-flat", children=[
                dcc.Tab(label="Flat Price",    value="out-flat"),
                dcc.Tab(label="Forward Curve", value="out-forward"),
            ]),
        ], id="outright-sub-tabs-wrapper", style={"display": "none"}),

        # Margins sub-tabs (drive the embedded HTML's internal tab; kept hidden —
        # the visible switcher is the left-sidebar accordion, like other groups).
        html.Div([
            dcc.Tabs(id="margins-sub-tabs", value="mg-vars", children=[
                dcc.Tab(label="Overview",             value="mg-overview"),
                dcc.Tab(label="Variables",            value="mg-vars"),
                dcc.Tab(label="Yield & Price Theory", value="mg-theory"),
                dcc.Tab(label="Flows + overwrites",   value="mg-flows"),
                dcc.Tab(label="Summary",              value="mg-summary"),
                dcc.Tab(label="Rules",                value="mg-rules"),
            ]),
        ], id="margins-sub-tabs-wrapper", style={"display": "none"}),

        ], style={"display": "none"}),  # end hidden tab-state holders

        # Chart area
        html.Div(id="chart-grid", style={"padding": "16px 24px"}),

        # Hedge Ratios content (hidden by default — shown under Cash Premium/Basis)
        html.Div([
            # Nested sub-sub-tabs: Tabular | Graphs
            html.Div([
                dcc.Tabs(id="hedge-sub-tabs", value="hr-tabular", children=[
                    dcc.Tab(label="Tabular", value="hr-tabular"),
                    dcc.Tab(label="Graphs",  value="hr-graphs"),
                ]),
            ], style={"display": "none"}),  # hidden — driven by left sidebar nav

            # Tabular pane — snapshot table only
            html.Div([
                html.Div(id="hedge-table-wrapper",
                         style={"padding": "16px 24px 24px"}),
            ], id="hedge-tabular-pane", style={"display": "block"}),

            # Graphs pane — controls + rolling graphs
            html.Div([
                html.Div([
                    html.Div([
                        html.Label("Products", style={"fontWeight": "600", "marginBottom": "6px", "display": "block"}),
                        dcc.Dropdown(
                            id="hedge-products",
                            options=[{"label": p, "value": p} for p in hedging_data.HEDGE_PRODUCTS],
                            value=["CPO", "Olein"],
                            multi=True,
                            clearable=False,
                            style={"minWidth": "320px"},
                        ),
                    ]),
                    html.Div([
                        html.Label("Tenor", style={"fontWeight": "600", "marginBottom": "6px", "display": "block"}),
                        dcc.RadioItems(
                            id="hedge-tenor",
                            options=[{"label": " M1", "value": "M1"},
                                     {"label": " M2", "value": "M2"}],
                            value="M2", inline=True,
                            inputStyle={"marginRight": "4px"},
                            labelStyle={"marginRight": "14px", "fontSize": "13px", "cursor": "pointer"},
                        ),
                    ]),
                    html.Div([
                        html.Label("Window", style={"fontWeight": "600", "marginBottom": "6px", "display": "block"}),
                        dcc.RadioItems(
                            id="hedge-window",
                            options=[{"label": " 30d", "value": 30},
                                     {"label": " 60d", "value": 60}],
                            value=60, inline=True,
                            inputStyle={"marginRight": "4px"},
                            labelStyle={"marginRight": "14px", "fontSize": "13px", "cursor": "pointer"},
                        ),
                    ]),
                    html.Div([
                        html.Label("Lookback", style={"fontWeight": "600", "marginBottom": "6px", "display": "block"}),
                        dcc.RadioItems(
                            id="hedge-lookback",
                            options=[{"label": " 3M",  "value": 63},
                                     {"label": " 6M",  "value": 126},
                                     {"label": " 1Y",  "value": 252},
                                     {"label": " All", "value": 0}],
                            value=252, inline=True,
                            inputStyle={"marginRight": "4px"},
                            labelStyle={"marginRight": "14px", "fontSize": "13px", "cursor": "pointer"},
                        ),
                    ]),
                ], style=_CONTROLS_STYLE),

                html.Div(id="hedge-graphs-wrapper",
                         style={"padding": "8px 24px 16px"}),
            ], id="hedge-graphs-pane", style={"display": "none"}),
        ], id="hedge-ratios-content", style={"display": "none"}),

        # Tradable Premium content (hidden by default — shown under Basis/Hedge Ratios)
        html.Div([
            html.Div([
                html.H3("Malaysia", style={**_SUBHEADER_STYLE, "marginTop": "8px"}),
                html.Div(id="tp-malaysia-grid"),
                html.H3("Indonesia", style={**_SUBHEADER_STYLE, "marginTop": "28px"}),
                html.Div(id="tp-indonesia-grid"),
            ], style={"padding": "8px 24px 16px"}),
        ], id="tradable-prem-content", style={"display": "none"}),

        # Margins content — embed the standalone plant-margin dashboard in an iframe.
        # A single shared iframe; the Dash sub-tabs drive its internal tab via a
        # clientside postMessage (see _MARGIN_SHIM). Placeholder if no source file.
        html.Div(
            [
                html.Iframe(
                    id="margins-iframe",
                    src=f"/margins-embed?v={_MARGIN_TOKEN or '0'}",
                    style={"width": "100%", "height": "calc(100vh - 130px)",
                           "border": "none"},
                )
            ] if _MARGIN_TOKEN else [
                html.Div(
                    "No plant-margin dashboard file found in Biofuels/Margin. "
                    "Save one (plant_margin_dashboard_YYYYMMDD.html) there and restart.",
                    style={"padding": "24px", "color": "#8a94a6"},
                ),
                # keep the iframe id present so the clientside callback never errors
                html.Iframe(id="margins-iframe", style={"display": "none"}),
            ],
            id="margins-content", style={"display": "none"},
        ),

        # HVO/SAF Atlas content — embed the static plant-atlas HTML in an iframe.
        # No sub-tabs and no postMessage bridge: the atlas is a single self-contained
        # page. Placeholder if the source file is missing.
        # ⚠️ src is deliberately EMPTY here and is filled in by _toggle_hvo when the
        # tab is first opened. The atlas builds its datamaps projection from the
        # container's clientWidth at load time; loading it inside a display:none pane
        # measures zero width and yields a badly distorted map.
        html.Div(
            [
                html.Iframe(
                    id="hvo-iframe",
                    src="",
                    style={"width": "100%", "height": "calc(100vh - 130px)",
                           "border": "none"},
                )
            ] if _HVO_TOKEN else [
                html.Div(
                    "No plant-atlas file found at "
                    "Biofuels/Claude/hvo_saf_global_plant_atlas.html. "
                    "Save one there and restart.",
                    style={"padding": "24px", "color": "#8a94a6"},
                ),
                # keep the iframe id present so the callbacks never error
                html.Iframe(id="hvo-iframe", style={"display": "none"}),
            ],
            id="hvo-content", style={"display": "none"},
        ),

        # Technical Analysis content (hidden by default)
        html.Div([
            html.Div([
                html.Label("From Year", style={"fontWeight": "600", "marginBottom": "6px", "display": "block"}),
                dcc.Dropdown(
                    id="ta-year-picker",
                    options=[{"label": str(y), "value": y} for y in _TA_YEAR_OPTIONS],
                    value=_TA_DEFAULT_YEAR,
                    multi=False,
                    clearable=False,
                    style={"minWidth": "180px"},
                ),
            ], style={
                "padding": "12px 24px",
                "borderBottom": "1px solid #e0e0e0",
                "backgroundColor": "#ffffff",
                "display": "flex",
                "alignItems": "flex-end",
                "gap": "16px",
            }),
            html.Div([
                dcc.Graph(
                    id=f"ta-{ric}",
                    config={"displayModeBar": False, "scrollZoom": True, "responsive": True},
                    style={"borderRadius": "8px"},
                )
                for ric in TA_INSTRUMENTS
            ], style={
                "display": "grid",
                "gridTemplateColumns": "repeat(3, 1fr)",
                "gap": "16px",
                "padding": "16px 24px",
            }),
            # Product Spreads (c3) subsection
            html.H4("Product Spreads (c3)", style={
                **_SUBHEADER_STYLE, "marginTop": "28px", "marginLeft": "24px", "marginRight": "24px",
            }),
            html.Div([
                dcc.Graph(
                    id=f"ta-spread-{key}",
                    config={"displayModeBar": False, "scrollZoom": True, "responsive": True},
                    style={"borderRadius": "8px"},
                )
                for key in TA_SPREAD_INSTRUMENTS
            ], style={
                "display": "grid",
                "gridTemplateColumns": "repeat(3, 1fr)",
                "gap": "16px",
                "padding": "0 24px 16px 24px",
            }),
        ], id="ta-content", style={"display": "none"}),

        # OI/Volume content (hidden by default) — single flat tab, no sub-tabs
        # (the old Individual/Cumulative split was removed 2026-09-28 per
        # trader's explicit ask; this is what Cumulative used to be)
        html.Div([
            html.Div([
                _oi_cumulative_table(product)
                for product in OI_CUMULATIVE_LAYOUT_ORDER
                if product in config.OI_CUMULATIVE_RANGE
            ], style={"padding": "8px 24px", "display": "flex",
                      "flexWrap": "wrap", "gap": "16px", "alignItems": "flex-start"}),
        ], id="oi-content", style={"display": "none"}),

        # SND content (hidden by default)
        html.Div([
            dcc.Tabs(id="snd-sub-tabs", value="snd-graphs", children=[
                dcc.Tab(label="Graphs",  value="snd-graphs"),
                dcc.Tab(label="Exports", value="snd-exports"),
                dcc.Tab(label="Tabular", value="snd-tabular"),
            ], style={"display": "none"}),  # hidden — driven by left sidebar nav
            html.Div(id="snd-sub-content"),
        ], id="snd-content", style={"display": "none", "padding": "16px 24px"}),

        # AI Analysis content (hidden by default)
        html.Div([
            # Controls row
            html.Div([
                html.Div([
                    html.Label("Instrument", style={"fontWeight": "600", "marginBottom": "6px", "display": "block"}),
                    dcc.Dropdown(
                        id="ai-instrument",
                        options=[],  # populated by callback on tab open
                        placeholder="Select instrument...",
                        style={"minWidth": "360px"},
                    ),
                ]),
                html.Div([
                    html.Label("Timeframe", style={"fontWeight": "600", "marginBottom": "6px", "display": "block"}),
                    dcc.Dropdown(
                        id="ai-timeframe",
                        options=[
                            {"label": "1 Month",  "value": "1M"},
                            {"label": "3 Months", "value": "3M"},
                            {"label": "6 Months", "value": "6M"},
                            {"label": "1 Year",   "value": "1Y"},
                            {"label": "3 Years",  "value": "3Y"},
                            {"label": "5 Years",  "value": "5Y"},
                            {"label": "All Available Data", "value": "All"},
                        ],
                        value="1Y",
                        clearable=False,
                        style={"minWidth": "180px"},
                    ),
                ]),
                html.Div([
                    html.Label("\u00a0", style={"marginBottom": "6px", "display": "block"}),
                    html.Button(
                        "Run Analysis",
                        id="ai-run-btn",
                        n_clicks=0,
                        style={
                            "padding": "8px 24px",
                            "backgroundColor": "#1a1a2e",
                            "color": "white",
                            "border": "none",
                            "borderRadius": "6px",
                            "fontWeight": "600",
                            "fontSize": "13px",
                            "cursor": "pointer",
                        },
                    ),
                ]),
            ], style={**_CONTROLS_STYLE, "borderBottom": "1px solid #e0e0e0"}),

            # Status bar
            html.Div(
                "Ready",
                id="ai-status",
                style={
                    "padding": "8px 24px",
                    "fontSize": "12px",
                    "color": "#888",
                    "backgroundColor": "#ffffff",
                    "borderBottom": "1px solid #e0e0e0",
                },
            ),

            # Narrative output panel
            html.Div(
                id="ai-narrative",
                style={
                    "padding": "24px",
                    "maxHeight": "60vh",
                    "overflowY": "auto",
                    "backgroundColor": "white",
                    "margin": "16px 24px",
                    "borderRadius": "8px",
                    "border": "1px solid #e0e0e0",
                    "boxShadow": "0 1px 4px rgba(0,0,0,0.08)",
                    "whiteSpace": "pre-wrap",
                    "fontSize": "13px",
                    "lineHeight": "1.6",
                },
            ),

            # Raw stats expander
            html.Details([
                html.Summary("Raw Statistics", style={
                    "cursor": "pointer", "fontWeight": "600", "fontSize": "12px",
                    "color": "#555", "padding": "8px 0",
                }),
                html.Pre(
                    id="ai-raw-stats",
                    style={
                        "padding": "16px",
                        "backgroundColor": "#f8f9fa",
                        "borderRadius": "6px",
                        "fontSize": "11px",
                        "lineHeight": "1.5",
                        "maxHeight": "40vh",
                        "overflowY": "auto",
                        "border": "1px solid #e0e0e0",
                    },
                ),
            ], style={"padding": "0 24px 24px 24px"}),

        ], id="ai-content", style={"display": "none"}),

        # News content (hidden by default)
        html.Div(id="news-content", style={"display": "none", "padding": "16px 24px"}),

        # Calendar content (hidden by default)
        html.Div(id="calendar-content", style={"display": "none", "padding": "16px 24px"}),

        # CFTC content (hidden by default)
        html.Div(id="cftc-content", style={"display": "none", "padding": "16px 24px"}),

        ], id="main-content", style=_MAIN_CONTENT_STYLE),  # end main content column

        ], id="dashboard-body",
           style={"display": "flex", "alignItems": "flex-start"}),  # end body flex row

    ], style={"fontFamily": "Inter, Segoe UI, Arial, sans-serif", "backgroundColor": "#f4f6f9", "minHeight": "100vh"})


# ── App init & callbacks ──────────────────────────────────────────────────────

SPREADS                          = load_all_spreads()


def _build_product_spread_curves(spreads: dict) -> dict:
    """Combine the per-contract Product Spread CSVs (e.g. BOPO_c1, BOPO_c2,
    BOPO_c3, BOPO_c5, BOPO_c7) into one date-indexed DataFrame per label
    (columns = c1/c2/c3/c5/c7) for the Product Spreads → Forward Curve
    sub-tab, mirroring FORWARD_CURVES for outright prices."""
    result = {}
    for label in PRODUCT_SPREAD_LABELS:
        cols = {}
        for contract in _PS_CONTRACTS:
            df = spreads.get(f"{label}_{contract}")
            if df is None or df.empty:
                continue
            cols[contract] = df.set_index("date")["spread_value"]
        if not cols:
            continue
        result[label] = pd.DataFrame(cols).sort_index()
    return result


PRODUCT_SPREAD_CURVES = _build_product_spread_curves(SPREADS)


def _inject_cash_m1m2_spreads() -> None:
    """Compute M1-M2 calendar spreads for each palm cash product and inject
    into SPREADS + TITLES. Uses the same USD/MT frame as the Hedge Ratios tab."""
    pairs = [
        ("CPO-MYSTH",  "CPO-MYSTH-M1",  "CPO-MYSTH-M2"),
        ("POL-MYRBD",  "POL-MYRBD-M1",  "POL-MYRBD-M2"),
        ("PO-MYRBD",   "PO-MYRBD-M1",   "PO-MYRBD-M2"),
        ("PSTR-MYRBD", "PSTR-MYRBD-M1", "PSTR-MYRBD-M2"),
        ("PFAD-MY",    "PFAD-MY-M1",    "PFAD-MY-M2"),
        ("CPO-ID",     "CPO-ID-M1",     "CPO-ID-M2"),
        ("POL-IDRBD",  "POL-IDRBD-M1",  "POL-IDRBD-M2"),
        ("PO-IDRBD",   "PO-IDRBD-M1",   "PO-IDRBD-M2"),
        ("PSTR-IDRBD", "PSTR-IDRBD-M1", "PSTR-IDRBD-M2"),
    ]
    try:
        wide = hedging_data.get_prices_wide()
    except Exception as e:
        print(f"  [cash M1-M2] skipped (no prices frame): {e}")
        return
    added = 0
    for stem, m1, m2 in pairs:
        if m1 not in wide.columns or m2 not in wide.columns:
            continue
        series = (wide[m1] - wide[m2]).dropna()
        if series.empty:
            continue
        df = pd.DataFrame({"date": series.index, "spread_value": series.values})
        df["date"]        = pd.to_datetime(df["date"])
        df["year"]        = df["date"].dt.year
        df["day_of_year"] = df["date"].dt.dayofyear
        df["month_day"]   = df["date"].dt.strftime("%m-%d")
        key = f"{stem}_M1M2"
        SPREADS[key] = df
        # Both legs share the same underlying RIC — collapse to "<base> M1 - M2"
        base = _ric_display(m2)
        if base.endswith(" M2"):
            base = base[:-3]
        TITLES[key] = f"{base} M1 - M2"
        added += 1
    print(f"  Cash M1-M2 calendar spreads injected: {added}")


_inject_cash_m1m2_spreads()

OUTRIGHTS                        = load_outright_prices()
# Synthetic FOB Malaysia CPO outright (USD/MT) comes from process.py's CSV, not raw RICs
if "CPO-MYFOB-M2" in SPREADS:
    OUTRIGHTS["CPO-MYFOB-M2"] = SPREADS["CPO-MYFOB-M2"].copy()
TA_WIDE                          = _ta_load_wide()
TA_SPREAD_WIDE                   = _ta_load_spread_wide()
FX_RATES                         = _load_fx_rates()
OI_CUMULATIVE = _oi_cumulative_load()
OI_ARROW_THRESHOLDS = _oi_arrow_thresholds_load()
SPREADS_LOCAL                    = _build_spreads_local(SPREADS, FX_RATES)
OUTRIGHTS_USDMT                  = _build_outrights_usdmt(OUTRIGHTS, FX_RATES)
TA_WIDE_USDMT                    = _build_ta_wide_usdmt(TA_WIDE, FX_RATES)
FORWARD_CURVES, FORWARD_CURVES_LOCAL = load_forward_curves(FX_RATES)

# India Local prices from Google Sheet (full time series)
_india_result = fetch_india_data()
if _india_result:
    INDIA_USD_OUT, INDIA_INR_OUT, INDIA_USD_SPR, INDIA_INR_SPR = _india_result
    print(f"India Local: {len(INDIA_USD_OUT)} outrights, {len(INDIA_USD_SPR)} spreads loaded.")
else:
    INDIA_USD_OUT, INDIA_INR_OUT, INDIA_USD_SPR, INDIA_INR_SPR = {}, {}, {}, {}
    print("WARNING: India Local prices unavailable — charts will be empty.")

# Register India titles into the global TITLES dict
TITLES.update(INDIA_TITLES)
# Typo fix: 'Grunt' -> 'Groundnut' (applied both to TITLES and INDIA_TITLES)
for _k, _v in list(INDIA_TITLES.items()):
    if "Grunt" in _v:
        INDIA_TITLES[_k] = _v.replace("Grunt", "Groundnut")
        TITLES[_k] = INDIA_TITLES[_k]
# Tradable Premium (Tradable − raw regional Reuters) from the Tradeable Prices Pull sheet
TRADABLE_PREM, _TP_YEARS = load_tradable_prem()
if TRADABLE_PREM:
    print(f"Tradable Prem: {len(TRADABLE_PREM)} series loaded.")
else:
    print("WARNING: Tradable Prem unavailable — tab will show 'awaiting credential'.")

# Parity tab → Ind/China sub-tab: forward-curve snapshot tables (China + India
# public Google Sheets, no service-account key needed — see parity_data.py).
CHINA_PARITY = fetch_china_parity()
if CHINA_PARITY:
    print(f"China Parity: {len(CHINA_PARITY.get('daily', []))} daily / "
          f"{len(CHINA_PARITY.get('weekly', []))} weekly rows loaded.")
else:
    CHINA_PARITY = {}
    print("WARNING: China Parity sheet unavailable — Ind/China tab will show 'no data'.")

INDIA_PARITY = fetch_india_parity()
if INDIA_PARITY:
    _loaded = sum(1 for v in INDIA_PARITY.values() if v.get("daily") or v.get("weekly"))
    print(f"India Parity: {_loaded}/{len(INDIA_PARITY_KEYS)} instruments loaded.")
else:
    INDIA_PARITY = {}
    print("WARNING: India Parity sheet unavailable — Ind/China tab will show 'no data'.")

# Argus biofuels reference prices (UCOME/FAME/HVO/HEFA-SPK/UCO) from the Argus
# "Prices" Google Sheet, plus FCPO merged in from LSEG — see scripts/argus_data.py
ARGUS_DATA, ARGUS_TITLES, ARGUS_YLABELS, _ARGUS_YEARS = load_argus()
if ARGUS_DATA:
    print(f"Argus: {len(ARGUS_DATA)} series loaded.")
else:
    print("WARNING: Argus data unavailable — tab will show 'awaiting credential'.")
TITLES.update(ARGUS_TITLES)

# Argus → Arb → quarterly MIP table (not a time series — see _argus_arb_tile()).
# 8 quarters, 2025 Q1 through 2026 Q4, per trader's explicit ask.
_ARGUS_MIP_QUARTERS_REQUESTED = [(2025, q) for q in (1, 2, 3, 4)] + [(2026, q) for q in (1, 2, 3, 4)]
ARGUS_MIP_QUARTERS = _argus_mod.compute_mip_table(_ARGUS_MIP_QUARTERS_REQUESTED)
_cur_year, _cur_q = _argus_mod.current_quarter()
# Reuse the row already computed above (no extra LSEG reload) when the
# current quarter is inside the requested range; only falls back to a fresh
# (reload-the-legs) call if it ever isn't (e.g. this code still running in
# a future year beyond _ARGUS_MIP_QUARTERS_REQUESTED).
ARGUS_MIP_CURRENT = next(
    (r for r in ARGUS_MIP_QUARTERS if r["year"] == _cur_year and r["quarter"] == _cur_q), None
) or _argus_mod.compute_mip_quarter(_cur_year, _cur_q)
if ARGUS_MIP_QUARTERS:
    print(f"Argus MIP: {len(ARGUS_MIP_QUARTERS)}/{len(_ARGUS_MIP_QUARTERS_REQUESTED)} quarters priced "
          f"(current: {ARGUS_MIP_CURRENT['label'] if ARGUS_MIP_CURRENT else 'n/a'}).")
else:
    print("WARNING: Argus MIP quarters unavailable — Arb tab will show 'no data'.")

# Commodity/energy event calendar (Sonnet + web-search daily pull → cached CSV)
CALENDAR_DF = load_calendar()
if not CALENDAR_DF.empty:
    print(f"Calendar: {len(CALENDAR_DF)} events loaded.")
else:
    print("WARNING: Calendar cache empty — tab will show 'awaiting first pull'.")

# CFTC Commitments of Traders — spec positioning (user-saved report pages
# under Biofuels/CFTC/, re-parsed fresh at each dashboard start, see
# scripts/cftc_data.py)
CFTC_DF = load_cftc()
if not CFTC_DF.empty:
    print(f"CFTC: {CFTC_DF['cftc_code'].nunique()} commodities, "
          f"{CFTC_DF['report_date'].nunique()} report date(s) loaded.")
else:
    print("WARNING: No CFTC report files found — CFTC tab will show 'no data'.")

# Register seasonal-chart titles for each Tradable Prem series.
for _region in ("MY", "ID"):
    for _prod in config.TRADABLE_PREM_PRODUCTS:
        TITLES[f"TP_{_region}_{_prod}"] = (
            f"{_prod}  ·  Tradable − {_region} Reuters"
        )

ALL_YEARS = sorted(
    {yr for df in SPREADS.values()       for yr in df["year"].unique()} |
    {yr for df in OUTRIGHTS.values()     for yr in df["year"].unique()} |
    {yr for df in INDIA_USD_OUT.values() for yr in df["year"].unique()} |
    set(_TP_YEARS) |
    set(_ARGUS_YEARS)
)

app = Dash(__name__, title="Price Dashboard")

# Cross-chart hover crosshair: every seasonal-overlay chart in the active tab shares
# the same x-axis (day-of-year), so hovering a date on one chart draws a matching
# vertical guide line (no popup) on every other chart in #chart-grid. Injected here
# rather than via assets/*.js because Dash's assets_folder resolution is broken for
# this script-run app (see the "assets caveat" in CLAUDE.md §7).
app.index_string = app.index_string.replace(
    "</body>",
    """
    <script>
    (function () {
        function getPlots() {
            var container = document.getElementById('chart-grid');
            if (!container) return [];
            return Array.prototype.slice.call(container.querySelectorAll('.js-plotly-plot'));
        }

        function clearCrosshair(gd) {
            if (gd._crosshairOn) {
                try { Plotly.relayout(gd, { shapes: [] }); } catch (e) {}
                gd._crosshairOn = false;
            }
        }

        function drawCrosshair(gd, xval) {
            gd._crosshairOn = true;
            try {
                Plotly.relayout(gd, {
                    shapes: [{
                        type: 'line', xref: 'x', yref: 'paper',
                        x0: xval, x1: xval, y0: 0, y1: 1,
                        line: { color: 'rgba(0,0,0,0.35)', width: 1, dash: 'dot' },
                    }],
                });
            } catch (e) {}
        }

        var pending = null;
        function attach(gd) {
            if (gd._hoverSyncAttached) return;
            gd._hoverSyncAttached = true;

            gd.on('plotly_hover', function (evt) {
                if (!evt.points || !evt.points.length) return;
                var xval = evt.points[0].x;
                if (pending) cancelAnimationFrame(pending);
                pending = requestAnimationFrame(function () {
                    getPlots().forEach(function (other) {
                        if (other === gd) return;
                        drawCrosshair(other, xval);
                    });
                });
            });

            gd.on('plotly_unhover', function () {
                getPlots().forEach(function (other) {
                    if (other === gd) return;
                    clearCrosshair(other);
                });
            });
        }

        var scanScheduled = false;
        function scheduleScan() {
            if (scanScheduled) return;
            scanScheduled = true;
            requestAnimationFrame(function () {
                scanScheduled = false;
                getPlots().forEach(attach);
            });
        }

        var observer = new MutationObserver(scheduleScan);
        observer.observe(document.body, { childList: true, subtree: true });
        document.addEventListener('DOMContentLoaded', scheduleScan);
        scheduleScan();
    }());
    </script>
    </body>""",
)


# Serve the embedded plant-margin dashboard via an explicit Flask route rather than
# Dash's assets/ mechanism (whose folder resolves to scripts/assets under a script
# run and is unreliable here). Served by absolute path, so cwd/__name__ don't matter.
@app.server.route("/margins-embed")
def _serve_margins_embed():
    if not os.path.exists(_MARGIN_ASSET):
        abort(404)
    resp = send_file(str(_MARGIN_ASSET), mimetype="text/html")
    # Revalidate each load (ETag) so a regenerated asset — new shim or a freshly
    # Saved dashboard — is picked up without a stale browser cache.
    resp.headers["Cache-Control"] = "no-cache"
    return resp


# Same treatment for the HVO/SAF plant atlas (see the assets caveat above).
@app.server.route("/hvo-embed")
def _serve_hvo_embed():
    if not os.path.exists(_HVO_ASSET):
        abort(404)
    resp = send_file(str(_HVO_ASSET), mimetype="text/html")
    resp.headers["Cache-Control"] = "no-cache"
    return resp


app.layout = build_layout(ALL_YEARS)


# ── Left sidebar navigation ───────────────────────────────────────────────────
# The sidebar is a rendered-by-callback accordion that mirrors the (hidden)
# dcc.Tabs values and writes back to them on click, so all existing content
# callbacks keep working off the same `.value` state.

@app.callback(
    Output("left-nav", "children"),
    Input("section-tabs",      "value"),
    Input("calspreads-sub-tabs", "value"),
    Input("cashprem-sub-tabs", "value"),
    Input("snd-sub-tabs",      "value"),
    Input("hedge-sub-tabs",    "value"),
    Input("margins-sub-tabs",  "value"),
    Input("argus-sub-tabs",    "value"),
    Input("premium-sub-tabs",  "value"),
    Input("fwddiff-sub-tabs",  "value"),
    Input("parity-sub-tabs",   "value"),
    Input("prodspreads-sub-tabs", "value"),
    Input("argus-cal-sub-tabs",  "value"),
    Input("argus-prod-sub-tabs", "value"),
    Input("outright-sub-tabs",   "value"),
)
def _render_left_nav(section, cs_sub, cp_sub, snd_sub, hedge_sub, margins_sub, argus_sub,
                     premium_sub, fwddiff_sub, parity_sub, ps_sub, argus_cal_sub, argus_prod_sub,
                     out_sub):
    return build_left_nav(section, cs_sub, cp_sub, snd_sub, hedge_sub, margins_sub, argus_sub,
                          premium_sub, parity_sub, fwddiff_sub, ps_sub, argus_cal_sub, argus_prod_sub,
                          out_sub)


def _clicked_index():
    """Return the pattern-matching `index` of a genuinely-clicked nav button, or
    raise PreventUpdate when the callback fired only because the accordion
    re-rendered (new buttons arrive with n_clicks == 0)."""
    trig = ctx.triggered[0] if ctx.triggered else None
    if not trig or not trig.get("value"):
        raise PreventUpdate
    return ctx.triggered_id["index"]


@app.callback(
    Output("section-tabs", "value"),
    Input({"type": "nav-section", "index": ALL}, "n_clicks"),
    prevent_initial_call=True,
)
def _nav_click_section(_clicks):
    return _clicked_index()


@app.callback(
    Output("calspreads-sub-tabs", "value"),
    Input({"type": "nav-calspreads", "index": ALL}, "n_clicks"),
    prevent_initial_call=True,
)
def _nav_click_calspreads(_clicks):
    return _clicked_index()


@app.callback(
    Output("prodspreads-sub-tabs", "value"),
    Input({"type": "nav-prodspreads", "index": ALL}, "n_clicks"),
    prevent_initial_call=True,
)
def _nav_click_prodspreads(_clicks):
    return _clicked_index()


@app.callback(
    Output("outright-sub-tabs", "value"),
    Input({"type": "nav-outright", "index": ALL}, "n_clicks"),
    prevent_initial_call=True,
)
def _nav_click_outright(_clicks):
    return _clicked_index()


@app.callback(
    Output("argus-cal-sub-tabs", "value"),
    Input({"type": "nav-argus-cal", "index": ALL}, "n_clicks"),
    prevent_initial_call=True,
)
def _nav_click_argus_cal(_clicks):
    return _clicked_index()


@app.callback(
    Output("argus-prod-sub-tabs", "value"),
    Input({"type": "nav-argus-prod", "index": ALL}, "n_clicks"),
    prevent_initial_call=True,
)
def _nav_click_argus_prod(_clicks):
    return _clicked_index()


@app.callback(
    Output("cashprem-sub-tabs", "value"),
    Input({"type": "nav-cashprem", "index": ALL}, "n_clicks"),
    prevent_initial_call=True,
)
def _nav_click_cashprem(_clicks):
    return _clicked_index()


@app.callback(
    Output("snd-sub-tabs", "value"),
    Input({"type": "nav-snd", "index": ALL}, "n_clicks"),
    prevent_initial_call=True,
)
def _nav_click_snd(_clicks):
    return _clicked_index()


@app.callback(
    Output("hedge-sub-tabs", "value"),
    Input({"type": "nav-hedge", "index": ALL}, "n_clicks"),
    prevent_initial_call=True,
)
def _nav_click_hedge(_clicks):
    return _clicked_index()


@app.callback(
    Output("margins-sub-tabs", "value"),
    Input({"type": "nav-margins", "index": ALL}, "n_clicks"),
    prevent_initial_call=True,
)
def _nav_click_margins(_clicks):
    return _clicked_index()


@app.callback(
    Output("argus-sub-tabs", "value"),
    Input({"type": "nav-argus", "index": ALL}, "n_clicks"),
    prevent_initial_call=True,
)
def _nav_click_argus(_clicks):
    return _clicked_index()


@app.callback(
    Output("premium-sub-tabs", "value"),
    Input({"type": "nav-premium", "index": ALL}, "n_clicks"),
    prevent_initial_call=True,
)
def _nav_click_premium(_clicks):
    return _clicked_index()


@app.callback(
    Output("fwddiff-sub-tabs", "value"),
    Input({"type": "nav-fwddiff", "index": ALL}, "n_clicks"),
    prevent_initial_call=True,
)
def _nav_click_fwddiff(_clicks):
    return _clicked_index()


@app.callback(
    Output("parity-sub-tabs", "value"),
    Input({"type": "nav-parity", "index": ALL}, "n_clicks"),
    prevent_initial_call=True,
)
def _nav_click_parity(_clicks):
    return _clicked_index()


@app.callback(
    Output("nav-collapsed", "data"),
    Input("nav-toggle", "n_clicks"),
    State("nav-collapsed", "data"),
    prevent_initial_call=True,
)
def _toggle_nav_collapsed(_n, collapsed):
    return not collapsed


@app.callback(
    Output("left-sidebar", "style"),
    Input("nav-collapsed", "data"),
)
def _apply_nav_collapsed(collapsed):
    return _SIDEBAR_STYLE_COLLAPSED if collapsed else _SIDEBAR_STYLE


# Nudge Plotly to redraw at the new width whenever the sidebar shows/hides.
app.clientside_callback(
    "function(_s){ setTimeout(function(){ "
    "window.dispatchEvent(new Event('resize')); }, 80); return ''; }",
    Output("nav-resize-sink", "children"),
    Input("left-sidebar", "style"),
    prevent_initial_call=True,
)



def _fwd_compare_block(fig: go.Figure) -> html.Div:
    """Full-width wrapper for the "compare every instrument" chart that sits
    above the per-instrument grid on every Forward Curve sub-tab."""
    return html.Div(
        dcc.Graph(figure=fig, config={"displayModeBar": False}),
        style={"borderRadius": "8px", "backgroundColor": "white",
               "boxShadow": "0 1px 4px rgba(0,0,0,0.08)", "marginBottom": "20px"},
    )


@app.callback(
    Output("chart-grid", "children"),
    Input("section-tabs", "value"),
    Input("year-picker", "value"),
    Input("ps-contract-picker", "value"),
    Input("unit-toggle", "value"),
    Input("calspreads-sub-tabs", "value"),
    Input("argus-sub-tabs", "value"),
    Input("premium-sub-tabs", "value"),
    Input("fwddiff-sub-tabs", "value"),
    Input("parity-sub-tabs", "value"),
    Input("ytd-year-picker", "value"),
    Input("prodspreads-sub-tabs", "value"),
    Input("argus-cal-sub-tabs", "value"),
    Input("argus-prod-sub-tabs", "value"),
    Input("outright-sub-tabs", "value"),
    Input("fwd-compare-year-picker", "value"),
)
def update_charts(section: str, selected_years: list[int], ps_contract: str,
                  unit_mode: str, cs_sub: str, argus_sub: str, premium_sub: str,
                  fwddiff_sub: str, parity_sub: str, ytd_year: int, ps_sub: str,
                  argus_cal_sub: str, argus_prod_sub: str, out_sub: str,
                  compare_year: int) -> list:
    if section == "Technical Analysis":
        return []
    if not selected_years:
        return [html.P("No years selected.", style={"color": "#888"})]

    is_outright   = (section == "Outright Prices")
    is_cal_spread = (section == "Calendar Spreads")
    is_prod_spread = (section == "Product Spreads")
    is_argus      = (section == "Argus")
    is_parity     = (section == "Parity")
    is_local      = (unit_mode == "local")
    children      = []

    # Parity → Ind/China sub-tab: forward-curve snapshot charts (China +
    # India public Google Sheets), not the seasonal-overlay idiom — bypasses
    # the generic SUBSECTIONS loop below, same as Forward Curve does. India
    # and China are rendered as two clearly separate subsection blocks
    # (India first, then China — trader's explicit ask), same header/grid
    # pattern as the generic loop below (html.H3 + _GRID_STYLE per group).
    if is_parity and parity_sub == "pa-indchina":
        def _snapshot_graph(title, records):
            return dcc.Graph(
                figure=make_snapshot_curve_chart(title, records),
                config={"displayModeBar": False},
                style={"borderRadius": "8px", "backgroundColor": "white",
                       "boxShadow": "0 1px 4px rgba(0,0,0,0.08)"},
            )

        india_charts = []
        for key in INDIA_PARITY_KEYS:
            blocks = INDIA_PARITY.get(key, {})
            title = INDIA_PARITY_TITLES[key]
            for period, label in (("daily", "Daily"), ("weekly", "Weekly")):
                india_charts.append(_snapshot_graph(f"{title} ({label})", blocks.get(period, [])))

        china_charts = [
            _snapshot_graph(f"{CHINA_PARITY_TITLE} (Daily)", CHINA_PARITY.get("daily", [])),
            _snapshot_graph(f"{CHINA_PARITY_TITLE} (Weekly)", CHINA_PARITY.get("weekly", [])),
        ]

        return [
            html.Div([
                html.H3("India", style={**_SUBHEADER_STYLE, "marginTop": "8px"}),
                html.Div(india_charts, style=_GRID_STYLE),
            ]),
            html.Div([
                html.H3("China", style={**_SUBHEADER_STYLE, "marginTop": "28px"}),
                html.Div(china_charts, style=_GRID_STYLE),
            ]),
        ]

    # Forward Curve sub-tab under Calendar Spreads, and under Outright Prices
    # (same c1..c7 snapshot-by-year charts for every multi-contract futures
    # product — only the nav location differs).
    if (is_cal_spread and cs_sub == "cs-forward") or (is_outright and out_sub == "out-forward"):
        snapshot_md = _date.today().strftime("%m-%d")
        curves_src  = FORWARD_CURVES_LOCAL if is_local else FORWARD_CURVES
        compare_items = [(FORWARD_CURVE_TITLES.get(p, p), FORWARD_CURVES.get(p))
                        for p in FORWARD_CURVE_PRODUCTS]
        compare_fig = make_fwd_compare_chart(
            "All Instruments — Forward Curve Comparison", compare_items,
            _CONTRACT_ORDER, compare_year, snapshot_md,
        )
        charts = []
        for product in FORWARD_CURVE_PRODUCTS:
            curve_df = curves_src.get(product)
            if is_local:
                info = SPREAD_LOCAL_UNITS.get(product)
                ylabel = info["unit"] if info else "USD/MT"
            else:
                ylabel = "USD/MT"
            fig = make_forward_curve_chart(
                product, curve_df, selected_years, snapshot_md, ylabel,
            )
            charts.append(dcc.Graph(
                figure=fig,
                config={"displayModeBar": False},
                style={"borderRadius": "8px", "backgroundColor": "white",
                       "boxShadow": "0 1px 4px rgba(0,0,0,0.08)"},
            ))
        return [_fwd_compare_block(compare_fig), html.Div(charts, style=_GRID_STYLE)]

    # YTD sub-tab under Calendar Spreads: one chart per instrument, x-axis =
    # actual trading dates within the single selected year, one line per
    # c1-cN leg (c1-c2 .. c1-c7) so the term structure is comparable on one
    # shared y-axis scale.
    if is_cal_spread and cs_sub == "cs-ytd":
        curves_src = FORWARD_CURVES_LOCAL if is_local else FORWARD_CURVES
        charts = []
        for product in FORWARD_CURVE_PRODUCTS:
            curve_df = curves_src.get(product)
            if is_local:
                info = SPREAD_LOCAL_UNITS.get(product)
                ylabel = info["unit"] if info else "USD/MT"
            else:
                ylabel = "USD/MT"
            fig = make_ytd_spread_chart(product, curve_df, ytd_year, ylabel)
            charts.append(dcc.Graph(
                figure=fig,
                config={"displayModeBar": False},
                style={"borderRadius": "8px", "backgroundColor": "white",
                       "boxShadow": "0 1px 4px rgba(0,0,0,0.08)"},
            ))
        return [html.Div(charts, style=_GRID_STYLE)]

    # Forward Curve sub-tab under Product Spreads: one chart per product
    # spread (BO - FCPO, etc.), x-axis = contract legs (c1/c2/c3/c5/c7),
    # one line per selected year — same snapshot-by-year idiom as the
    # Calendar Spreads Forward Curve tab, so different tenors of the same
    # spread are comparable on one shared y-axis scale.
    if is_prod_spread and ps_sub == "ps-forward":
        snapshot_md = _date.today().strftime("%m-%d")
        compare_items = [(_PS_DISPLAY_NAMES.get(l, l), PRODUCT_SPREAD_CURVES.get(l))
                        for l in PRODUCT_SPREAD_LABELS]
        compare_fig = make_fwd_compare_chart(
            "All Product Spreads — Forward Curve Comparison", compare_items,
            _PS_CONTRACTS, compare_year, snapshot_md,
        )
        charts = []
        for label in PRODUCT_SPREAD_LABELS:
            curve_df = PRODUCT_SPREAD_CURVES.get(label)
            fig = make_ps_forward_curve_chart(label, curve_df, selected_years, snapshot_md)
            charts.append(dcc.Graph(
                figure=fig,
                config={"displayModeBar": False},
                style={"borderRadius": "8px", "backgroundColor": "white",
                       "boxShadow": "0 1px 4px rgba(0,0,0,0.08)"},
            ))
        return [_fwd_compare_block(compare_fig), html.Div(charts, style=_GRID_STYLE)]

    # Argus → Calendar Spreads → Forward Curve: one chart per curve product,
    # x-axis = M1/M2/M3 outright, one line per selected year — mirrors the
    # top-level Calendar Spreads → Forward Curve tab, just for Argus's own
    # M1/M2/M3 curve instead of c1..c7.
    if is_argus and argus_sub == "ar-calendar" and argus_cal_sub == "ar-cal-forward":
        snapshot_md = _date.today().strftime("%m-%d")
        compare_items = [(label, _argus_fwd_diff_wide(keys))
                        for label, keys in ARGUS_CAL_FORWARD_GROUPS.items()]
        compare_fig = make_fwd_compare_chart(
            "All Argus Curve Products — Forward Curve Comparison", compare_items,
            ["M1", "M2", "M3"], compare_year, snapshot_md,
        )
        charts = []
        for label, keys in ARGUS_CAL_FORWARD_GROUPS.items():
            curve_df = _argus_fwd_diff_wide(keys)
            fig = make_argus_forward_curve_chart(label, curve_df, selected_years, snapshot_md)
            charts.append(dcc.Graph(
                figure=fig,
                config={"displayModeBar": False},
                style={"borderRadius": "8px", "backgroundColor": "white",
                       "boxShadow": "0 1px 4px rgba(0,0,0,0.08)"},
            ))
        return [_fwd_compare_block(compare_fig), html.Div(charts, style=_GRID_STYLE)]

    # Argus → Calendar Spreads → YTD: one chart per curve product, x-axis =
    # actual trading dates within the single selected year, one line per
    # M1-M2/M1-M3 leg — mirrors the top-level Calendar Spreads → YTD tab,
    # just for Argus's own M1/M2/M3 curve instead of c1..c7.
    if is_argus and argus_sub == "ar-calendar" and argus_cal_sub == "ar-cal-ytd":
        charts = []
        for label, keys in ARGUS_CAL_FORWARD_GROUPS.items():
            curve_df = _argus_fwd_diff_wide(keys)
            fig = make_argus_ytd_chart(label, curve_df, ytd_year)
            charts.append(dcc.Graph(
                figure=fig,
                config={"displayModeBar": False},
                style={"borderRadius": "8px", "backgroundColor": "white",
                       "boxShadow": "0 1px 4px rgba(0,0,0,0.08)"},
            ))
        return [html.Div(charts, style=_GRID_STYLE)]

    # Argus → Product Spreads → Forward Curve: one chart per product spread,
    # x-axis = M1/M2/M3, one line per selected year — mirrors the top-level
    # Product Spreads → Forward Curve tab.
    if is_argus and argus_sub == "ar-product" and argus_prod_sub == "ar-prod-forward":
        snapshot_md = _date.today().strftime("%m-%d")
        compare_items = [(label, _argus_fwd_diff_wide(keys))
                        for label, keys in ARGUS_PS_FORWARD_GROUPS.items()]
        compare_fig = make_fwd_compare_chart(
            "All Argus Product Spreads — Forward Curve Comparison", compare_items,
            ["M1", "M2", "M3"], compare_year, snapshot_md,
        )
        charts = []
        for label, keys in ARGUS_PS_FORWARD_GROUPS.items():
            curve_df = _argus_fwd_diff_wide(keys)
            fig = make_argus_forward_curve_chart(label, curve_df, selected_years, snapshot_md)
            charts.append(dcc.Graph(
                figure=fig,
                config={"displayModeBar": False},
                style={"borderRadius": "8px", "backgroundColor": "white",
                       "boxShadow": "0 1px 4px rgba(0,0,0,0.08)"},
            ))
        return [_fwd_compare_block(compare_fig), html.Div(charts, style=_GRID_STYLE)]

    # Argus → Arb: quarterly MIP tables, not a chart grid at all — bypasses
    # the generic SUBSECTIONS loop below entirely. Each arb is its own
    # section/function, stacked top to bottom.
    if is_argus and argus_sub == "ar-arb":
        return [_argus_arb_tile(), _argus_china_ucome_arb_tile(selected_years)]

    # India Local data lookup helpers
    _india_out_src = INDIA_INR_OUT if is_local else INDIA_USD_OUT
    _india_spr_src = INDIA_INR_SPR if is_local else INDIA_USD_SPR
    _india_ylabel  = "INR/MT" if is_local else "USD/MT"

    if is_argus:
        groups = ARGUS_SUBSECTIONS.get(argus_sub, {})
    elif is_parity:
        # Parity → Malindo sub-tab reuses the existing "Malindo Spreads"
        # SUBSECTIONS entry as-is (seasonal-overlay idiom, driven by the
        # global year-picker) — it's just no longer its own top-level tab.
        groups = SUBSECTIONS.get("Malindo Spreads", {})
    else:
        groups = SUBSECTIONS.get(section, {})
    if is_argus and argus_sub == "ar-premium":
        # Nested 3rd-level tab (7-28 Day Diff | Fwd Diff) picks one group
        # instead of showing both stacked on the page.
        sel = {"prem-7to28": "7-28 Day Diff", "prem-fwd": "Fwd Diff"}.get(
            premium_sub, "7-28 Day Diff")
        groups = {sel: groups.get(sel, [])}
    if is_argus and argus_sub == "ar-tickets":
        children.append(_ticket_calc_block())
    for i, (subsection_name, names) in enumerate(groups.items()):
        charts = []
        for name in names:
            # Product spread labels → append selected contract suffix
            data_key = f"{name}_{ps_contract}" if name in _PS_LABELS_SET else name

            # Argus Biofuel Premium → Fwd Diff: forward-curve-style chart
            # (x = M1/M2/M3), not the standard single-series seasonal
            # overlay — built and appended directly, bypassing make_chart.
            # Graph vs Tabular are separate sub-tabs (fwddiff_sub), each
            # rendering ONE component per product directly into the grid
            # cell — never both stacked in one cell (a first attempt at
            # that stacked the dcc.Graph inside a display:flex wrapper
            # alongside the table, which triggered dcc.Graph's default
            # responsive="auto" 100%-height mode and caused an infinite
            # resize/regrow loop; kept as two plain sub-tabs instead).
            if is_argus and data_key in ARGUS_FWDDIFF_PRODUCTS:
                label, series_keys = ARGUS_FWDDIFF_PRODUCTS[data_key]
                wide = _argus_fwd_diff_wide(series_keys)
                if wide.empty:
                    charts.append(html.Div(
                        f"{data_key}: no data.",
                        style={"padding": "20px", "color": "#aaa", "border": "1px dashed #ccc", "borderRadius": "8px"},
                    ))
                    continue
                snapshot_md = _date.today().strftime("%m-%d")
                if fwddiff_sub == "fd-tabular":
                    charts.append(make_argus_fwd_diff_table(label, wide, selected_years, snapshot_md))
                else:
                    fig = make_argus_fwd_diff_chart(label, wide, selected_years, snapshot_md)
                    charts.append(dcc.Graph(
                        figure=fig,
                        config={"displayModeBar": False},
                        style={"borderRadius": "8px", "backgroundColor": "white", "boxShadow": "0 1px 4px rgba(0,0,0,0.08)"},
                    ))
                continue

            # India Local data — served from Google Sheet dicts
            if data_key.startswith("INDIA_"):
                df = _india_out_src.get(data_key)
                if df is None:
                    df = _india_spr_src.get(data_key)
                ylabel = _india_ylabel
            elif is_argus:
                df = ARGUS_DATA.get(data_key)
                ylabel = ARGUS_YLABELS.get(data_key, "USD/MT")
            elif is_outright:
                if is_local:
                    _out = OUTRIGHTS.get(data_key)
                else:
                    _out = OUTRIGHTS_USDMT.get(data_key, OUTRIGHTS.get(data_key))
                df = _out if _out is not None else SPREADS.get(data_key)
                ylabel = None  # set below
            elif is_cal_spread and is_local:
                df = SPREADS_LOCAL.get(data_key, SPREADS.get(data_key))
                ylabel = None
            else:
                df = SPREADS.get(data_key)
                ylabel = None

            if df is None or (hasattr(df, 'empty') and df.empty):
                charts.append(html.Div(
                    f"{data_key}: no data.",
                    style={"padding": "20px", "color": "#aaa", "border": "1px dashed #ccc", "borderRadius": "8px"},
                ))
                continue

            # Y-axis unit label (non-India)
            if ylabel is None:
                if is_cal_spread and is_local:
                    prefix = data_key.split("_")[0]
                    info = SPREAD_LOCAL_UNITS.get(prefix)
                    ylabel = info["unit"] if info else "USD/MT"
                elif is_outright:
                    if is_local and data_key in OUTRIGHT_UNITS:
                        ylabel = OUTRIGHT_UNITS[data_key]
                    elif not is_local:
                        ylabel = "USD/MT"
                    else:
                        ylabel = OUTRIGHT_UNITS.get(data_key, "USD/MT")
                else:
                    ylabel = YLABEL_OVERRIDES.get(data_key, "USD/MT")

            fig = make_chart(data_key, df, selected_years, ylabel=ylabel)
            charts.append(dcc.Graph(
                figure=fig,
                config={"displayModeBar": False},
                style={"borderRadius": "8px", "backgroundColor": "white", "boxShadow": "0 1px 4px rgba(0,0,0,0.08)"},
            ))

        top_margin = "8px" if i == 0 else "28px"
        use_h3 = section in ("Product Spreads", "Basis/Hedge Ratios", "Outright Prices", "Crush Spread", "Argus")
        header = (html.H3 if use_h3 else html.H4)(
            subsection_name, style={**_SUBHEADER_STYLE, "marginTop": top_margin}
        )
        children.append(html.Div([
            header,
            html.Div(charts, style=_GRID_STYLE),
        ]))

    return children



def _last_val(key):
    df = ARGUS_DATA.get(key)
    if df is None or df.empty:
        return None
    return float(df.sort_values("date")["spread_value"].iloc[-1])


_ARB_OPEN_COLOR, _ARB_OPEN_BG = "#1a7f37", "#e6f4ea"
_ARB_CLOSED_COLOR, _ARB_CLOSED_BG = "#c0392b", "#fbe9e7"
_ARB_TH = {"textAlign": "left", "padding": "5px 10px", "color": "#555", "borderBottom": "1px solid #ddd"}
_ARB_TD = {"padding": "5px 10px", "verticalAlign": "top", "borderBottom": "1px solid #eee"}


def _arb_status(diff):
    """diff = FAME 0 (EUR/t) - SME landed value (EUR/t); > 0 means FAME 0 is
    pricier than the import route, i.e. the arb is open. Returns (label,
    color, bg)."""
    is_open = diff > 0
    if is_open:
        return "ARB OPEN", _ARB_OPEN_COLOR, _ARB_OPEN_BG
    return "ARB CLOSED", _ARB_CLOSED_COLOR, _ARB_CLOSED_BG


def _arb_row(label, detail, amount_text, bold=False):
    """One line of an arb waterfall table (3 cols: Item | Detail | Amount) —
    shared by every arb section in the Arb tab."""
    return html.Tr([
        html.Td(label, style={**_ARB_TD, "fontWeight": "700" if bold else "400"}),
        html.Td(detail, style={**_ARB_TD, "color": "#666", "fontSize": "11px"}),
        html.Td(amount_text, style={**_ARB_TD, "fontWeight": "700" if bold else "400",
                                    "textAlign": "right"}),
    ])


# RME (Rapeseed Methyl Ester = Rapeseed OME) richness over FAME 0 FOB ARA
# feeds into the SME landed-value waterfall below (added 2026-10-14, trader's
# explicit ask): some of that richness also applies to SME, taken as a flat
# 1/4 fraction of the *latest* M1 RME-FAME0 spread — a live market overlay,
# not fixed at the quarter's own averaging window like the rest of the
# waterfall, so it uses the latest EURUSD too (not the quarter's locked FX).
_RME_PREMIUM_FRACTION = 0.25


def _argus_arb_tile():
    """Quarterly MIP (minimum import price) history table for SME landed
    into ARA — 8 quarters, 2025 Q1 through 2026 Q4 (trader's explicit ask),
    computed once at startup (ARGUS_MIP_QUARTERS/ARGUS_MIP_CURRENT). Plus,
    for the current (already-confirmed) quarter: a line-item waterfall
    building up the full "SME landed value" (one row per cost component —
    oil, conversion, freight, duty, and a live RME-vs-FAME0 premium — per
    trader's explicit ask for visibility into the calculation, added
    2026-10-14), and a per-month comparison of that value against the live
    Argus FAME 0 curve (M1/M2/M3 map to the quarter's 3 months in order)
    with an arb open/closed read per month. Not a chart; see
    compute_mip_table/compute_mip_quarter in argus_data.py for the
    quarter-fixing-window logic behind the first 6 waterfall rows."""
    if not ARGUS_MIP_QUARTERS:
        return html.Div(
            "MIP unavailable — missing LSEG SBO_arg (BO-ARGUPR-C1) or EUR= data.",
            style={"color": "#888", "padding": "20px"},
        )

    cur = ARGUS_MIP_CURRENT
    cur_label = cur["label"] if cur else None

    hist_rows = []
    for r in ARGUS_MIP_QUARTERS:
        is_cur = cur_label is not None and r["label"] == cur_label
        row_style = {"backgroundColor": "#eef5ff"} if is_cur else {}
        hist_rows.append(html.Tr([
            html.Td(r["label"] + (" (current)" if is_cur else ""),
                    style={**_ARB_TD, "fontWeight": "700" if is_cur else "400"}),
            html.Td(f"{r['window_start']} → {r['window_end']}", style=_ARB_TD),
            html.Td(f"{r['eurusd']:.4f} ({r['fx_date']})", style=_ARB_TD),
            html.Td(f"${r['avg_sbo_usd']:,.1f}", style=_ARB_TD),
            html.Td(f"€{r['mip_eur']:,.1f}", style={**_ARB_TD, "fontWeight": "700"}),
        ], style=row_style))

    hist_table = html.Table([
        html.Thead(html.Tr([html.Th(h, style=_ARB_TH) for h in
                   ("Quarter", "Fixing window", "EURUSD ref", "Avg SBO Argentina (USD/t)", "MIP (EUR/t)")])),
        html.Tbody(hist_rows),
    ], style={"width": "100%", "borderCollapse": "collapse", "fontSize": "12px", "marginBottom": "24px"})

    blocks = [
        html.Div("MIP (minimum import price) — SME landed into ARA, by quarter",
                 style={"fontWeight": "700", "fontSize": "14px", "marginBottom": "4px"}),
        html.Div(
            "MIP = (avg SBO Argentina over the quarter's fixing window ÷ EURUSD averaged over the same window "
            f"+ €{_argus_mod.ARB_CONVERSION_EUR:.0f} conversion + ${_argus_mod.ARB_FREIGHT_USD:.0f} freight ÷ EURUSD) "
            f"× {1 + _argus_mod.ARB_IMPORT_DUTY:.3f} duty. Fixing window = the 3 calendar months ending 2 months "
            "before the quarter starts (e.g. Q4 Oct-Dec priced off the Jun-Aug average) — the same lag the physical "
            "import price is actually confirmed under. One MIP number applies flat across all 3 months of its quarter.",
            style={"fontSize": "11px", "color": "#666", "marginBottom": "10px", "maxWidth": "900px"},
        ),
        hist_table,
    ]

    if cur is not None:
        eur_oil = cur["avg_sbo_usd"] / cur["eurusd"]
        eur_conversion = _argus_mod.ARB_CONVERSION_EUR
        eur_freight = _argus_mod.ARB_FREIGHT_USD / cur["eurusd"]
        subtotal_pre_duty = eur_oil + eur_conversion + eur_freight
        duty_amount = subtotal_pre_duty * _argus_mod.ARB_IMPORT_DUTY
        landed_post_duty = subtotal_pre_duty + duty_amount   # == cur["mip_eur"]

        live_eurusd = _last_val("ARGUS_FX_EURUSD") or cur["eurusd"]
        rme_m1 = _last_val("ARGUS_OUT_RSO_OME_M1")
        fame0_m1 = _last_val("ARGUS_OUT_FAME0_M1")
        rme_fame0_diff = _last_val("ARGUS_PS_RSO_OME_FAME0_M1")
        have_rme = rme_fame0_diff is not None
        rme_premium_eur = (_RME_PREMIUM_FRACTION * rme_fame0_diff / live_eurusd) if have_rme else 0.0
        sme_landed_value = landed_post_duty + rme_premium_eur

        waterfall_rows = [
            _arb_row("Avg SBO Argentina (fixing window)",
                     f"${cur['avg_sbo_usd']:,.1f}/t ÷ EURUSD {cur['eurusd']:.4f} [{cur['fx_date']}]",
                     f"€{eur_oil:,.1f}/t"),
            _arb_row("+ SME conversion cost", "soyoil → SME, flat assumption", f"€{eur_conversion:,.1f}/t"),
            _arb_row("+ Freight (Argentina → ARA)",
                     f"${_argus_mod.ARB_FREIGHT_USD:.0f}/t ÷ EURUSD {cur['eurusd']:.4f}",
                     f"€{eur_freight:,.1f}/t"),
            _arb_row("= Subtotal (pre-duty)", "", f"€{subtotal_pre_duty:,.1f}/t", bold=True),
            _arb_row(f"+ EU import duty ({_argus_mod.ARB_IMPORT_DUTY:.1%})",
                     "applied to the whole landed value", f"€{duty_amount:,.1f}/t"),
            _arb_row("= Landed cost (post-duty)", "", f"€{landed_post_duty:,.1f}/t", bold=True),
        ]
        if have_rme:
            waterfall_rows.append(_arb_row(
                f"+ RME premium (¼ × RME−FAME 0, latest M1)",
                f"¼ × (${rme_m1:,.1f} − ${fame0_m1:,.1f}) ÷ EURUSD {live_eurusd:.4f}",
                f"€{rme_premium_eur:,.1f}/t"))
        else:
            waterfall_rows.append(_arb_row("+ RME premium (¼ × RME−FAME 0, latest M1)",
                                            "no data", "€0.0/t"))
        waterfall_rows.append(_arb_row("= SME landed value (ARA)", "", f"€{sme_landed_value:,.1f}/t", bold=True))

        waterfall_table = html.Table([
            html.Thead(html.Tr([html.Th(h, style=_ARB_TH) for h in ("Item", "Detail", "Amount")])),
            html.Tbody(waterfall_rows),
        ], style={"width": "100%", "borderCollapse": "collapse", "fontSize": "12px", "marginBottom": "16px"})

        months = pd.date_range(cur["q_start"], cur["q_end"], freq="MS")
        month_rows = []
        for i, m in enumerate(months, start=1):
            fame_usd = _last_val(f"ARGUS_OUT_FAME0_M{i}")
            if fame_usd is None:
                month_rows.append(html.Tr([
                    html.Td(m.strftime("%b %Y"), style=_ARB_TD),
                    html.Td(f"M{i}", style=_ARB_TD),
                    html.Td(f"€{sme_landed_value:,.1f}", style=_ARB_TD),
                    html.Td("no data", style={**_ARB_TD, "color": "#aaa"}, colSpan=3),
                ]))
                continue
            fame_eur = fame_usd / live_eurusd
            diff = fame_eur - sme_landed_value
            label, color, bg = _arb_status(diff)
            month_rows.append(html.Tr([
                html.Td(m.strftime("%b %Y"), style=_ARB_TD),
                html.Td(f"M{i}", style=_ARB_TD),
                html.Td(f"€{sme_landed_value:,.1f}", style=_ARB_TD),
                html.Td(f"${fame_usd:,.1f}", style=_ARB_TD),
                html.Td(f"€{fame_eur:,.1f}", style=_ARB_TD),
                html.Td(f"{'+' if diff >= 0 else ''}€{diff:,.1f}", style={**_ARB_TD, "fontWeight": "700"}),
                html.Td(label, style={**_ARB_TD, "fontWeight": "800", "color": color, "backgroundColor": bg}),
            ]))

        month_table = html.Table([
            html.Thead(html.Tr([html.Th(h, style=_ARB_TH) for h in
                       ("Month", "Maturity", "SME landed value (EUR/t)", "FAME 0 (USD/t)", "FAME 0 (EUR/t)",
                        "Diff (EUR/t)", "Status")])),
            html.Tbody(month_rows),
        ], style={"width": "100%", "borderCollapse": "collapse", "fontSize": "12px"})

        blocks += [
            html.Div(f"Confirmed quarter detail — {cur['label']} (fixed {cur['window_start']} → "
                     f"{cur['window_end']}, EURUSD {cur['eurusd']:.4f} {cur['fx_date']})",
                     style={"fontWeight": "700", "fontSize": "14px", "marginTop": "10px", "marginBottom": "4px"}),
            html.Div(
                "SME landed value build-up, one row per item — the first 6 rows use the quarter's own fixing "
                "(window-average SBO + window-average EURUSD); the RME premium row uses live/latest data "
                "instead (not fixed at the quarter's fixing date).",
                style={"fontSize": "11px", "color": "#666", "marginBottom": "6px", "maxWidth": "900px"},
            ),
            waterfall_table,
            html.Div(
                "FAME 0 below is the latest Argus swap curve value (M1/M2/M3 = this quarter's 3 months, in order), "
                f"converted at the latest EURUSD ({live_eurusd:.4f}) — same basis as the RME premium row above, "
                "not the quarter's locked fixing FX.",
                style={"fontSize": "11px", "color": "#666", "marginBottom": "10px", "maxWidth": "900px"},
            ),
            month_table,
        ]
    else:
        blocks.append(html.Div(
            "Current quarter's MIP not yet computable (fixing window not fully observed in the LSEG data).",
            style={"color": "#888", "marginTop": "10px"},
        ))

    return html.Div(blocks, style={"background": "white", "border": "1px solid #e0e0e0", "borderRadius": "8px",
                                    "padding": "16px 20px", "maxWidth": "980px"})


# China UCOME CIF Malaysia (added 2026-10-14, 2nd Arb section, trader's
# explicit formula): a blended CIF Malaysia cost — 60% a China-UCOME import
# route, 40% a POME-fob-Malaysia route — compared against the pure POME
# route alone. All legs are already USD/t (no FX conversion needed, unlike
# the SME arb above). Trader's exact formula:
#   0.60*(China UCOME FOB + 50 freight + 8 trucking + 6 fobbing)
#   + 0.40*(POME FOB MSIA + 175 net costs) - (POME FOB MSIA + 175 net costs)
# which simplifies to 0.60*(china_route_landed - pome_reference), i.e. 60%
# of the China route's premium/discount to the pure-POME reference — but
# shown here as the literal unsimplified waterfall per trader's ask for
# visibility into the calculation. "China UCOME FOB" = the Argus sheet's
# new "Biodiesel UCOME ... RED bulk China fob prompt" column, added
# 2026-10-14 specifically for this arb (see UCOME_CHINA in argus_data.py).
# Constants (CHINA_UCOME_*/POME_MY_*) live in argus_data.py, shared with the
# daily ARGUS_ARB_CHINA_UCOME_CIF_MY series computed there for the chart
# below, so the live waterfall and the historical line can never drift apart.


def _argus_china_ucome_arb_tile(selected_years):
    """China UCOME CIF Malaysia arb — a line-item waterfall (one row per
    cost component, trader's explicit ask) ending in a blended CIF Malaysia
    value compared against the pure-POME reference cost, using the latest
    available Argus values (the trader's formula has no quarter/maturity
    dimension). Plus a historical seasonal-overlay chart of the same diff
    (`ARGUS_ARB_CHINA_UCOME_CIF_MY`, computed daily in argus_data.py) — per
    trader's explicit ask, since both legs have several years of history.
    Sign convention here is the OPPOSITE of _arb_status(): diff = blended
    CIF − POME reference; diff > 0 means the China-blend route costs MORE
    than pure POME (no incentive to use it) → ARB CLOSED; diff < 0 means
    the blend is cheaper → ARB OPEN."""
    A = _argus_mod
    china_fob = _last_val("ARGUS_OUT_UCOME_CHINA")
    pome_fob = _last_val("ARGUS_OUT_POME_REFINED_MALAYSIA")
    if china_fob is None or pome_fob is None:
        return html.Div(
            "China UCOME CIF Malaysia unavailable — missing Argus UCOME China FOB or POME fob Malaysia data.",
            style={"color": "#888", "padding": "20px", "marginTop": "16px"},
        )

    china_landed = china_fob + A.CHINA_UCOME_FREIGHT_MY + A.CHINA_UCOME_TRUCKING_MY + A.CHINA_UCOME_FOBBING_MY
    china_weighted = A.CHINA_UCOME_WEIGHT * china_landed
    pome_reference = pome_fob + A.POME_MY_NET_COSTS
    pome_weighted = A.POME_MY_WEIGHT * pome_reference
    blended_cif = china_weighted + pome_weighted
    diff = blended_cif - pome_reference

    is_closed = diff > 0   # costlier than pure POME -> no reason to use the blend
    label = "ARB CLOSED" if is_closed else "ARB OPEN"
    color, bg = (_ARB_CLOSED_COLOR, _ARB_CLOSED_BG) if is_closed else (_ARB_OPEN_COLOR, _ARB_OPEN_BG)

    rows = [
        _arb_row("China UCOME FOB (latest)", "", f"${china_fob:,.1f}/t"),
        _arb_row("+ Freight (China → Malaysia)", "", f"${A.CHINA_UCOME_FREIGHT_MY:,.1f}/t"),
        _arb_row("+ Trucking", "", f"${A.CHINA_UCOME_TRUCKING_MY:,.1f}/t"),
        _arb_row("+ Fobbing", "", f"${A.CHINA_UCOME_FOBBING_MY:,.1f}/t"),
        _arb_row("= China route landed cost", "", f"${china_landed:,.1f}/t", bold=True),
        _arb_row(f"× {A.CHINA_UCOME_WEIGHT:.0%} weight", "", f"${china_weighted:,.1f}/t"),
        _arb_row("POME FOB Malaysia (latest)", "", f"${pome_fob:,.1f}/t"),
        _arb_row("+ Net costs", "", f"${A.POME_MY_NET_COSTS:,.1f}/t"),
        _arb_row("= POME reference cost", "", f"${pome_reference:,.1f}/t", bold=True),
        _arb_row(f"× {A.POME_MY_WEIGHT:.0%} weight", "", f"${pome_weighted:,.1f}/t"),
        _arb_row("= China UCOME CIF Malaysia Blended", f"{A.CHINA_UCOME_WEIGHT:.0%} China route + {A.POME_MY_WEIGHT:.0%} POME route",
                 f"${blended_cif:,.1f}/t", bold=True),
        _arb_row("− POME reference cost", "pure-POME alternative (unweighted)", f"${pome_reference:,.1f}/t"),
        _arb_row("= Diff", "", f"{'+' if diff >= 0 else ''}${diff:,.1f}/t", bold=True),
    ]

    table = html.Table([
        html.Thead(html.Tr([html.Th(h, style=_ARB_TH) for h in ("Item", "Detail", "Amount")])),
        html.Tbody(rows),
    ], style={"width": "100%", "borderCollapse": "collapse", "fontSize": "12px", "marginBottom": "10px"})

    blocks = [
        html.Div("China UCOME CIF Malaysia",
                 style={"fontWeight": "700", "fontSize": "14px", "marginBottom": "4px"}),
        html.Div(
            "Blended CIF Malaysia cost — 60% a China-UCOME import route, 40% a POME-fob-Malaysia route — vs. the "
            "pure-POME reference alone. All legs already USD/t, no FX conversion needed.",
            style={"fontSize": "11px", "color": "#666", "marginBottom": "6px", "maxWidth": "900px"},
        ),
        table,
        html.Div([
            html.Span(label, style={"fontWeight": "800", "color": color, "backgroundColor": bg,
                                     "padding": "4px 10px", "borderRadius": "6px", "fontSize": "13px"}),
        ]),
    ]

    hist_df = ARGUS_DATA.get("ARGUS_ARB_CHINA_UCOME_CIF_MY")
    if hist_df is not None and not hist_df.empty:
        fig = make_chart("ARGUS_ARB_CHINA_UCOME_CIF_MY", hist_df, selected_years, "USD/MT")
        blocks += [
            html.Div("Historical diff (blended CIF − POME reference) — below 0 = arb open, above 0 = arb closed",
                     style={"fontSize": "11px", "color": "#666", "marginTop": "14px", "marginBottom": "4px"}),
            dcc.Graph(
                figure=fig,
                config={"displayModeBar": False},
                style={"borderRadius": "8px", "backgroundColor": "white",
                       "boxShadow": "0 1px 4px rgba(0,0,0,0.08)"},
            ),
        ]

    return html.Div(blocks, style={"background": "white", "border": "1px solid #e0e0e0", "borderRadius": "8px",
                                    "padding": "16px 20px", "maxWidth": "980px", "marginTop": "16px"})


def _ticket_calc_block():
    """Compact 'how it is calculated' panel shown above the Tickets charts.
    Constants come from argus_data so the text can never drift from the maths;
    worked example uses the latest UCOME (waste) ticket + FX."""
    A = _argus_mod
    gbp, eur = _last_val("ARGUS_FX_GBPUSD"), _last_val("ARGUS_FX_EURUSD")
    _u = A.BLEND_PRODUCTS["UCOME"]
    dens, mj, sav = _u[2], _u[3], _u[4]
    t_co2 = mj * A.FOSSIL_COMPARATOR_G_MJ * sav / 1000
    uk_p = _last_val("ARGUS_TICKET_TKT_UK_OTHER")
    de_p = _last_val("ARGUS_TICKET_TKT_DE_CONVENTIONAL")
    nl_p = _last_val("ARGUS_TICKET_TKT_NL_LRE_C")

    def f(x, nd=0):
        return "n/a" if x is None else f"{x:,.{nd}f}"

    uk_ex = de_ex = nl_ex = "n/a"
    if None not in (uk_p, gbp):
        litres = 1000 / dens
        tkt_ppl, mar_ppl = _last_val("ARGUS_BLENDTKT_UCOME_UK"), _last_val("ARGUS_BLEND_UCOME_UK")
        uk_ex = (f"ticket: 2 RTFC/l × {uk_p:.2f}p = {f(tkt_ppl,1)} p/l; premium: UCOME M1 − Gasoil M1 "
                 f"= ${f((tkt_ppl-mar_ppl)*gbp*litres/100)}/t ÷ {gbp:.3f} ÷ {f(litres)} l/t = {f(tkt_ppl-mar_ppl,1)} p/l; "
                 f"margin = {f(mar_ppl,1)} p/l") if tkt_ppl is not None and mar_ppl is not None else "n/a"
    if None not in (de_p, eur):
        de_eur = t_co2 * de_p
        de_ex = (f"{mj} MJ/kg × {A.FOSSIL_COMPARATOR_G_MJ:.0f} g/MJ × {sav} ÷ 1,000 = {t_co2:.2f} t CO2 saved per tonne of biofuel × €{f(de_p)} per t CO2 "
                 f"= €{f(de_eur)} per tonne of biofuel × {eur:.3f} = ${f(de_eur*eur)} per tonne of biofuel")
    if None not in (nl_p, eur):
        nl_eur = t_co2 * nl_p * 10
        nl_ex = (f"{t_co2:.2f} t CO2 saved per tonne of biofuel × €{f(nl_p*10)} per t CO2 ({nl_p:.1f} c/kg × 10) = €{f(nl_eur)} per tonne of biofuel "
                 f"(no national levy) × {eur:.3f} = ${f(nl_eur*eur)} per tonne of biofuel")

    th = {"textAlign": "left", "padding": "4px 8px", "color": "#555", "borderBottom": "1px solid #ddd"}
    td = {"padding": "4px 8px", "verticalAlign": "top", "borderBottom": "1px solid #eee"}
    rows = [
        ("UK", "FAME → Crop RTFC (1 RTFC/l)\nUCOME, HVO → Other RTFC (2 RTFC/l, waste)",
         "in pence per LITRE of fuel: ticket = RTFC per litre × RTFC price (p); premium = (premium USD/t ÷ GBPUSD) ÷ (1,000 ÷ density kg/l) × 100; margin = ticket − premium", uk_ex),
        ("Germany", "FAME, UCOME, HVO → Conventional GHG quota (non-advanced; UCO = 9B)",
         "t CO2 saved [MJ/kg × 94 g/MJ × GHG saving ÷ 1,000] × THG quota (€/t CO2) × EURUSD. No nEHS carbon levy.", de_ex),
        ("Netherlands", "FAME, UCOME, HVO → LRE-C (non-advanced; UCO = 9B)",
         "t CO2 saved [MJ/kg × 94 g/MJ × GHG saving ÷ 1,000] × LRE price (c/kg × 10 = €/t CO2) × EURUSD. No national levy.", nl_ex),
    ]
    body = [html.Tr([html.Td(c, style={**td, "fontWeight": "700"}),
                     html.Td(t, style={**td, "whiteSpace": "pre-line"}),
                     html.Td(fm, style=td), html.Td(ex, style=td)]) for c, t, fm, ex in rows]
    P = A.BLEND_PRODUCTS
    assumptions = (
        f"Blend margin = ticket value − premium to gasoil, in the same unit (UK p/litre; DE/NL USD/t). Premium (all products) = product M1 − ICE Gasoil M1 (LGOc1): FAME 0 M1, UCOME M1, HVO M1. "
        f"Positive = the ticket more than "
        f"pays for the premium over gasoil. Energy / GHG saving / density: "
        f"FAME & UCOME {P['FAME'][3]} MJ/kg, {P['FAME'][4]:.0%}, {P['FAME'][2]} kg/l; "
        f"HVO {P['HVO'][3]} MJ/kg, {P['HVO'][4]:.0%}, {P['HVO'][2]} kg/l. "
        f"Tickets are weekly/monthly snapshots, held until the next one. Worked example below = UCOME at latest prices "
        f"(GBPUSD {f(gbp,3)}, EURUSD {f(eur,3)})."
    )
    return html.Div([
        html.Div("How the blend margin is calculated — UK: pence per litre of fuel; Germany / Netherlands: USD per tonne of biofuel",
                 style={"fontWeight": "700", "fontSize": "12px", "color": "#333", "marginBottom": "4px"}),
        html.Div(assumptions, style={"fontSize": "11px", "color": "#666", "marginBottom": "6px"}),
        html.Table([html.Thead(html.Tr([html.Th(h, style=th) for h in
                    ("Market", "Ticket used", "Formula", "Worked example — UCOME, latest")])),
                    html.Tbody(body)],
                   style={"width": "100%", "borderCollapse": "collapse", "fontSize": "11px"}),
    ], style={"background": "white", "border": "1px solid #e0e0e0", "borderRadius": "8px",
              "padding": "10px 14px", "marginBottom": "14px"})


_UNIT_TOGGLE_STYLE = {
    "padding": "8px 24px",
    "borderBottom": "1px solid #e0e0e0",
    "backgroundColor": "#ffffff",
    "display": "flex",
    "alignItems": "center",
}


@app.callback(
    Output("main-controls",                "style"),
    Output("chart-grid",                    "style"),
    Output("ta-content",                    "style"),
    Output("ps-contract-wrapper",           "style"),
    Output("unit-toggle-bar",               "style"),
    Output("ai-content",                    "style"),
    Output("snd-content",                   "style"),
    Output("news-content",                  "style"),
    Output("cashprem-sub-tabs-wrapper",     "style"),
    Output("hedge-ratios-content",          "style"),
    Output("calspreads-sub-tabs-wrapper",   "style"),
    Output("argus-sub-tabs-wrapper",        "style"),
    Output("parity-sub-tabs-wrapper",       "style"),
    Output("ytd-year-picker-wrapper",       "style"),
    Output("prodspreads-sub-tabs-wrapper",  "style"),
    Input("section-tabs",      "value"),
    Input("cashprem-sub-tabs", "value"),
    Input("parity-sub-tabs",   "value"),
    Input("calspreads-sub-tabs", "value"),
    Input("prodspreads-sub-tabs", "value"),
    Input("argus-sub-tabs", "value"),
    Input("argus-cal-sub-tabs", "value"),
)
def _toggle_ta_view(tab: str, cp_sub: str, parity_sub: str, cs_sub: str, ps_sub: str,
                    argus_sub: str, argus_cal_sub: str):
    ps_vis     = ({"display": "block"} if tab == "Product Spreads" and ps_sub == "ps-seasonal"
                  else {"display": "none"})
    unit_vis   = _UNIT_TOGGLE_STYLE
    unit_hide  = {**_UNIT_TOGGLE_STYLE, "display": "none"}
    _hide      = {"display": "none"}
    _show_ai   = {"display": "block"}
    _show_snd  = {"display": "block", "padding": "16px 24px"}
    _show_news = {"display": "block", "padding": "16px 24px"}
    _cp_wrap_show = {"display": "block", "padding": "8px 24px 0"}
    _cs_wrap_show = {"display": "block", "padding": "8px 24px 0"}
    _ar_wrap_show = {"display": "block", "padding": "8px 24px 0"}
    _pa_wrap_show = {"display": "block", "padding": "8px 24px 0"}
    _ps_wrap_show = {"display": "block", "padding": "8px 24px 0"}
    _show_ratios  = {"display": "block"}
    _ytd_wrap_show = _CONTROLS_STYLE

    if tab == "Technical Analysis":
        return ({**_CONTROLS_STYLE, "display": "none"}, {"display": "none"},
                {"display": "block"}, {"display": "none"}, unit_vis,
                _hide, _hide, _hide, _hide, _hide, _hide, _hide, _hide, _hide, _hide)
    if tab == "OI/Volume":
        return ({**_CONTROLS_STYLE, "display": "none"}, {"display": "none"},
                {"display": "none"}, {"display": "none"}, unit_hide,
                _hide, _hide, _hide, _hide, _hide, _hide, _hide, _hide, _hide, _hide)
    if tab == "AI":
        return ({**_CONTROLS_STYLE, "display": "none"}, {"display": "none"},
                {"display": "none"}, {"display": "none"}, unit_hide,
                _show_ai, _hide, _hide, _hide, _hide, _hide, _hide, _hide, _hide, _hide)
    if tab == "SND":
        return (_CONTROLS_STYLE, {"display": "none"},
                {"display": "none"}, {"display": "none"}, unit_hide,
                _hide, _show_snd, _hide, _hide, _hide, _hide, _hide, _hide, _hide, _hide)
    if tab == "News":
        return ({**_CONTROLS_STYLE, "display": "none"}, {"display": "none"},
                {"display": "none"}, {"display": "none"}, unit_hide,
                _hide, _hide, _show_news, _hide, _hide, _hide, _hide, _hide, _hide, _hide)
    if tab == "Calendar":
        # Calendar has its own content div toggled by _toggle_calendar_view; here
        # we just hide every other pane (chart-grid, TA, AI, News, etc.).
        return ({**_CONTROLS_STYLE, "display": "none"}, {"display": "none"},
                {"display": "none"}, {"display": "none"}, unit_hide,
                _hide, _hide, _hide, _hide, _hide, _hide, _hide, _hide, _hide, _hide)
    if tab == "Margins":
        # Margins has its own content div toggled by _toggle_margins; hide all else.
        return ({**_CONTROLS_STYLE, "display": "none"}, {"display": "none"},
                {"display": "none"}, {"display": "none"}, unit_hide,
                _hide, _hide, _hide, _hide, _hide, _hide, _hide, _hide, _hide, _hide)
    if tab == "CFTC":
        # CFTC has its own content div toggled by _toggle_cftc_view; hide all else.
        return ({**_CONTROLS_STYLE, "display": "none"}, {"display": "none"},
                {"display": "none"}, {"display": "none"}, unit_hide,
                _hide, _hide, _hide, _hide, _hide, _hide, _hide, _hide, _hide, _hide)
    if tab == "HVO/SAF Atlas":
        # Own content div toggled by _toggle_hvo; hide every other pane.
        return ({**_CONTROLS_STYLE, "display": "none"}, {"display": "none"},
                {"display": "none"}, {"display": "none"}, unit_hide,
                _hide, _hide, _hide, _hide, _hide, _hide, _hide, _hide, _hide, _hide)
    if tab == "Basis/Hedge Ratios":
        if cp_sub == "cp-ratios":
            return ({**_CONTROLS_STYLE, "display": "none"}, {"display": "none"},
                    {"display": "none"}, {"display": "none"}, unit_hide,
                    _hide, _hide, _hide, _cp_wrap_show, _show_ratios, _hide, _hide, _hide, _hide, _hide)
        if cp_sub == "cp-tradable":
            # Keep main-controls (year picker) visible; hide the prices grid and
            # hedge content — the Tradable Prem pane has its own visibility callback.
            return (_CONTROLS_STYLE, {"display": "none"},
                    {"display": "none"}, {"display": "none"}, unit_hide,
                    _hide, _hide, _hide, _cp_wrap_show, _hide, _hide, _hide, _hide, _hide, _hide)
        return (_CONTROLS_STYLE, {"padding": "16px 24px"},
                {"display": "none"}, ps_vis, unit_vis,
                _hide, _hide, _hide, _cp_wrap_show, _hide, _hide, _hide, _hide, _hide, _hide)
    if tab == "Calendar Spreads":
        if cs_sub == "cs-ytd":
            # Single-year picker instead of the multi-select "Years" picker
            # — the underlying dcc.Dropdown keeps its own stored value, so
            # update_charts still gets a non-empty selected_years and never
            # hits the "no years" guard (same trick as Parity → Ind/China).
            return ({**_CONTROLS_STYLE, "display": "none"}, {"padding": "16px 24px"},
                    {"display": "none"}, ps_vis, unit_vis,
                    _hide, _hide, _hide, _hide, _hide, _cs_wrap_show, _hide, _hide, _ytd_wrap_show, _hide)
        return (_CONTROLS_STYLE, {"padding": "16px 24px"},
                {"display": "none"}, ps_vis, unit_vis,
                _hide, _hide, _hide, _hide, _hide, _cs_wrap_show, _hide, _hide, _hide, _hide)
    if tab == "Product Spreads":
        # Forward Curve sub-tab: contract picker doesn't apply (all contracts
        # plot at once on the x-axis) — hide it; keep the multi-year picker
        # visible since it drives which years overlay as separate lines.
        return (_CONTROLS_STYLE, {"padding": "16px 24px"},
                {"display": "none"}, ps_vis, unit_vis,
                _hide, _hide, _hide, _hide, _hide, _hide, _hide, _hide, _hide, _ps_wrap_show)
    if tab == "Argus":
        if argus_sub == "ar-calendar" and argus_cal_sub == "ar-cal-ytd":
            # YTD uses the same single-year picker as the top-level
            # Calendar Spreads → YTD tab, not the multi-year main-controls.
            return ({**_CONTROLS_STYLE, "display": "none"}, {"padding": "16px 24px"},
                    {"display": "none"}, {"display": "none"}, unit_hide,
                    _hide, _hide, _hide, _hide, _hide, _hide, _ar_wrap_show, _hide,
                    _ytd_wrap_show, _hide)
        return (_CONTROLS_STYLE, {"padding": "16px 24px"},
                {"display": "none"}, {"display": "none"}, unit_hide,
                _hide, _hide, _hide, _hide, _hide, _hide, _ar_wrap_show, _hide, _hide, _hide)
    if tab == "Parity":
        if parity_sub == "pa-indchina":
            # Snapshot forward-curve charts don't use the year-picker/unit
            # toggle at all — hide main-controls entirely (the Dropdown
            # keeps its default `value`, so update_charts still gets a
            # non-empty selected_years and never hits the "no years" guard).
            return ({**_CONTROLS_STYLE, "display": "none"}, {"padding": "16px 24px"},
                    {"display": "none"}, {"display": "none"}, unit_hide,
                    _hide, _hide, _hide, _hide, _hide, _hide, _hide, _pa_wrap_show, _hide, _hide)
        return (_CONTROLS_STYLE, {"padding": "16px 24px"},
                {"display": "none"}, {"display": "none"}, unit_vis,
                _hide, _hide, _hide, _hide, _hide, _hide, _hide, _pa_wrap_show, _hide, _hide)
    return (_CONTROLS_STYLE, {"padding": "16px 24px"},
            {"display": "none"}, ps_vis, unit_vis,
            _hide, _hide, _hide, _hide, _hide, _hide, _hide, _hide, _hide, _hide)


# ── Tradable Premium tab ──────────────────────────────────────────────────────

@app.callback(
    Output("tradable-prem-content", "style"),
    Input("section-tabs", "value"),
    Input("cashprem-sub-tabs", "value"),
)
def _toggle_tradable_prem(section: str, cp_sub: str):
    if section == "Basis/Hedge Ratios" and cp_sub == "cp-tradable":
        return {"display": "block"}
    return {"display": "none"}


# ── Margins tab ───────────────────────────────────────────────────────────────

@app.callback(
    Output("margins-content", "style"),
    Input("section-tabs", "value"),
)
def _toggle_margins(section: str):
    return {"display": "block"} if section == "Margins" else {"display": "none"}


# Drive the embedded HTML's internal tab from the Dash sub-tab value (clientside so
# it can reach the iframe's window). The injected _MARGIN_SHIM listens for this.
app.clientside_callback(
    """
    function(subval){
        var map={'mg-overview':'overview','mg-vars':'vars','mg-theory':'theory',
                 'mg-flows':'flows','mg-summary':'summary','mg-rules':'rules'};
        var t=map[subval];
        var f=document.getElementById('margins-iframe');
        if(t && f && f.contentWindow){
            f.contentWindow.postMessage({type:'margins-select', tab:t}, '*');
        }
        return '';
    }
    """,
    Output("margins-iframe", "title"),   # dummy sink
    Input("margins-sub-tabs", "value"),
)


# ── HVO/SAF Atlas tab ─────────────────────────────────────────────────────────

@app.callback(
    Output("hvo-content", "style"),
    Output("hvo-iframe", "src"),
    Input("section-tabs", "value"),
    State("hvo-iframe", "src"),
)
def _toggle_hvo(section: str, cur_src: str):
    """Show/hide the atlas pane and lazily load the iframe.

    The src is set in the SAME callback as the style so Dash applies both in one
    render pass — the iframe therefore starts loading only once its container is
    visible and has a real width. Loading it while hidden makes datamaps compute
    its projection/aspectRatio from a zero-width element → distorted map.
    Once set, the src is left alone so switching tabs never reloads the page.
    """
    if section != "HVO/SAF Atlas":
        return {"display": "none"}, (cur_src or "")
    src = cur_src or f"/hvo-embed?v={_HVO_TOKEN or '0'}"
    return {"display": "block"}, src


# Belt-and-braces: re-kick the atlas's own resize handler whenever the tab is
# re-opened. If the browser window is resized while the pane is hidden, datamaps'
# resize() runs against a zero-width container and mangles the map; firing a resize
# once it is visible again lets it recompute against the real width.
app.clientside_callback(
    """
    function(sec){
        if(sec === 'HVO/SAF Atlas'){
            setTimeout(function(){
                var f = document.getElementById('hvo-iframe');
                if(f && f.contentWindow){
                    try { f.contentWindow.dispatchEvent(new Event('resize')); }
                    catch(e) {}
                }
            }, 300);
        }
        return '';
    }
    """,
    Output("hvo-iframe", "title"),   # dummy sink
    Input("section-tabs", "value"),
)


def _tp_section_charts(region: str, selected_years: list[int]) -> list:
    """Build the row of 5 product seasonal charts for one region."""
    charts = []
    for product in config.TRADABLE_PREM_PRODUCTS:
        key = f"TP_{region}_{product}"
        df  = TRADABLE_PREM.get(key)
        if df is None or df.empty:
            charts.append(html.Div(
                f"{product}: no data.",
                style={"padding": "20px", "color": "#aaa",
                       "border": "1px dashed #ccc", "borderRadius": "8px"},
            ))
            continue
        fig = make_chart(key, df, selected_years, ylabel="USD/MT")
        charts.append(dcc.Graph(
            figure=fig,
            config={"displayModeBar": False},
            style={"borderRadius": "8px", "backgroundColor": "white",
                   "boxShadow": "0 1px 4px rgba(0,0,0,0.08)"},
        ))
    return [html.Div(charts, style=_GRID_STYLE)]


@app.callback(
    Output("tp-malaysia-grid",  "children"),
    Output("tp-indonesia-grid", "children"),
    Input("section-tabs",      "value"),
    Input("cashprem-sub-tabs", "value"),
    Input("year-picker",       "value"),
)
def _update_tradable_prem(section: str, cp_sub: str,
                          selected_years: list[int]):
    if section != "Basis/Hedge Ratios" or cp_sub != "cp-tradable":
        raise PreventUpdate
    if not TRADABLE_PREM:
        msg = html.Div(
            "Tradable Premium data unavailable — add the Google service-account "
            "key at LSEGDashboard/secrets/gsheet_service_account.json and share "
            "the sheet with its email, then re-run the daily update.",
            style={"padding": "24px", "color": "#888",
                   "border": "1px dashed #ccc", "borderRadius": "8px"},
        )
        return [msg], [msg]
    if not selected_years:
        msg = html.P("No years selected.", style={"color": "#888"})
        return [msg], [msg]
    return (_tp_section_charts("MY", selected_years),
            _tp_section_charts("ID", selected_years))


@app.callback(
    [Output(f"ta-{ric}", "figure") for ric in TA_INSTRUMENTS]
    + [Output(f"ta-spread-{key}", "figure") for key in TA_SPREAD_INSTRUMENTS],
    Input("ta-year-picker", "value"),
    Input("section-tabs",   "value"),
    Input("unit-toggle",    "value"),
    prevent_initial_call=True,
)
def update_ta_charts(year_from: int, tab: str, unit_mode: str) -> list:
    if tab != "Technical Analysis":
        raise PreventUpdate
    if year_from is None:
        year_from = _TA_DEFAULT_YEAR

    is_local = (unit_mode == "local")
    start    = pd.Timestamp(f"{year_from}-01-01")

    # Outright instruments — pick local or USD/MT data source
    source = TA_WIDE if is_local else TA_WIDE_USDMT
    data   = source[source["Date"] >= start].copy()
    figures = []
    for ric in TA_INSTRUMENTS:
        title = TITLES.get(ric, ric)
        if ric not in data.columns or data[ric].dropna().empty:
            figures.append(go.Figure(layout=go.Layout(
                title=f"{title}: no data", height=400,
            )))
            continue
        sub = data[["Date", ric]].dropna().reset_index(drop=True)
        ranked_levels = calculate_ranked_levels(sub, ric, ric)
        if ric in config.FX_RICS:
            ylabel = "Rate"   # FX is an exchange rate in both unit modes
        else:
            ylabel = OUTRIGHT_UNITS.get(ric, "USD/MT") if is_local else "USD/MT"
        fig = create_instrument_plot(sub, ranked_levels, title, ric, ric, ric, ric, chart_type='line', ylabel=ylabel)
        figures.append(fig)

    # Product spread instruments — always USD/MT (no toggle)
    spread_data = TA_SPREAD_WIDE[TA_SPREAD_WIDE["Date"] >= start].copy()
    for key in TA_SPREAD_INSTRUMENTS:
        if key not in spread_data.columns or spread_data[key].dropna().empty:
            figures.append(go.Figure(layout=go.Layout(
                title=f"{TITLES.get(key, key)}: no data", height=400,
            )))
            continue
        sub = spread_data[["Date", key]].dropna().reset_index(drop=True)
        ranked_levels = calculate_ranked_levels(sub, key, key)
        title = TITLES.get(key, key)
        ylabel = '$/bushel' if key.startswith("CRUSH") else 'USD/MT'
        fig = create_instrument_plot(sub, ranked_levels, title, key, key, key, key, chart_type='line', ylabel=ylabel)
        figures.append(fig)

    return figures


@app.callback(
    Output("oi-content", "style"),
    Input("section-tabs", "value"),
)
def _toggle_oi_view(tab: str):
    if tab == "OI/Volume":
        return {"display": "block"}
    return {"display": "none"}


@app.callback(
    Output("calendar-content", "style"),
    Input("section-tabs", "value"),
)
def _toggle_calendar_view(tab: str):
    if tab == "Calendar":
        return {"display": "block", "padding": "8px 16px"}
    return {"display": "none"}


@app.callback(
    Output("cftc-content", "style"),
    Input("section-tabs", "value"),
)
def _toggle_cftc_view(tab: str):
    if tab == "CFTC":
        return {"display": "block", "padding": "8px 16px"}
    return {"display": "none"}


@app.callback(
    Output("fwd-compare-year-wrapper", "style"),
    Input("section-tabs",        "value"),
    Input("calspreads-sub-tabs", "value"),
    Input("outright-sub-tabs",   "value"),
    Input("prodspreads-sub-tabs", "value"),
    Input("argus-sub-tabs",      "value"),
    Input("argus-cal-sub-tabs",  "value"),
    Input("argus-prod-sub-tabs", "value"),
)
def _toggle_fwd_compare_year(section, cs_sub, out_sub, ps_sub,
                             argus_sub, argus_cal_sub, argus_prod_sub):
    """Show the dedicated single-year 'Compare Year' picker only on the five
    Forward Curve sub-tabs it drives — independent of the big
    `_toggle_ta_view` callback so adding it didn't require touching every
    one of that callback's existing branches."""
    on_forward_curve = (
        (section == "Calendar Spreads" and cs_sub == "cs-forward") or
        (section == "Outright Prices" and out_sub == "out-forward") or
        (section == "Product Spreads" and ps_sub == "ps-forward") or
        (section == "Argus" and argus_sub == "ar-calendar" and argus_cal_sub == "ar-cal-forward") or
        (section == "Argus" and argus_sub == "ar-product" and argus_prod_sub == "ar-prod-forward")
    )
    return {**_CONTROLS_STYLE, "display": "flex"} if on_forward_curve else {**_CONTROLS_STYLE, "display": "none"}


# ── SND tab callback ─────────────────────────────────────────────────────────

def _snd_seasonal_chart(item_name: str, df: pd.DataFrame, selected_years: list[int],
                        section: str, unit: str = "MT") -> go.Figure:
    """Build a seasonal chart for one SND item: one line per year, x=month."""
    available_years = sorted(df["year"].unique())
    years_to_plot = sorted(set(available_years) & set(selected_years))

    fig = go.Figure()
    month_labels = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

    for yr in years_to_plot:
        yr_df = df[df["year"] == yr].sort_values("month")
        color = year_color(yr, years_to_plot)
        width = year_width(yr, years_to_plot)
        opacity = year_opacity(yr, years_to_plot)

        x_labels = [month_labels[m - 1] for m in yr_df["month"]]
        fig.add_trace(go.Scatter(
            x=x_labels,
            y=yr_df["value"],
            mode="lines+markers",
            name=str(yr),
            line=dict(color=color, width=width),
            marker=dict(size=4),
            opacity=opacity,
            hovertemplate=f"<b>{yr}</b><br>%{{x}}<br>%{{y:,.0f}}<extra></extra>",
        ))

    fig.update_layout(
        title=dict(text=f"{section} — {item_name}", font=dict(size=13)),
        xaxis=dict(showgrid=True, gridcolor="#e8e8e8",
                   categoryorder="array", categoryarray=month_labels),
        yaxis=dict(title=unit, showgrid=True, gridcolor="#e8e8e8"),
        legend=dict(orientation="v", x=1.01, y=1, font=dict(size=10)),
        margin=dict(l=60, r=80, t=40, b=40),
        plot_bgcolor="white",
        paper_bgcolor="white",
        hovermode="x unified",
        height=320,
    )
    return fig


@app.callback(
    Output("snd-sub-content", "children"),
    Input("section-tabs", "value"),
    Input("snd-sub-tabs", "value"),
    Input("year-picker", "value"),
)
def _update_snd(tab: str, sub_tab: str, selected_years: list[int]):
    if tab != "SND":
        raise PreventUpdate

    # ── Graphs sub-tab ──────────────────────────────────────────────────────
    if sub_tab == "snd-graphs":
        if not selected_years:
            return [html.P("No years selected.", style={"color": "#888"})]

        from snd_data import load_snd, load_snd_indonesia, load_snd_india
        result = load_snd()
        if result is None:
            return [html.P("SND data file not found.", style={"color": "#c0392b"})]

        sections = []

        # Malaysia
        my_charts = []
        for item_name, df in result.items():
            fig = _snd_seasonal_chart(item_name, df, selected_years, "Malaysia")
            my_charts.append(dcc.Graph(
                figure=fig,
                config={"displayModeBar": False},
                style={"borderRadius": "8px", "backgroundColor": "white",
                       "boxShadow": "0 1px 4px rgba(0,0,0,0.08)"},
            ))
        sections.append(html.Div([
            html.H3("Malaysia", style={**_SUBHEADER_STYLE, "marginTop": "8px"}),
            html.Div(my_charts, style=_GRID_STYLE),
        ]))

        # Indonesia
        id_result = load_snd_indonesia()
        if id_result:
            id_charts = []
            for item_name, df in id_result.items():
                fig = _snd_seasonal_chart(item_name, df, selected_years, "Indonesia")
                id_charts.append(dcc.Graph(
                    figure=fig,
                    config={"displayModeBar": False},
                    style={"borderRadius": "8px", "backgroundColor": "white",
                           "boxShadow": "0 1px 4px rgba(0,0,0,0.08)"},
                ))
            sections.append(html.Div([
                html.H3("Indonesia", style={**_SUBHEADER_STYLE, "marginTop": "32px"}),
                html.Div(id_charts, style=_GRID_STYLE),
            ]))

        # India
        india_result = load_snd_india()
        if india_result:
            india_charts = []
            for item_name, df in india_result.items():
                unit = "%" if "Ratio" in item_name else "'000 MT"
                fig = _snd_seasonal_chart(item_name, df, selected_years, "India", unit=unit)
                india_charts.append(dcc.Graph(
                    figure=fig,
                    config={"displayModeBar": False},
                    style={"borderRadius": "8px", "backgroundColor": "white",
                           "boxShadow": "0 1px 4px rgba(0,0,0,0.08)"},
                ))
            sections.append(html.Div([
                html.H3("India", style={**_SUBHEADER_STYLE, "marginTop": "32px"}),
                html.Div(india_charts, style=_GRID_STYLE),
            ]))

        return sections

    # ── Exports sub-tab ───────────────────────────────────────────────────
    if sub_tab == "snd-exports":
        from snd_data import fetch_exports_2026_table
        exports_df = fetch_exports_2026_table()
        if exports_df is None:
            return [html.P("Failed to load exports data.", style={"color": "#c0392b"})]
        return [_render_snd_table(exports_df, "Malindo 2026 Exports — By Destination")]

    # ── Tabular sub-tab ─────────────────────────────────────────────────────
    if sub_tab == "snd-tabular":
        from snd_data import (fetch_malaysia_2026_table, fetch_indonesia_2026_table,
                              fetch_india_2026_table, fetch_bmd_curve, fetch_bmd_as_of)
        children = []
        bmd_prices = fetch_bmd_curve()
        bmd_date   = fetch_bmd_as_of()

        my_table = fetch_malaysia_2026_table()
        if my_table is not None:
            children.append(_render_snd_table(my_table, "Malaysia 2026", bmd_prices, bmd_date))
        else:
            children.append(html.P("Failed to load Malaysia 2026 table.", style={"color": "#c0392b"}))

        id_table = fetch_indonesia_2026_table()
        if id_table is not None:
            children.append(html.Div(style={"marginTop": "32px"}))
            children.append(_render_snd_table(id_table, "Indonesia 2026", bmd_prices, bmd_date))
        else:
            children.append(html.P("Failed to load Indonesia 2026 table.", style={"color": "#c0392b"}))

        india_table = fetch_india_2026_table()
        if india_table is not None:
            children.append(html.Div(style={"marginTop": "32px"}))
            children.append(_render_snd_table(india_table, "India 2026"))
        else:
            children.append(html.P("Failed to load India 2026 table.", style={"color": "#c0392b"}))

        return children

    raise PreventUpdate


# Section-header row labels in the 2026 sheet (used for styling)
_SND_SECTION_HEADERS = {"SUPPLY", "OFFTAKE", "STOCK CHANGE", "MEMO / KEY RATIOS"}


_REGION_ROWS = {"EU 27", "Oth. Europe", "C.I.S.", "Africa", "America", "Asia", "Oceania", "Other"}


def _render_snd_table(df: pd.DataFrame, title: str = "Malaysia 2026",
                      bmd_prices: dict[int, float] | None = None,
                      bmd_as_of: str = "") -> html.Div:
    """Render an SND DataFrame as a styled html.Table with optional BMD row."""
    cols = df.columns.tolist()

    # Classify columns
    highlight_cols = set()   # Annual / Prior Yr / YoY
    quarter_cols = set()     # Q1-Q4
    for i, c in enumerate(cols):
        if c in ("Annual", "YoY%", "YoY %", "Prior Yr"):
            highlight_cols.add(i)
        if c in ("Q1", "Q2", "Q3", "Q4"):
            quarter_cols.add(i)

    # Border helpers — thicker left border on quarter and end-segment columns
    def _col_border_left(i):
        if i in quarter_cols:
            return "2px solid #b0b0b0"
        if i in highlight_cols:
            return "2px solid #7a9ec2"
        return "1px solid #ddd"

    def _col_border_right(i):
        if i in quarter_cols:
            return "2px solid #b0b0b0"
        if i == len(cols) - 1:
            return "1px solid #ddd"
        return "none"

    # ── Header row ──────────────────────────────────────────────────────────
    header_cells = []
    for i, c in enumerate(cols):
        is_hl = i in highlight_cols
        is_q = i in quarter_cols
        header_cells.append(html.Th(c, style={
            "padding": "10px 10px",
            "textAlign": "right" if i > 0 else "left",
            "fontWeight": "700",
            "fontSize": "11px",
            "color": "#fff" if is_hl else "#4a235a" if is_q else "#555",
            "backgroundColor": "#2c3e50" if is_hl else "#d7bde2" if is_q else "#f2f3f4",
            "borderBottom": "3px solid #1a252f",
            "borderLeft": _col_border_left(i),
            "borderRight": _col_border_right(i),
            "whiteSpace": "nowrap",
            "textTransform": "uppercase",
            "letterSpacing": "0.5px",
        }))
    header = html.Tr(header_cells)

    # ── Data rows ───────────────────────────────────────────────────────────
    rows = []
    row_idx = 0
    for _, row in df.iterrows():
        label = str(row.iloc[0]).strip()

        is_section = "\u25b6" in label
        is_blank = label == "" and all(str(row.iloc[c]).strip() == "" for c in range(1, len(cols)))
        is_mom = "Month-on-Month" in label or label.endswith("%")
        is_note = (label.startswith("\u2713") or label.startswith("✓")
                   or label.startswith("\u2726") or label.startswith("✦"))
        is_total = ("Total Supply" in label or "Total Offtake" in label
                    or "Total Demand" in label or "Total Malindo" in label
                    or label == "TOTAL")
        is_end_stock = (("End Stock" in label or "Ending Stock" in label)
                        and label.lstrip().startswith(("8.", "9.", "End", "Ending")))
        is_region = label in _REGION_ROWS

        if is_blank:
            rows.append(html.Tr([html.Td("", colSpan=len(cols),
                        style={"height": "4px", "padding": "0",
                               "borderBottom": "none"})]))
            row_idx += 1
            continue

        if is_section:
            clean_label = label.replace("\u25b6", "").strip()
            rows.append(html.Tr([
                html.Td(clean_label, colSpan=len(cols), style={
                    "padding": "12px 14px 8px",
                    "fontWeight": "700",
                    "fontSize": "12px",
                    "color": "#fff",
                    "backgroundColor": "#2c3e50",
                    "letterSpacing": "1px",
                    "textTransform": "uppercase",
                    "borderBottom": "none",
                    "borderRadius": "4px",
                }),
            ]))
            row_idx += 1
            continue

        if is_note:
            rows.append(html.Tr([
                html.Td(label, colSpan=len(cols), style={
                    "padding": "4px 14px",
                    "fontSize": "10px",
                    "color": "#999",
                    "fontStyle": "italic",
                    "borderBottom": "none",
                }),
            ]))
            row_idx += 1
            continue

        zebra_bg = "#f7f8f9" if row_idx % 2 == 0 else "#fff"

        cells = []
        for i, val in enumerate(row):
            val_str = str(val) if val != "" else ""
            is_hl = i in highlight_cols
            is_q = i in quarter_cols
            bl = _col_border_left(i)
            br = _col_border_right(i)

            if is_mom:
                # MoM / percentage rows
                neg = val_str.startswith("-") or val_str.startswith("(")
                pos = i > 0 and val_str and not neg and not val_str.startswith("M") and not val_str.startswith("I")
                style = {
                    "padding": "3px 10px",
                    "fontSize": "10px",
                    "borderBottom": "1px solid #ddd",
                    "borderLeft": bl, "borderRight": br,
                    "textAlign": "right" if i > 0 else "left",
                    "fontWeight": "700" if i > 0 and val_str else "normal",
                    "color": "#c0392b" if neg else "#27ae60" if pos else "#999",
                    "backgroundColor": "#fef9e7" if is_hl and val_str else
                                       "#faf5ff" if is_q else zebra_bg,
                    "fontStyle": "italic" if i == 0 else "normal",
                }
            elif is_total:
                style = {
                    "padding": "8px 10px",
                    "fontSize": "12px",
                    "borderTop": "2px solid #555",
                    "borderBottom": "3px double #555",
                    "borderLeft": bl, "borderRight": br,
                    "textAlign": "right" if i > 0 else "left",
                    "fontWeight": "800",
                    "color": "#fff" if is_hl else "#1a252f",
                    "backgroundColor": "#2c3e50" if is_hl else "#e8e8e8" if is_q else "#eaecee",
                }
            elif is_region:
                # Region summary rows — bold, slightly tinted background
                style = {
                    "padding": "7px 10px",
                    "fontSize": "12px",
                    "borderTop": "2px solid #aaa",
                    "borderBottom": "1px solid #ccc",
                    "borderLeft": bl, "borderRight": br,
                    "textAlign": "right" if i > 0 else "left",
                    "fontWeight": "700",
                    "color": "#1a5276" if is_hl else "#4a235a" if is_q else "#1a252f",
                    "backgroundColor": "#dce6f0" if is_hl else "#e8daf0" if is_q else "#e8edf2",
                }
            else:
                # Regular data rows (including sub-country indented rows)
                is_sub = label.startswith("  ")
                bg = ("#e4effa" if is_hl and val_str else
                      "#f0e6f6" if is_q and val_str else zebra_bg)
                style = {
                    "padding": "6px 10px",
                    "fontSize": "12px" if not is_sub else "11px",
                    "borderBottom": "1px solid #ddd",
                    "borderLeft": bl, "borderRight": br,
                    "textAlign": "right" if i > 0 else "left",
                    "fontWeight": "700" if is_hl and val_str else
                                  "600" if is_q and val_str else
                                  "600" if i == 0 and not is_sub else "normal",
                    "color": "#1a5276" if is_hl and val_str else
                             "#4a235a" if is_q and val_str else
                             "#666" if is_sub else "#333",
                    "backgroundColor": bg,
                }

            cells.append(html.Td(val_str, style=style))

        rows.append(html.Tr(cells))
        row_idx += 1

        # Inject BMD row after End Stock
        if is_end_stock and bmd_prices:
            month_names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
            bmd_cells = []
            for i, c in enumerate(cols):
                base = {
                    "padding": "7px 10px", "fontSize": "12px",
                    "borderTop": "2px solid #8e44ad",
                    "borderBottom": "2px solid #8e44ad",
                    "borderLeft": _col_border_left(i),
                    "borderRight": _col_border_right(i),
                    "backgroundColor": "#f5eef8",
                }
                if i == 0:
                    bmd_cells.append(html.Td("BMD (FCPO)", style={
                        **base, "fontWeight": "700", "color": "#8e44ad",
                        "textAlign": "left",
                    }))
                elif c in month_names:
                    month_num = month_names.index(c) + 1
                    price = bmd_prices.get(month_num)
                    val_str = f"{price:,.0f}" if price else ""
                    bmd_cells.append(html.Td(val_str, style={
                        **base, "textAlign": "right",
                        "fontWeight": "700" if val_str else "normal",
                        "color": "#8e44ad" if val_str else "#ccc",
                    }))
                else:
                    bmd_cells.append(html.Td("", style=base))
            rows.append(html.Tr(bmd_cells))
            if bmd_as_of:
                rows.append(html.Tr([html.Td(
                    f"BMD settlement prices as of {bmd_as_of}",
                    colSpan=len(cols),
                    style={"padding": "3px 14px", "fontSize": "10px",
                           "color": "#8e44ad", "fontStyle": "italic",
                           "borderBottom": "none"},
                )]))
            row_idx += 1

    table = html.Table(
        [html.Thead(header), html.Tbody(rows)],
        style={
            "width": "100%",
            "borderCollapse": "collapse",
            "backgroundColor": "white",
            "fontFamily": "'Segoe UI', Tahoma, Geneva, Verdana, sans-serif",
            "borderSpacing": "0",
            "border": "1px solid #ccc",
        },
    )

    return html.Div([
        html.H3(f"{title} — Supply & Demand Balance Sheet", style={
            **_SUBHEADER_STYLE, "marginTop": "8px", "marginBottom": "4px",
        }),
        html.P("All figures in '000 Metric Tonnes", style={
            "fontSize": "11px", "color": "#888", "marginBottom": "16px",
            "fontStyle": "italic",
        }),
        html.Div(table, style={
            "overflowX": "auto",
            "borderRadius": "10px",
            "boxShadow": "0 2px 12px rgba(0,0,0,0.06)",
            "backgroundColor": "white",
            "padding": "20px 24px",
            "border": "1px solid #e8e8e8",
        }),
    ])


# ── AI tab callbacks ─────────────────────────────────────────────────────────

@app.callback(
    Output("ai-instrument", "options"),
    Input("section-tabs", "value"),
    prevent_initial_call=True,
)
def _ai_populate_instruments(tab: str):
    if tab != "AI":
        raise PreventUpdate
    from ai_analysis import build_instrument_options
    return build_instrument_options()


@app.callback(
    Output("ai-narrative",  "children"),
    Output("ai-raw-stats",  "children"),
    Output("ai-status",     "children"),
    Output("ai-run-btn",    "disabled"),
    Input("ai-run-btn",     "n_clicks"),
    Input("ai-instrument",  "value"),
    Input("ai-timeframe",   "value"),
    prevent_initial_call=True,
)
def _ai_run_analysis(n_clicks, instrument, timeframe):
    from dash import ctx
    if ctx.triggered_id != "ai-run-btn" or not n_clicks:
        raise PreventUpdate
    if not instrument:
        return "", "", "Please select an instrument.", False

    from ai_analysis import run_analysis

    result = run_analysis(instrument, timeframe)

    if result.get("error"):
        return result["error"], "", result["error"], False

    raw_text = result.get("raw_text", "")
    narrative = result.get("narrative")
    figures = result.get("figures", {})

    # Build chart grid
    chart_names = {
        "seasonal": "1. Seasonal Pattern",
        "extremes": "2. Extreme Values",
        "highs_lows": "3. Highs & Lows",
        "oi": "4. OI Buildup",
        "support_resistance": "5. Support & Resistance",
        "year_resemblance": "6. Year Resemblance",
        "momentum": "7. Momentum & Trend",
    }

    chart_items = []
    for key, title in chart_names.items():
        fig = figures.get(key)
        if fig is None:
            # OI not available — show placeholder
            if key == "oi":
                chart_items.append(html.Div(
                    "OI data not available for this instrument.",
                    style={"padding": "40px 20px", "color": "#aaa", "textAlign": "center",
                           "border": "1px dashed #ccc", "borderRadius": "8px",
                           "backgroundColor": "white"},
                ))
            continue
        chart_items.append(dcc.Graph(
            figure=fig,
            config={"displayModeBar": False, "scrollZoom": True},
            style={"borderRadius": "8px", "backgroundColor": "white",
                   "boxShadow": "0 1px 4px rgba(0,0,0,0.08)"},
        ))

    # Arrange charts in a grid
    charts_div = html.Div(chart_items, style={
        "display": "grid",
        "gridTemplateColumns": "repeat(2, 1fr)",
        "gap": "16px",
        "marginBottom": "24px",
    })

    # Gemini narrative section (collapsible, below charts)
    if narrative:
        narrative_section = html.Details([
            html.Summary("AI Narrative Report", style={
                "cursor": "pointer", "fontWeight": "700", "fontSize": "13px",
                "color": "#1a1a2e", "padding": "10px 0",
            }),
            dcc.Markdown(narrative, style={
                "fontSize": "13px", "lineHeight": "1.6",
                "padding": "16px", "backgroundColor": "#f8f9fa",
                "borderRadius": "6px", "border": "1px solid #e0e0e0",
                "maxHeight": "50vh", "overflowY": "auto",
            }),
        ], open=True, style={"marginTop": "8px"})
    else:
        narrative_section = html.Div(
            "AI narration unavailable — Gemini API key not configured or quota exceeded.",
            style={"color": "#888", "fontSize": "12px", "fontStyle": "italic", "marginTop": "8px"},
        )

    display = html.Div([charts_div, narrative_section])
    status = "Analysis complete."

    return display, raw_text, status, False


# ── News tab callbacks ────────────────────────────────────────────────────────

@app.callback(
    Output("news-content", "children"),
    Input("section-tabs", "value"),
)
def _init_news_tab(tab: str):
    if tab != "News":
        raise PreventUpdate
    from news_dashboard import build_news_layout
    return build_news_layout()


@app.callback(
    Output("calendar-content", "children"),
    Input("section-tabs", "value"),
)
def _init_calendar_tab(tab: str):
    if tab != "Calendar":
        raise PreventUpdate
    from calendar_data import build_calendar_layout
    return build_calendar_layout(CALENDAR_DF)


@app.callback(
    Output("cftc-content", "children"),
    Input("section-tabs", "value"),
)
def _init_cftc_tab(tab: str):
    if tab != "CFTC":
        raise PreventUpdate
    return build_cftc_layout(CFTC_DF)


@app.callback(
    Output("news-panel", "children"),
    Input("news-geography", "value"),
    Input("news-product", "value"),
    prevent_initial_call=True,
)
def _update_news_panel(geography: str, product: str):
    if not geography or not product:
        raise PreventUpdate
    from news_dashboard import build_news_panel
    return build_news_panel(geography, product)


# ── Hedge Ratios tab (under Cash Premium/Basis) ──────────────────────────────

_HR_SNAPSHOT_LABELS = ["Now", "-1M", "-3M", "-6M", "-1Y"]
_HR_WINDOWS         = [30, 60]

_HR_NAV_BG     = "#1F3864"
_HR_SUBHDR_BG  = "#2E75B6"
_HR_ALT_BG     = "#D9E1F2"
_HR_SECTION_BG = "#808080"

# Heat-map end/mid stops (Excel-default 3-colour scale)
_HR_HEAT_LOW  = (248, 105, 107)  # #F8696B — red
_HR_HEAT_MID  = (255, 235, 132)  # #FFEB84 — yellow
_HR_HEAT_HIGH = (99,  190, 123)  # #63BE7B — green


def _lerp(a: tuple[int, int, int], b: tuple[int, int, int],
          t: float) -> str:
    t = max(0.0, min(1.0, t))
    r = round(a[0] + (b[0] - a[0]) * t)
    g = round(a[1] + (b[1] - a[1]) * t)
    bl = round(a[2] + (b[2] - a[2]) * t)
    return f"#{r:02x}{g:02x}{bl:02x}"


def _hr_heat_color(value: float, vmin: float, vmid: float,
                   vmax: float) -> str:
    if pd.isna(value):
        return "#FFFFFF"
    if vmax <= vmin:
        return _lerp(_HR_HEAT_LOW, _HR_HEAT_HIGH, 0.5)
    if value <= vmid:
        span = max(vmid - vmin, 1e-9)
        return _lerp(_HR_HEAT_LOW, _HR_HEAT_MID, (value - vmin) / span)
    span = max(vmax - vmid, 1e-9)
    return _lerp(_HR_HEAT_MID, _HR_HEAT_HIGH, (value - vmid) / span)


def _hr_cell(value, *, fill="#FFFFFF", color="#222", align="right",
             border_left="1px solid #e6e6e6"):
    text = "" if value is None or (isinstance(value, float) and pd.isna(value)) \
             else (f"{value:.4f}" if isinstance(value, (int, float)) else str(value))
    return html.Td(text, style={
        "padding":         "8px 14px",
        "backgroundColor": fill,
        "color":           color,
        "textAlign":       align,
        "fontSize":        "16px",
        "fontWeight":      "500",
        "borderLeft":      border_left,
        "borderBottom":    "1px solid #eee",
        "whiteSpace":      "nowrap",
    })


def _hr_header_rows() -> list:
    n = len(_HR_SNAPSHOT_LABELS)
    top = [html.Th("RIC", rowSpan=2, style={
        "padding": "10px 14px", "backgroundColor": _HR_NAV_BG,
        "color": "#fff", "fontWeight": "700", "fontSize": "14px",
        "textAlign": "left", "letterSpacing": "0.05em",
    })]
    for w in _HR_WINDOWS:
        top.append(html.Th(f"{w}d Beta", colSpan=n, style={
            "padding": "10px 14px", "backgroundColor": _HR_NAV_BG,
            "color": "#fff", "fontWeight": "700", "fontSize": "14px",
            "textAlign": "center", "letterSpacing": "0.05em",
            "borderLeft": "2px solid #fff",
        }))
    sub = []
    for _w in _HR_WINDOWS:
        for li, label in enumerate(_HR_SNAPSHOT_LABELS):
            sub.append(html.Th(label, style={
                "padding": "8px 12px", "backgroundColor": _HR_SUBHDR_BG,
                "color": "#fff", "fontWeight": "700", "fontSize": "13px",
                "textAlign": "center",
                "borderLeft": "2px solid #fff" if li == 0 else "1px solid #fff",
                "minWidth": "78px",
            }))
    return [html.Tr(top), html.Tr(sub)]


def _hr_beta_cols() -> list[str]:
    return [f"B{w}_{l}" for w in _HR_WINDOWS for l in _HR_SNAPSHOT_LABELS]


def _hr_row_values(df: pd.DataFrame, ric: str) -> list[float]:
    return [df.at[ric, c] if (ric in df.index and c in df.columns) else np.nan
            for c in _hr_beta_cols()]


def _hr_heat_bounds(*frames: pd.DataFrame | None) -> tuple[float, float, float]:
    vals = []
    for df in frames:
        if df is None or df.empty:
            continue
        cols = [c for c in _hr_beta_cols() if c in df.columns]
        if not cols:
            continue
        arr = df[cols].to_numpy(dtype=float)
        vals.extend(arr[~np.isnan(arr)].tolist())
    if not vals:
        return 0.0, 0.0, 0.0
    arr = np.asarray(vals)
    return float(arr.min()), float(np.median(arr)), float(arr.max())


def _hr_data_row(ric: str, values: list[float], fills: list[str],
                 base_fill: str) -> html.Tr:
    cells = [html.Td(ric, style={
        "padding": "8px 14px", "backgroundColor": base_fill,
        "fontWeight": "700", "fontSize": "16px", "textAlign": "left",
        "borderBottom": "1px solid #eee", "whiteSpace": "nowrap",
    })]
    for i, (val, fill) in enumerate(zip(values, fills)):
        bl = "2px solid #ddd" if i % len(_HR_SNAPSHOT_LABELS) == 0 else "1px solid #eee"
        cells.append(_hr_cell(val, fill=fill, border_left=bl))
    return html.Tr(cells)


def _hr_section_row(text: str, n_cols: int) -> html.Tr:
    return html.Tr([html.Td(text, colSpan=n_cols + 1, style={
        "padding": "10px 14px", "backgroundColor": _HR_SECTION_BG,
        "color": "#fff", "fontWeight": "700", "fontSize": "14px",
        "textAlign": "center", "letterSpacing": "0.1em",
        "textTransform": "uppercase",
    })])


def _render_hedge_table(snap: pd.DataFrame,
                        waste_snaps: pd.DataFrame | None) -> html.Div:
    products = hedging_data.PRODUCTS
    n_data   = len(_HR_WINDOWS) * len(_HR_SNAPSHOT_LABELS)
    vmin, vmid, vmax = _hr_heat_bounds(snap, waste_snaps)

    def _fills(values: list[float]) -> list[str]:
        return [_hr_heat_color(v, vmin, vmid, vmax) for v in values]

    rows: list = list(_hr_header_rows())

    for gi, (_prod, info) in enumerate(products.items()):
        base_fill = _HR_ALT_BG if gi % 2 == 0 else "#FFFFFF"
        for ric in (info["M1"], info["M2"]):
            if ric not in snap.index:
                continue
            vals = _hr_row_values(snap, ric)
            rows.append(_hr_data_row(ric, vals, _fills(vals), base_fill))

    if waste_snaps is not None and not waste_snaps.empty:
        rows.append(_hr_section_row("Waste", n_data))
        for ri, ric in enumerate(waste_snaps.index):
            base = _HR_ALT_BG if ri % 2 == 0 else "#FFFFFF"
            vals = _hr_row_values(waste_snaps, ric)
            rows.append(_hr_data_row(ric, vals, _fills(vals), base))

    table = html.Table(rows, style={
        "borderCollapse": "collapse",
        "fontFamily":     "Calibri, Arial, sans-serif",
        "backgroundColor": "#fff",
        "boxShadow":       "0 1px 4px rgba(0,0,0,0.08)",
        "borderRadius":    "6px",
        "overflow":        "hidden",
    })
    return html.Div([
        html.H3("Snapshot Hedge Ratios", style={**_SUBHEADER_STYLE, "marginTop": "0"}),
        html.Div(table, style={"overflowX": "auto"}),
    ])


# ── Rolling graphs ────────────────────────────────────────────────────────────

_HR_LINE_COLORS = {"MY": "#1F3864", "ID": "#C0504D"}


def _hedge_beta_figure(display_name: str, info: dict[str, str | None],
                       prices: pd.DataFrame, tenor: str, window: int,
                       lookback_bdays: int) -> go.Figure:
    fig = go.Figure()
    end_date = None
    for region, base in info.items():
        if base is None:
            continue
        ric = f"{base}-{tenor}"
        series = hedging_data.rolling_beta_series(prices, ric, window).dropna()
        if series.empty:
            continue
        if end_date is None or series.index[-1] > end_date:
            end_date = series.index[-1]
        fig.add_trace(go.Scatter(
            x=series.index, y=series.values,
            mode="lines", name=f"{region} ({ric})",
            line=dict(color=_HR_LINE_COLORS[region], width=1.8),
            hovertemplate=f"<b>{region}</b><br>%{{x|%Y-%m-%d}}<br>β = %{{y:.4f}}<extra></extra>",
        ))

    title = f"{display_name} — {window}d Rolling β vs FCPOc3  ({tenor})"
    layout = dict(
        title=dict(text=title, font=dict(size=13)),
        xaxis=dict(showgrid=True, gridcolor="#e8e8e8"),
        yaxis=dict(title="β", showgrid=True, gridcolor="#e8e8e8",
                   zeroline=False),
        legend=dict(orientation="h", x=0, y=1.08, font=dict(size=10)),
        margin=dict(l=60, r=30, t=50, b=40),
        plot_bgcolor="white", paper_bgcolor="white",
        hovermode="x unified", height=320,
    )
    if lookback_bdays and end_date is not None:
        start = end_date - pd.tseries.offsets.BusinessDay(lookback_bdays)
        layout["xaxis"]["range"] = [start, end_date]
    fig.update_layout(**layout)
    # Reference line at β=1
    fig.add_hline(y=1.0, line=dict(color="#888", width=1, dash="dot"))
    return fig


@app.callback(
    Output("hedge-tabular-pane", "style"),
    Output("hedge-graphs-pane",  "style"),
    Input("hedge-sub-tabs", "value"),
)
def _toggle_hedge_sub_tabs(sub_tab: str):
    if sub_tab == "hr-graphs":
        return {"display": "none"}, {"display": "block"}
    return {"display": "block"}, {"display": "none"}


@app.callback(
    Output("hedge-table-wrapper",  "children"),
    Output("hedge-graphs-wrapper", "children"),
    Input("section-tabs",      "value"),
    Input("cashprem-sub-tabs", "value"),
    Input("hedge-products",    "value"),
    Input("hedge-tenor",       "value"),
    Input("hedge-window",      "value"),
    Input("hedge-lookback",    "value"),
)
def _update_hedge_ratios(section, sub_tab, products, tenor, window, lookback):
    if section != "Basis/Hedge Ratios" or sub_tab != "cp-ratios":
        raise PreventUpdate
    try:
        snap, waste_snaps, prices = hedging_data.load_snapshots()
    except FileNotFoundError as e:
        err = html.P(str(e), style={"color": "#c0392b"})
        return err, None
    except Exception as e:
        err = html.P(f"Hedge-ratio load failed: {e}", style={"color": "#c0392b"})
        return err, None

    table = _render_hedge_table(snap, waste_snaps)

    if not products:
        graphs = html.P("Select one or more products to view rolling betas.",
                        style={"color": "#888", "marginTop": "12px"})
        return table, graphs

    charts = []
    for name in products:
        info = hedging_data.HEDGE_PRODUCTS.get(name)
        if info is None:
            continue
        fig = _hedge_beta_figure(name, info, prices, tenor, int(window),
                                 int(lookback))
        charts.append(dcc.Graph(
            figure=fig, config={"displayModeBar": False},
            style={"borderRadius": "8px", "backgroundColor": "white",
                   "boxShadow": "0 1px 4px rgba(0,0,0,0.08)",
                   "marginTop": "12px"},
        ))
    graphs = html.Div([
        html.H3("Rolling Hedge Ratios", style={**_SUBHEADER_STYLE, "marginTop": "16px"}),
        html.Div(charts),
    ])
    return table, graphs


if __name__ == "__main__":
    print("\nStarting Palm Oil Spreads Dashboard...")
    print("Access on this machine : http://localhost:8055")
    print("Access on local network: http://<your-ip>:8055\n")
    app.run(host="0.0.0.0", port=8055, debug=False)