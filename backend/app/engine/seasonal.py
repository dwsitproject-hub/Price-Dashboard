"""Seasonal-overlay shaping: one trace per year, x mapped onto the leap year 2000 (legacy make_chart)."""
from __future__ import annotations

from datetime import date

import pandas as pd


def seasonal_traces(points: pd.DataFrame, years: list[int], column: str = "value") -> list[dict]:
    """points: index = obs_date (date), columns value/value_local → [{year, x[], y[]}] (ascending years)."""
    if points.empty:
        return []
    idx = pd.to_datetime(points.index)
    traces = []
    for year in sorted(set(years)):
        mask = idx.year == year
        if not mask.any():
            continue
        sub = points.loc[mask, column]
        sub_idx = idx[mask]
        valid = sub.notna().to_numpy()
        if not valid.any():
            continue
        xs = [f"2000-{d.month:02d}-{d.day:02d}" for d, ok in zip(sub_idx, valid) if ok]
        ys = [round(float(v), 6) for v in sub.to_numpy()[valid]]
        traces.append({"year": int(year), "x": xs, "y": ys})
    return traces


def default_years(available: list[int], n: int = 3) -> list[int]:
    return sorted(available)[-n:] if available else [date.today().year]
