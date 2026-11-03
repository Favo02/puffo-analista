"""Activity kinds: what to show for each sport.

Strava `sport_type` strings are mapped to a kind the same way puffo-coach does
(substring match), so variants like TrailRun, GravelRide or WeightTraining land
in the right kind.

Endurance kinds (run, ride, hike) are measured over distance: pace or speed,
elevation, cadence and per-km splits. Sport kinds (workout, soccer, padel,
tennis, volleyball) are measured over time: duration, heart rate (avg/min/max),
relative effort and HR over the session; distance is shown only as "distance
covered" when the watch recorded it, never as a pace.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Kind:
    key: str
    label: str
    slot: int | None  # categorical palette slot; None -> neutral gray
    endurance: bool = False  # distance-based: pace/speed, elevation, splits
    speed: str | None = None  # "pace" (min/km) | "speed" (km/h)
    elev_pace: bool = False  # min per 100 m climbed
    cadence: bool = False


RUN = Kind("run", "Run", 0, endurance=True, speed="pace", cadence=True)
RIDE = Kind("ride", "Ride", 1, endurance=True, speed="speed", cadence=True)
WORKOUT = Kind("workout", "Workout", 2)
HIKE = Kind("hike", "Hike", 3, endurance=True, speed="pace", elev_pace=True)
SOCCER = Kind("soccer", "Soccer", 4)
PADEL = Kind("padel", "Padel", 5)
TENNIS = Kind("tennis", "Tennis", 6)
VOLLEYBALL = Kind("volleyball", "Volleyball", 7)
OTHER = Kind("other", "Other", None)

# Fixed display order (= palette slot order).
KINDS: list[Kind] = [RUN, RIDE, WORKOUT, HIKE, SOCCER, PADEL, TENNIS, VOLLEYBALL, OTHER]
BY_KEY = {k.key: k for k in KINDS}
BY_LABEL = {k.label: k for k in KINDS}


def classify(sport_type: str | None) -> Kind:
    s = (sport_type or "").lower()
    if "volley" in s:
        return VOLLEYBALL
    if "soccer" in s or "football" in s:
        return SOCCER
    if "padel" in s:
        return PADEL
    if "tennis" in s and "table" not in s:
        return TENNIS
    if "workout" in s or "weight" in s or "crossfit" in s:
        return WORKOUT
    if "hike" in s:
        return HIKE
    if "run" in s:
        return RUN
    if "ride" in s:
        return RIDE
    return OTHER
