"""Turn parsed records into pandas DataFrames with derived columns."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .activities import BY_KEY
from .food import assign_days
from .parse import Dataset, night_after_day

COLUMNS = {
    "vitals": ["date", "steps", "resting_hr", "active_minutes", "training_load", "pai_total", "vo2max"],
    "hr_buckets": ["date", "start_min", "end_min", "label", "avg_hr", "min_hr", "max_hr",
                   "avg_hrv", "min_hrv", "max_hrv"],
    "sleep": ["date", "start_min", "end_min", "score", "total_min", "deep", "light", "rem",
              "awake", "wakes", "resp_rate", "sleep_hrv", "sleep_rhr", "spo2_night"],
    "activities": ["id", "date", "type", "kind", "name", "duration_s", "distance_km", "elevation_m",
                   "avg_cadence", "relative_effort", "avg_hr", "min_hr", "max_hr"],
    "splits": ["activity_id", "n", "time_s", "avg_hr", "elev_diff"],
    "hr_chunks": ["activity_id", "n", "start_s", "end_s", "avg_hr", "min_hr", "max_hr"],
    "meals": ["date", "day_idx", "time", "type", "text"],
    "medicines": ["date", "day_idx", "time", "text"],
}


@dataclass
class Frames:
    vitals: pd.DataFrame
    hr_buckets: pd.DataFrame
    sleep: pd.DataFrame
    activities: pd.DataFrame
    splits: pd.DataFrame
    hr_chunks: pd.DataFrame
    meals: pd.DataFrame
    medicines: pd.DataFrame

    def filter(self, start, end) -> "Frames":
        """Restrict every table to dates in [start, end] (inclusive)."""
        def by_date(df, col="date"):
            return df[(df[col] >= pd.Timestamp(start)) & (df[col] <= pd.Timestamp(end))]

        acts = by_date(self.activities)
        ids = set(acts["id"])
        return Frames(
            vitals=by_date(self.vitals),
            hr_buckets=by_date(self.hr_buckets),
            sleep=by_date(self.sleep),
            activities=acts,
            splits=self.splits[self.splits["activity_id"].isin(ids)],
            hr_chunks=self.hr_chunks[self.hr_chunks["activity_id"].isin(ids)],
            meals=by_date(self.meals, "day"),
            medicines=by_date(self.medicines),
        )

    def all_dates(self) -> pd.Series:
        return pd.concat(
            [*(df["date"] for df in (self.vitals, self.sleep, self.activities, self.meals, self.medicines)),
             self.meals["day"]]  # a food day can start the day before its first meal's date
        ).dropna()


def _df(ds: Dataset, name: str) -> pd.DataFrame:
    df = pd.DataFrame(ds.tables[name], columns=COLUMNS[name])
    if "date" in df:
        df["date"] = pd.to_datetime(df["date"])
    return df


def build(ds: Dataset) -> Frames:
    vitals = _df(ds, "vitals").sort_values("date")
    buckets = _df(ds, "hr_buckets").sort_values(["date", "start_min"])

    sleep = _df(ds, "sleep")
    # A date can hold several sleep entries (asleep at 00:30 and again at 23:30),
    # so nightly charts are keyed by the moment you fell asleep, not the date.
    sleep["asleep_at"] = sleep["date"] + pd.to_timedelta(sleep["start_min"].fillna(0), unit="min")
    sleep = sleep.sort_values("asleep_at", kind="stable").reset_index(drop=True)
    # Bedtime on a continuous axis around midnight: 23:30 -> -30, 00:45 -> 45.
    sleep["bed_min"] = sleep["start_min"].where(sleep["start_min"] < 12 * 60, sleep["start_min"] - 1440)
    sleep["in_bed_min"] = (sleep["end_min"] - sleep["start_min"]) % 1440
    sleep["night_after"] = [
        pd.Timestamp(night_after_day(d.date(), None if pd.isna(s) else s))
        for d, s in zip(sleep["date"], sleep["start_min"])
    ]

    acts = _df(ds, "activities").sort_values(["date", "id"])
    acts["kind_label"] = acts["kind"].map(lambda k: BY_KEY[k].label)
    # Pace/speed/climb rate only make sense for distance-based (endurance) kinds:
    # a soccer match's "pace" would be meaningless.
    endurance = acts["kind"].map(lambda k: BY_KEY[k].endurance).astype(bool)
    dist = acts["distance_km"].where((acts["distance_km"] > 0) & endurance)
    acts["pace_s_km"] = acts["duration_s"] / dist
    acts["speed_kmh"] = dist / (acts["duration_s"] / 3600)
    elev = acts["elevation_m"].where((acts["elevation_m"] > 0) & endurance)
    acts["elev_pace"] = (acts["duration_s"] / 60) / (elev / 100)
    acts["week"] = acts["date"] - pd.to_timedelta(acts["date"].dt.day_of_week, unit="D")
    acts["label"] = (
        acts["date"].dt.strftime("%Y-%m-%d") + " · " + acts["type"] + " · " + acts["name"]
    )

    splits = _df(ds, "splits").sort_values(["activity_id", "n"])
    # The last split is usually a partial km: its length is what remains of the distance.
    total = splits["activity_id"].map(acts.set_index("id")["distance_km"])
    last_n = splits.groupby("activity_id")["n"].transform("max")
    remainder = total - (last_n - 1)
    splits["km"] = 1.0
    partial = (splits["n"] == last_n) & remainder.between(0.05, 0.999)
    splits.loc[partial, "km"] = remainder[partial].round(2)
    splits["pace_s_km"] = splits["time_s"] / splits["km"]
    splits["speed_kmh"] = splits["km"] / (splits["time_s"] / 3600)

    chunks = _df(ds, "hr_chunks").sort_values(["activity_id", "n"])
    chunks["mid_min"] = (chunks["start_s"] + chunks["end_s"]) / 2 / 60

    # Meals belong to sleep-based days (a snack at 00:45 before bed counts for the day before).
    meals = assign_days(_df(ds, "meals"), sleep)
    return Frames(vitals, buckets, sleep, acts, splits, chunks, meals, _df(ds, "medicines"))
