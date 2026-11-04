"""Technical analysis — K-Means support/resistance and SMAs (verbatim logic from legacy dashboard.py,
originally AutomatedTechnicalAnalysisPaperMarkets.py)."""
from __future__ import annotations

import warnings

import numpy as np
import pandas as pd
from kneed import KneeLocator
from sklearn.cluster import KMeans


def calculate_local_extrema(values: pd.Series, window_size: int = 30, step_size: int = 5):
    if len(values) < window_size:
        return np.array([]).reshape(-1, 1), np.array([]).reshape(-1, 1)
    mins, maxs = [], []
    for i in range(0, len(values) - window_size + 1, step_size):
        w = values.iloc[i:i + window_size]
        mins.append(w.min())
        maxs.append(w.max())
    return np.array(mins).reshape(-1, 1), np.array(maxs).reshape(-1, 1)


def find_optimal_clusters(data: np.ndarray, max_k: int = 6) -> int:
    if len(data) == 0:
        return 4
    max_k = min(max_k, len(data))
    wcss = []
    for k in range(1, max_k + 1):
        km = KMeans(n_clusters=k, random_state=0, n_init=10)
        km.fit(data)
        wcss.append(km.inertia_)
    if max_k < 3:
        return max_k
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        knee = KneeLocator(range(1, max_k + 1), wcss, curve="convex", direction="decreasing")
    return knee.elbow if knee.elbow else 3


def ranked_levels(values: pd.Series) -> list[float]:
    """Price series in → sorted S/R levels (cluster centres of rolling minima and maxima)."""
    values = values.dropna()
    lows, highs = calculate_local_extrema(values)
    if len(lows) == 0:
        return []
    k_min = find_optimal_clusters(lows)
    k_max = find_optimal_clusters(highs)
    km_min = KMeans(n_clusters=k_min, random_state=0, n_init=10).fit(lows)
    km_max = KMeans(n_clusters=k_max, random_state=0, n_init=10).fit(highs)
    levels = np.concatenate([km_min.cluster_centers_.flatten(), km_max.cluster_centers_.flatten()])
    return [float(x) for x in np.sort(levels)]


def smas(values: pd.Series) -> dict[str, pd.Series]:
    return {f"sma{w}": values.rolling(window=w).mean() for w in (50, 100, 200)}
