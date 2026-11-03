from pathlib import Path

import pytest

from puffo_analista import fmt
from puffo_analista.activities import BY_KEY, KINDS, OTHER, classify
from puffo_analista.frames import build
from puffo_analista.parse import load_folder


@pytest.mark.parametrize("sport_type,kind", [
    ("Run", "run"), ("TrailRun", "run"), ("VirtualRun", "run"),
    ("Ride", "ride"), ("GravelRide", "ride"), ("MountainBikeRide", "ride"),
    ("Hike", "hike"),
    ("Workout", "workout"), ("WeightTraining", "workout"), ("Crossfit", "workout"),
    ("Soccer", "soccer"), ("Padel", "padel"), ("Tennis", "tennis"),
    ("Volleyball", "volleyball"), ("BeachVolleyball", "volleyball"),
    ("TableTennis", "other"), ("Walk", "other"), ("", "other"),
])
def test_classify(sport_type, kind):
    assert classify(sport_type).key == kind


def test_eight_kinds_use_eight_distinct_slots():
    slots = [k.slot for k in KINDS if k is not OTHER]
    assert sorted(slots) == list(range(8))
    assert OTHER.slot is None


def test_sports_have_no_pace(tmp_path: Path):
    (tmp_path / "a.md").write_text("""# Personal Context: 2026-11-01 → 2026-11-30
<activities>
<activity date="2026-11-01" type="Soccer" name="Calcetto" duration="01:02:00" distance_km="6.1" avg_hr="150" max_hr="185" min_hr="90" relative_effort="80"/>
<activity date="2026-11-02" type="Padel" name="Padel" duration="1:30:00" distance_km="2.3" elevation_m="4" avg_hr="130" max_hr="170" min_hr="85" relative_effort="40"/>
<activity date="2026-11-03" type="Tennis" name="Tennis" duration="55:00" avg_hr="135" max_hr="172" min_hr="88"/>
<activity date="2026-11-04" type="Volleyball" name="Volley" duration="01:45:00" avg_hr="120" max_hr="165" min_hr="80" relative_effort="30"/>
<activity date="2026-11-05" type="Run" name="Run" duration="25:00" distance_km="5.0" elevation_m="10"/>
</activities>""", encoding="utf-8")
    ds = load_folder(tmp_path)
    assert ds.issues == []
    A = build(ds).activities.set_index("kind")
    assert A.loc["soccer", "duration_s"] == 3720 and A.loc["padel", "duration_s"] == 5400
    for k in ("soccer", "padel", "tennis", "volleyball"):
        assert A.loc[k, "pace_s_km"] != A.loc[k, "pace_s_km"]  # NaN
        assert fmt.activity_speed(A.loc[k].to_dict() | {"kind": k}) == "–"
    assert A.loc["run", "pace_s_km"] == 300
    assert fmt.activity_speed(A.loc["run"].to_dict() | {"kind": "run"}) == "5:00 /km"
    assert A.loc["padel", "kind_label"] == BY_KEY["padel"].label


def test_unknown_type_warns_as_other(tmp_path: Path):
    (tmp_path / "a.md").write_text("""# Personal Context: 2026-11-01 → 2026-11-30
<activities><activity date="2026-11-01" type="Swim" name="Pool" duration="40:00" distance_km="2"/></activities>""",
                                   encoding="utf-8")
    ds = load_folder(tmp_path)
    assert len(ds.issues) == 1 and "'Swim'" in ds.issues[0].message and "Other" in ds.issues[0].message
    A = build(ds).activities
    assert A.iloc[0]["kind"] == "other" and A.iloc[0]["pace_s_km"] != A.iloc[0]["pace_s_km"]
