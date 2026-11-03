"""How good is a value? Maps each metric to a 0 (bad) … 1 (good) score.

Two families of rules:

- **fixed targets** where a universal reference exists (sleep score, bedtime
  around 23:00, wake-up around 08:00, 7.5–8.5 h of sleep, 10k steps, SpO₂…);
- **personal baseline** where only your own history is meaningful (resting HR,
  HRV, VO₂max, pace…): the value is compared with your all-time median, and
  ±2 robust standard deviations span the whole red → green scale.

The score drives a red → amber → green color; the hover always shows the value
and a word (poor / fair / good), so color is never the only cue.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

# red → orange → amber → yellow-green → green
SCALE = [
    [0.00, "#d03b3b"],
    [0.25, "#ec835a"],
    [0.50, "#f0a91c"],
    [0.75, "#8db92e"],
    [1.00, "#0ca30c"],
]


@dataclass(frozen=True)
class Rule:
    """kind:
    - "higher": worse at `a`, best at `b` (linear in between)
    - "target": best within `ok` of `a`, worst at distance `b`
    - "base_higher" / "base_lower": vs personal median, higher / lower is better
    - "base_near": vs personal median, closer is better
    """

    kind: str
    a: float = 0.0
    b: float = 0.0
    ok: float = 0.0
    note: str = ""


def higher(worst, best, note):
    return Rule("higher", worst, best, note=note)


def target(ideal, ok, zero_at, note):
    return Rule("target", ideal, zero_at, ok, note)


BASE_HIGHER = Rule("base_higher", note="higher than your usual is better")
BASE_LOWER = Rule("base_lower", note="lower than your usual is better")
BASE_NEAR = Rule("base_near", note="closer to your usual is better")

RULES: dict[str, Rule] = {
    # sleep
    "score": higher(50, 90, "50 → 90"),
    "total_min": target(480, 30, 150, "7.5–8.5 h is best"),
    "bed_min": target(-60, 15, 150, "closer to 23:00 is better"),
    "end_min": target(480, 15, 150, "closer to 08:00 is better"),
    "wakes": higher(10, 0, "fewer is better"),
    "sleep_hrv": BASE_HIGHER,
    "sleep_rhr": BASE_LOWER,
    "resp_rate": BASE_NEAR,
    "spo2_night": higher(92, 98, "92% → 98%"),
    # vitals
    "resting_hr": BASE_LOWER,
    "steps": higher(3000, 10000, "3k → 10k"),
    "active_minutes": higher(0, 60, "0 → 60 min"),
    "vo2max": BASE_HIGHER,
    "pai_total": higher(0, 100, "0 → 100 (PAI target)"),
    # activities (per kind)
    "run_pace": BASE_LOWER,
    "hike_pace": BASE_LOWER,
    "hike_climb": BASE_LOWER,
    "ride_speed": BASE_HIGHER,
}


def _robust_spread(base: pd.Series) -> tuple[float, float]:
    base = base.dropna()
    med = float(base.median())
    mad = float((base - med).abs().median()) * 1.4826
    spread = mad or float(base.std(ddof=0) or 0) or max(abs(med) * 0.05, 1e-9)
    return med, spread


def score(rule: Rule | None, values: pd.Series, baseline: pd.Series | None = None) -> pd.Series:
    """0..1 goodness per value (NaN where unknown or no rule)."""
    v = pd.to_numeric(values, errors="coerce").astype(float)
    if rule is None:
        return pd.Series(np.nan, index=v.index)
    if rule.kind == "higher":
        s = (v - rule.a) / (rule.b - rule.a)
    elif rule.kind == "target":
        dist = (v - rule.a).abs()
        s = 1 - (dist - rule.ok) / (rule.b - rule.ok)
    else:
        base = values if baseline is None else baseline
        if pd.to_numeric(base, errors="coerce").dropna().shape[0] < 3:
            return pd.Series(np.nan, index=v.index)  # not enough history for a baseline
        med, spread = _robust_spread(pd.to_numeric(base, errors="coerce"))
        z = (v - med) / spread
        if rule.kind == "base_higher":
            s = 0.5 + z / 4
        elif rule.kind == "base_lower":
            s = 0.5 - z / 4
        else:  # base_near
            s = 1 - z.abs() / 2.5
    return s.clip(0, 1)


def label(s: float) -> str:
    if s != s:  # NaN
        return ""
    return "poor" if s < 1 / 3 else "fair" if s < 2 / 3 else "good"


def describe(key: str) -> str:
    rule = RULES.get(key)
    return rule.note if rule else ""
