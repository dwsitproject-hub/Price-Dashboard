"""Read-side payloads for the sheet-backed views that are not plain seasonal grids: Waste/Bio forward curves,
YTD, Arb (MIP table + waterfalls), the Tickets calculation box, and the Ind/China parity snapshots.

Ports of the legacy dashboard.py renderers (make_argus_forward_curve_chart / _argus_fwd_diff_snapshot_rows,
make_argus_ytd_chart, _argus_arb_tile, _argus_china_ucome_arb_tile, _ticket_calc_block) — numbers only; the
React views do the layout.
"""
from __future__ import annotations

from datetime import date

import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session as DbSession

from app.services.market import _cached, _points, _series_xy, _snapshot_rows, data_version
from app.sheets import argus as A
from app.sheets import job

RME_PREMIUM_FRACTION = 0.25          # legacy dashboard._RME_PREMIUM_FRACTION


def _last(db: DbSession, key: str) -> float | None:
    v = db.execute(text("SELECT value FROM market.series_points WHERE series_key = :k ORDER BY obs_date DESC LIMIT 1"),
                   {"k": key}).scalar()
    return None if v is None else float(v)


def _wide(db: DbSession, keys: list[str], start: date | None) -> pd.DataFrame:
    """legacy _argus_fwd_diff_wide: one column per maturity (M1..M3), outer-joined, no fill."""
    cols = {}
    for i, k in enumerate(keys, start=1):
        pts = _points(db, k, start)
        if not pts.empty:
            cols[f"M{i}"] = pts["value"]
    if not cols:
        return pd.DataFrame()
    df = pd.DataFrame(cols).sort_index()
    df.index = pd.to_datetime(df.index)
    return df


def curves(db: DbSession, set_name: str, years: list[int], snapshot_md: str) -> list[dict]:
    years = sorted(set(years))

    def q():
        start = date(min(years), 1, 1) if years else None
        out = []
        for key, title, keys in A.CURVE_SETS[set_name]:
            curve = _wide(db, keys, start)
            out.append({"key": key, "title": title if set_name == "fwd_diff" else f"{title} Forward Curve",
                        "unit": "USD/MT", "traces": _snapshot_rows(curve, years, snapshot_md) if not curve.empty else []})
        return out
    return _cached(("argus_curves", set_name, tuple(years), snapshot_md, data_version(db)), q)


def ytd(db: DbSession, year: int) -> list[dict]:
    """Argus → Calendar Spreads → YTD: M1−M2, M1−M3 on the year's own dates, M1 price for context."""
    def q():
        out = []
        for code, label, keys in A.CURVE_SETS["calendar"]:
            item = {"key": code, "title": label, "unit": "USD/MT", "year": year, "legs": [], "front": None}
            curve = _wide(db, keys, date(year, 1, 1))
            if not curve.empty and "M1" in curve.columns:
                yr = curve.loc[curve.index.year == year]
                for leg in ("M2", "M3"):
                    if leg in yr.columns:
                        spread = (yr["M1"] - yr[leg]).dropna()
                        if not spread.empty:
                            item["legs"].append({"label": f"M1-{leg}", **_series_xy(spread)})
                front = yr["M1"].dropna()
                if not front.empty:
                    item["front"] = {"label": "M1 Price", **_series_xy(front)}
            out.append(item)
        return out
    return _cached(("argus_ytd", year, data_version(db)), q)


def _arb_status(diff: float) -> str:
    return "open" if diff > 0 else "closed"     # diff = FAME 0 (EUR/t) − SME landed value


def arb(db: DbSession) -> dict:
    def q():
        legs = A.lseg_legs(job.lseg_frames())
        this_year, this_q = A.current_quarter()
        quarters = [(y, qq) for y in (this_year - 1, this_year) for qq in (1, 2, 3, 4)]
        table = A.mip_table(legs, quarters)
        cur = next((r for r in table if r["year"] == this_year and r["quarter"] == this_q), None)
        for r in table:
            r["q_start"], r["q_end"] = r["q_start"].date().isoformat(), r["q_end"].date().isoformat()
        sme = None
        if cur is not None:
            eur_oil = cur["avg_sbo_usd"] / cur["eurusd"]
            eur_freight = A.ARB_FREIGHT_USD / cur["eurusd"]
            subtotal = eur_oil + A.ARB_CONVERSION_EUR + eur_freight
            duty = subtotal * A.ARB_IMPORT_DUTY
            landed = subtotal + duty
            live_eurusd = _last(db, "ARGUS_FX_EURUSD") or cur["eurusd"]
            rme_m1, fame0_m1 = _last(db, "ARGUS_OUT_RSO_OME_M1"), _last(db, "ARGUS_OUT_FAME0_M1")
            rme_diff = _last(db, "ARGUS_PS_RSO_OME_FAME0_M1")
            rme_prem = RME_PREMIUM_FRACTION * rme_diff / live_eurusd if rme_diff is not None else 0.0
            value = landed + rme_prem
            months = []
            for i, m in enumerate(pd.date_range(cur["q_start"], cur["q_end"], freq="MS"), start=1):
                fame_usd = _last(db, f"ARGUS_OUT_FAME0_M{i}")
                row = {"month": m.strftime("%b %Y"), "maturity": f"M{i}", "sme_landed_eur": value,
                       "fame0_usd": fame_usd, "fame0_eur": None, "diff_eur": None, "status": None}
                if fame_usd is not None:
                    fame_eur = fame_usd / live_eurusd
                    row.update(fame0_eur=fame_eur, diff_eur=fame_eur - value, status=_arb_status(fame_eur - value))
                months.append(row)
            sme = {"eur_oil": eur_oil, "eur_conversion": A.ARB_CONVERSION_EUR, "eur_freight": eur_freight,
                   "subtotal": subtotal, "duty": duty, "landed": landed, "live_eurusd": live_eurusd,
                   "rme_m1": rme_m1, "fame0_m1": fame0_m1, "rme_premium": rme_prem if rme_diff is not None else None,
                   "value": value, "months": months}
        china_fob, pome_fob = _last(db, "ARGUS_OUT_UCOME_CHINA"), _last(db, "ARGUS_OUT_POME_REFINED_MALAYSIA")
        china = None
        if china_fob is not None and pome_fob is not None:
            landed_cn = china_fob + A.CHINA_UCOME_FREIGHT_MY + A.CHINA_UCOME_TRUCKING_MY + A.CHINA_UCOME_FOBBING_MY
            pome_ref = pome_fob + A.POME_MY_NET_COSTS
            blended = A.CHINA_UCOME_WEIGHT * landed_cn + A.POME_MY_WEIGHT * pome_ref
            diff = blended - pome_ref
            china = {"china_fob": china_fob, "freight": A.CHINA_UCOME_FREIGHT_MY, "trucking": A.CHINA_UCOME_TRUCKING_MY,
                     "fobbing": A.CHINA_UCOME_FOBBING_MY, "china_landed": landed_cn,
                     "china_weight": A.CHINA_UCOME_WEIGHT, "china_weighted": A.CHINA_UCOME_WEIGHT * landed_cn,
                     "pome_fob": pome_fob, "net_costs": A.POME_MY_NET_COSTS, "pome_reference": pome_ref,
                     "pome_weight": A.POME_MY_WEIGHT, "pome_weighted": A.POME_MY_WEIGHT * pome_ref,
                     "blended": blended, "diff": diff, "status": "closed" if diff > 0 else "open"}
        return {"constants": {"conversion_eur": A.ARB_CONVERSION_EUR, "freight_usd": A.ARB_FREIGHT_USD,
                              "import_duty": A.ARB_IMPORT_DUTY, "rme_fraction": RME_PREMIUM_FRACTION,
                              "fame_maturity": A.ARB_FAME_MATURITY},
                "quarters": table, "current": None if cur is None else {**cur, "label": cur["label"]},
                "sme": sme, "china_ucome": china}
    return _cached(("argus_arb", date.today().isoformat(), data_version(db)), q)


def tickets_calc(db: DbSession) -> dict:
    """legacy _ticket_calc_block: assumptions + per-market formula and a worked UCOME example (latest values)."""
    def f(x, nd=0):
        return "n/a" if x is None else f"{x:,.{nd}f}"

    def q():
        gbp, eur = _last(db, "ARGUS_FX_GBPUSD"), _last(db, "ARGUS_FX_EURUSD")
        _u = A.BLEND_PRODUCTS["UCOME"]
        dens, mj, sav = _u[2], _u[3], _u[4]
        t_co2 = mj * A.FOSSIL_COMPARATOR_G_MJ * sav / 1000
        uk_p, de_p, nl_p = (_last(db, "ARGUS_TICKET_TKT_UK_OTHER"), _last(db, "ARGUS_TICKET_TKT_DE_CONVENTIONAL"),
                            _last(db, "ARGUS_TICKET_TKT_NL_LRE_C"))
        uk_ex = de_ex = nl_ex = "n/a"
        if None not in (uk_p, gbp):
            litres = 1000 / dens
            tkt, mar = _last(db, "ARGUS_BLENDTKT_UCOME_UK"), _last(db, "ARGUS_BLEND_UCOME_UK")
            if tkt is not None and mar is not None:
                uk_ex = (f"ticket: 2 RTFC/l × {uk_p:.2f}p = {f(tkt, 1)} p/l; premium: UCOME M1 − Gasoil M1 "
                         f"= ${f((tkt - mar) * gbp * litres / 100)}/t ÷ {gbp:.3f} ÷ {f(litres)} l/t = "
                         f"{f(tkt - mar, 1)} p/l; margin = {f(mar, 1)} p/l")
        if None not in (de_p, eur):
            de_eur = t_co2 * de_p
            de_ex = (f"{mj} MJ/kg × {A.FOSSIL_COMPARATOR_G_MJ:.0f} g/MJ × {sav} ÷ 1,000 = {t_co2:.2f} t CO2 saved per "
                     f"tonne of biofuel × €{f(de_p)} per t CO2 = €{f(de_eur)} per tonne of biofuel × {eur:.3f} = "
                     f"${f(de_eur * eur)} per tonne of biofuel")
        if None not in (nl_p, eur):
            nl_eur = t_co2 * nl_p * 10
            nl_ex = (f"{t_co2:.2f} t CO2 saved per tonne of biofuel × €{f(nl_p * 10)} per t CO2 ({nl_p:.1f} c/kg × 10) "
                     f"= €{f(nl_eur)} per tonne of biofuel (no national levy) × {eur:.3f} = ${f(nl_eur * eur)} per "
                     "tonne of biofuel")
        P = A.BLEND_PRODUCTS
        assumptions = (
            "Blend margin = ticket value − premium to gasoil, in the same unit (UK p/litre; DE/NL USD/t). Premium (all "
            "products) = product M1 − ICE Gasoil M1 (LGOc1): FAME 0 M1, UCOME M1, HVO M1. Positive = the ticket more "
            f"than pays for the premium over gasoil. Energy / GHG saving / density: FAME & UCOME {P['FAME'][3]} MJ/kg, "
            f"{P['FAME'][4]:.0%}, {P['FAME'][2]} kg/l; HVO {P['HVO'][3]} MJ/kg, {P['HVO'][4]:.0%}, {P['HVO'][2]} kg/l. "
            "Tickets are weekly/monthly snapshots, held until the next one. Worked example below = UCOME at latest "
            f"prices (GBPUSD {f(gbp, 3)}, EURUSD {f(eur, 3)}).")
        rows = [
            {"market": "UK", "ticket": "FAME → Crop RTFC (1 RTFC/l)\nUCOME, HVO → Other RTFC (2 RTFC/l, waste)",
             "formula": "in pence per LITRE of fuel: ticket = RTFC per litre × RTFC price (p); premium = (premium USD/t "
                        "÷ GBPUSD) ÷ (1,000 ÷ density kg/l) × 100; margin = ticket − premium", "example": uk_ex},
            {"market": "Germany", "ticket": "FAME, UCOME, HVO → Conventional GHG quota (non-advanced; UCO = 9B)",
             "formula": "t CO2 saved [MJ/kg × 94 g/MJ × GHG saving ÷ 1,000] × THG quota (€/t CO2) × EURUSD. "
                        "No nEHS carbon levy.", "example": de_ex},
            {"market": "Netherlands", "ticket": "FAME, UCOME, HVO → LRE-C (non-advanced; UCO = 9B)",
             "formula": "t CO2 saved [MJ/kg × 94 g/MJ × GHG saving ÷ 1,000] × LRE price (c/kg × 10 = €/t CO2) × "
                        "EURUSD. No national levy.", "example": nl_ex},
        ]
        return {"assumptions": assumptions, "rows": rows}
    return _cached(("argus_tickets", data_version(db)), q)


def parity_snapshots(db: DbSession) -> dict:
    rows = {r.key: r for r in db.execute(text(
        "SELECT key, payload, updated_at FROM intel.sheet_snapshots WHERE key IN ('parity_india','parity_china')"))}
    return {"india": rows["parity_india"].payload if "parity_india" in rows else [],
            "china": rows["parity_china"].payload if "parity_china" in rows else [],
            "india_updated_at": rows["parity_india"].updated_at if "parity_india" in rows else None,
            "china_updated_at": rows["parity_china"].updated_at if "parity_china" in rows else None}
