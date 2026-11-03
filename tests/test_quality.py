import math

import pandas as pd
import pytest

from puffo_analista import quality as q
from puffo_analista.charts import quality_color


def s(rule_key, values, baseline=None):
    return q.score(q.RULES[rule_key], pd.Series(values, dtype=float), baseline).round(2).tolist()


def test_fixed_higher_is_better():
    assert s("score", [40, 50, 70, 90, 99]) == [0, 0, 0.5, 1, 1]
    assert s("wakes", [0, 5, 10, 15]) == [1, 0.5, 0, 0]


def test_target_bedtime_and_wakeup():
    # bedtime as minutes around midnight: 23:00 = -60
    assert s("bed_min", [-60, -50, -75, 90]) == [1, 1, 1, 0]
    assert s("end_min", [480, 600])[0] == 1 and s("end_min", [480, 600])[1] < 0.3


def test_baseline_rules_follow_personal_median():
    hist = pd.Series([48, 49, 50, 51, 52], dtype=float)
    lower = s("resting_hr", [46, 50, 54], hist)
    assert lower[0] > 0.5 > lower[2] and lower[1] == 0.5
    higher = s("sleep_hrv", [60, 80, 100], pd.Series([70, 75, 80, 85, 90], dtype=float))
    assert higher[0] < 0.5 < higher[2]
    near = s("resp_rate", [12.0, 14.0], pd.Series([11.8, 12.0, 12.2, 12.1, 11.9]))
    assert near[0] == 1 and near[1] == 0


def test_baseline_needs_history():
    assert all(math.isnan(x) for x in s("resting_hr", [50, 51], pd.Series([50.0, 51.0])))


def test_no_rule_and_nan_are_neutral():
    assert math.isnan(q.score(None, pd.Series([1.0]))[0])
    assert math.isnan(s("score", [float("nan")])[0])
    assert q.label(float("nan")) == ""


@pytest.mark.parametrize("v,word", [(0.1, "poor"), (0.5, "fair"), (0.9, "good")])
def test_labels(v, word):
    assert q.label(v) == word


def test_quality_color_endpoints():
    assert quality_color(0, "#000000") == q.SCALE[0][1]
    assert quality_color(1, "#000000") == q.SCALE[-1][1]
    assert quality_color(float("nan"), "#123456") == "#123456"
