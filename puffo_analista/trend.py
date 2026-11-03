"""Smoothed trend for noisy daily series (Gaussian kernel regression).

The bandwidth adapts to the visible time span, so a month shows a short-term
trend and a year shows the seasonal one. Where there is no data nearby (e.g. a
missing month) the trend is left empty instead of bridging the gap.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

MIN_POINTS = 5
DAY = pd.Timedelta(days=1)


def bandwidth_days(span_days: float) -> float:
    """Kernel standard deviation in days: ~1/30 of the span, between 2.5 and 21."""
    return float(np.clip(span_days / 30, 2.5, 21))


def gap_days(span_days: float, spacing_days: float = 1.0) -> float:
    """Distance between consecutive data points beyond which the trend/line is broken.

    Scales with the bandwidth and with the series' own typical spacing, so sparse
    series (VO₂max, runs) are not chopped into pieces while a missing month in a
    daily series is.
    """
    return max(1.5 * bandwidth_days(span_days), 3 * spacing_days, 5.0)


def _spacing(t: np.ndarray) -> float:
    d = np.diff(np.sort(t))
    return float(np.median(d)) if len(d) else 1.0


def smooth(x: pd.Series, y: pd.Series) -> tuple[pd.DatetimeIndex, np.ndarray]:
    """Return (grid, smoothed values); empty if there are too few points."""
    ok = x.notna() & y.notna()
    xs, ys = pd.to_datetime(x[ok]), y[ok].astype(float).to_numpy()
    if len(xs) < MIN_POINTS:
        return pd.DatetimeIndex([]), np.array([])
    t0 = xs.min()
    t = ((xs - t0) / DAY).to_numpy(dtype=float)
    span = float(t.max() - t.min())
    if span < 2:
        return pd.DatetimeIndex([]), np.array([])
    h = bandwidth_days(span)
    step = 0.25 if span <= 45 else 1.0
    g = np.arange(t.min(), t.max() + step / 2, step)

    w = np.exp(-0.5 * ((g[:, None] - t[None, :]) / h) ** 2)
    est = (w @ ys) / w.sum(axis=1)

    # no trend inside a gap: between two consecutive observations too far apart
    order = np.sort(t)
    idx = np.clip(np.searchsorted(order, g), 1, len(order) - 1)
    in_gap = (order[idx] - order[idx - 1] > gap_days(span, _spacing(t))) & (g > order[idx - 1]) & (g < order[idx])
    est[in_gap] = np.nan

    return pd.DatetimeIndex(t0 + pd.to_timedelta(g, unit="D")), est


def break_gaps(df: pd.DataFrame, x: str, span_days: float) -> pd.DataFrame:
    """Insert an empty row inside long gaps so a connecting line is not drawn across them."""
    d = df.sort_values(x)
    if len(d) < 2:
        return d
    t = ((d[x] - d[x].min()) / DAY).to_numpy(dtype=float)
    gaps = d[x].diff() > pd.Timedelta(days=gap_days(span_days, _spacing(t)))
    if not gaps.any():
        return d
    fillers = d.loc[gaps, [x]].copy()
    fillers[x] = fillers[x] - pd.Timedelta(hours=1)  # NaN y just before the next point
    return pd.concat([d, fillers]).sort_values(x, kind="stable")
