from datetime import date
from pathlib import Path

import pytest

from puffo_analista.frames import build
from puffo_analista.parse import load_folder, night_after_day, parse_duration

SAMPLE = Path(__file__).parent / "fixtures" / "context_from_2026-10-05_to_2026-10-06.md"


def write(folder: Path, name: str, body: str, start="2026-11-01", end="2026-11-30"):
    (folder / name).write_text(f"# Personal Context: {start} → {end}\n\n{body}", encoding="utf-8")


def test_sample_loads_cleanly(tmp_path):
    (tmp_path / SAMPLE.name).write_text(SAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
    ds = load_folder(tmp_path)
    assert ds.issues == []
    t = ds.tables
    assert len(t["vitals"]) == 2 and len(t["hr_buckets"]) == 8
    assert len(t["sleep"]) == 2 and len(t["activities"]) == 3
    assert len(t["splits"]) == 11 and len(t["hr_chunks"]) == 10 and len(t["meals"]) == 9
    assert t["vitals"][0]["vo2max"] is None and t["vitals"][1]["vo2max"] == 60.9

    F = build(ds)
    run = F.activities[F.activities["type"] == "Run"].iloc[0]
    assert run["duration_s"] == 33 * 60 + 48
    # last run split is 0.9 km -> pace ~5:35/km, not 5:02
    last = F.splits[F.splits["activity_id"] == run["id"]].iloc[-1]
    assert last["km"] == pytest.approx(0.9)
    assert last["pace_s_km"] == pytest.approx(302 / 0.9)


@pytest.mark.parametrize("raw,secs", [("3:47", 227), ("33:48", 2028), ("01:05:00", 3900), ("1:5", None),
                                      ("x:10", None), ("10:75", None)])
def test_parse_duration(raw, secs):
    assert parse_duration(raw) == secs


def test_night_after_day():
    assert night_after_day(date(2026, 11, 5), 23 * 60 + 30) == date(2026, 11, 5)
    assert night_after_day(date(2026, 11, 5), 45) == date(2026, 11, 4)


def test_unknown_things_are_warned(tmp_path):
    write(tmp_path, "a.md", """<vitals>
<day date="2026-11-02" steps="100" stress="12"><mood>ok</mood></day>
</vitals>
<weight><day date="2026-11-02" kg="70"/></weight>
stray text
<sleep><night date="2026-11-02" score="abc" start="23:10" end="07:00"/></sleep>
""")
    ds = load_folder(tmp_path)
    msgs = " | ".join(i.message for i in ds.issues)
    assert all(i.level == "warning" for i in ds.issues)
    assert "stress" in msgs and "<mood>" in msgs and "<weight>" in msgs
    assert "stray text" in msgs and "score='abc'" in msgs
    assert ds.tables["vitals"][0]["steps"] == 100  # known data still loaded
    assert ds.tables["sleep"][0]["score"] is None


def test_duplicates_across_files_are_dropped(tmp_path):
    write(tmp_path, "a.md", '<vitals><day date="2026-11-01" steps="1"/><day date="2026-11-02" steps="2"/></vitals>')
    write(tmp_path, "b.md", '<vitals><day date="2026-11-02" steps="3"/><day date="2026-11-03" steps="4"/></vitals>',
          start="2026-11-02", end="2026-11-03")
    ds = load_folder(tmp_path)
    errors = [i for i in ds.issues if i.level == "error"]
    assert any("overlap" in e.message for e in errors)
    assert any("2026-11-02" in e.message and "multiple files" in e.message for e in errors)
    assert sorted(r["steps"] for r in ds.tables["vitals"]) == [1, 4]


def test_duplicate_day_inside_file(tmp_path):
    write(tmp_path, "a.md", '<sleep><night date="2026-11-01" score="1"/><night date="2026-11-01" score="2"/></sleep>'
                            '<meals><day date="2026-11-01"><meal time="08:00" type="colazione">a</meal>'
                            '<meal time="12:00" type="pranzo">b</meal></day></meals>')
    ds = load_folder(tmp_path)
    assert ds.tables["sleep"] == []
    assert len(ds.tables["meals"]) == 2  # several meals in one <day> is not a duplicate
    assert len([i for i in ds.issues if i.level == "error"]) == 1


def test_multiple_sleep_entries_same_date_are_valid(tmp_path):
    # asleep 00:40 on the 2nd, then again 23:30 on the 2nd: both dated 2026-11-02
    write(tmp_path, "a.md", '<sleep>'
                            '<night date="2026-11-02" score="70" start="00:40" end="08:30"/>'
                            '<night date="2026-11-02" score="80" start="23:30" end="07:10"/>'
                            '</sleep>')
    ds = load_folder(tmp_path)
    assert ds.issues == []
    F = build(ds)
    assert F.sleep["score"].tolist() == [70, 80]
    assert F.sleep["asleep_at"].dt.strftime("%Y-%m-%d %H:%M").tolist() == ["2026-11-02 00:40", "2026-11-02 23:30"]
    assert F.sleep["bed_min"].tolist() == [40, -30]


def test_overlapping_sleep_entries_warn(tmp_path):
    write(tmp_path, "a.md", '<sleep>'
                            '<night date="2026-11-01" start="23:00" end="07:00"/>'
                            '<night date="2026-11-02" start="06:00" end="09:00"/>'
                            '</sleep>')
    ds = load_folder(tmp_path)
    assert [i.level for i in ds.issues] == ["warning"] and "overlap" in ds.issues[0].message
    assert len(ds.tables["sleep"]) == 2


def test_ampersand_and_hike_and_long_duration(tmp_path):
    write(tmp_path, "a.md", """<meals><day date="2026-11-01"><meal time="08:00" type="colazione">pane & burro</meal></day></meals>
<activities>
<activity date="2026-11-01" type="Hike" name="Monte" duration="02:30:00" distance_km="10" elevation_m="900"/>
<activity date="2026-11-01" type="Swim" name="Pool" duration="40:00" distance_km="2"/>
</activities>""")
    ds = load_folder(tmp_path)
    assert ds.tables["meals"][0]["text"] == "pane & burro"
    assert [i.message for i in ds.issues if "Swim" in i.message]
    F = build(ds)
    hike = F.activities[F.activities["type"] == "Hike"].iloc[0]
    assert hike["duration_s"] == 9000
    assert hike["pace_s_km"] == 900  # 15:00 /km
    assert hike["elev_pace"] == pytest.approx(150 / 9)  # min per 100 m climbed


def test_unparseable_file_is_reported(tmp_path):
    write(tmp_path, "bad.md", "<vitals><day date='2026-11-01'></vitals>")
    ds = load_folder(tmp_path)
    assert ds.files == [] and ds.issues[0].level == "error"
