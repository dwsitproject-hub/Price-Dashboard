"""Catalog entries for the Google Sheet series (no registry import: the registry imports this module)."""
from __future__ import annotations

from functools import lru_cache


@lru_cache(maxsize=1)
def sheet_specs() -> list[dict]:
    from app.sheets import argus, china, india, tradable
    specs = []
    for source, items in (("argus", argus.series_specs()), ("market_price", tradable.series_specs()),
                          ("india_parity", india.series_specs()), ("china_parity", china.series_specs())):
        for it in items:
            unit = it.get("unit") or "USD/MT"
            if len(unit) > 24:                       # market.series.unit is varchar(24)
                unit = "USD/t"
            specs.append({**it, "unit": unit, "source": source})
    return specs


def keys_for(source: str) -> list[str]:
    return [s["key"] for s in sheet_specs() if s["source"] == source]
