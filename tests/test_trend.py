import numpy as np
import pandas as pd

from puffo_analista import trend


def days(n, start="2026-01-01"):
    return pd.Series(pd.date_range(start, periods=n, freq="D"))


def test_smooth_reduces_noise_and_follows_level():
    rng = np.random.default_rng(0)
    x = days(120)
    y = pd.Series(50 + rng.normal(0, 3, 120))
    gx, gy = trend.smooth(x, y)
    assert len(gx) > 100
    assert np.nanstd(gy) < 1.5 < y.std()
    assert abs(np.nanmean(gy) - 50) < 1


def test_smooth_tracks_a_step_change():
    x = days(200)
    y = pd.Series([50.0] * 100 + [60.0] * 100)
    gx, gy = trend.smooth(x, y)
    assert gy[5] < 51 and gy[-5] > 59


def test_too_few_points_gives_no_trend():
    gx, gy = trend.smooth(days(4), pd.Series([1.0, 2, 3, 4]))
    assert len(gx) == 0 and len(gy) == 0


def test_gap_is_not_bridged():
    x = pd.concat([days(60, "2026-01-01"), days(60, "2026-05-01")], ignore_index=True)
    y = pd.Series(np.ones(120))
    gx, gy = trend.smooth(x, y)
    inside_gap = (gx > pd.Timestamp("2026-03-20")) & (gx < pd.Timestamp("2026-04-10"))
    assert inside_gap.any() and np.isnan(gy[inside_gap]).all()
    near_data = gx.normalize().isin(pd.DatetimeIndex(x))
    assert not np.isnan(gy[near_data]).any()


def test_sparse_series_is_not_chopped():
    x = pd.Series(pd.date_range("2026-01-01", periods=12, freq="14D"))  # e.g. VO₂max every 2 weeks
    gx, gy = trend.smooth(x, pd.Series(np.linspace(50, 55, 12)))
    assert len(gx) and not np.isnan(gy).any()


def test_break_gaps_inserts_empty_row():
    x = pd.concat([days(10), pd.Series(pd.to_datetime(["2026-03-01"]))], ignore_index=True)
    df = pd.DataFrame({"x": x, "y": np.arange(11, dtype=float)})
    out = trend.break_gaps(df, "x", span_days=60)
    assert len(out) == 12 and out["y"].isna().sum() == 1
    assert out["y"].isna().to_numpy().nonzero()[0][0] == 10  # just before the point after the gap
