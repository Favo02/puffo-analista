"""Unit conversion and formatting (European units, 24h clock)."""

from __future__ import annotations

import math


def _ok(x) -> bool:
    return x is not None and not (isinstance(x, float) and math.isnan(x))


def duration(seconds) -> str:
    """1:05:00 above one hour, 33:48 below."""
    if not _ok(seconds):
        return "–"
    s = int(round(seconds))
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def hours_minutes(minutes) -> str:
    """567 -> 9h 27m"""
    if not _ok(minutes):
        return "–"
    h, m = divmod(int(round(minutes)), 60)
    return f"{h}h {m:02d}m"


def pace(sec_per_km) -> str:
    if not _ok(sec_per_km) or sec_per_km <= 0:
        return "–"
    m, s = divmod(int(round(sec_per_km)), 60)
    return f"{m}:{s:02d} /km"


def speed(kmh) -> str:
    return "–" if not _ok(kmh) else f"{kmh:.1f} km/h"


def elev_pace(min_per_100m) -> str:
    return "–" if not _ok(min_per_100m) else f"{min_per_100m:.1f} min/100m"


def clock(minutes) -> str:
    """Minutes from midnight (may be negative or >= 1440) -> HH:MM."""
    if not _ok(minutes):
        return "–"
    m = int(round(minutes)) % 1440
    return f"{m // 60:02d}:{m % 60:02d}"


def num(x, digits=0, unit="") -> str:
    if not _ok(x):
        return "–"
    s = f"{x:,.{digits}f}".replace(",", " ")  # thin-space thousands separator
    return f"{s} {unit}".strip()


def speed_label(activity_type: str) -> str:
    """Which speed metric to show for an activity type."""
    return "speed" if activity_type == "Ride" else "pace"


def activity_speed(row) -> str:
    """Speed/pace string according to the activity type."""
    t = row["type"]
    if not _ok(row.get("distance_km")) or not row.get("distance_km"):
        return "–"
    if t == "Ride":
        return speed(row["speed_kmh"])
    out = pace(row["pace_s_km"])
    if t == "Hike" and _ok(row.get("elev_pace")):
        out += f" · {elev_pace(row['elev_pace'])}"
    return out
