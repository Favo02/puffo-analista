import json
from pathlib import Path

import pandas as pd
import pytest

from puffo_analista import food
from puffo_analista.frames import build
from puffo_analista.parse import load_folder

SAMPLE = Path(__file__).parent / "fixtures" / "context_from_2026-10-05_to_2026-10-06.md"


def frames(tmp_path, meals: str, sleep: str):
    body = f"<meals>\n{meals}\n</meals>\n<sleep>\n{sleep}\n</sleep>\n"
    (tmp_path / "x.md").write_text(f"# Personal Context: 2026-11-01 → 2026-11-30\n\n{body}", encoding="utf-8")
    ds = load_folder(tmp_path)
    assert [i for i in ds.issues if i.level == "error"] == []
    return build(ds)


def night(d, start, end, total=420):
    return f'<night date="{d}" score="70" total_min="{total}" start="{start}" end="{end}"/>'


def meal(t, text="pasta", type_="cena"):
    return f'<meal time="{t}" type="{type_}">{text}</meal>'


def days_of(F):
    return dict(zip(F.meals["id"], F.meals["day"].dt.strftime("%m-%d")))


def test_sample_after_midnight_snack_goes_to_previous_day(tmp_path):
    (tmp_path / SAMPLE.name).write_text(SAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
    F = build(load_folder(tmp_path))
    d = days_of(F)
    # asleep 00:04 → 09:32 on the 5th: the 00:34 herbal tea is the evening of the 4th
    assert d["2026-10-05 00:34"] == "10-04"
    assert d["2026-10-05 11:34"] == "10-05" and d["2026-10-05 23:06"] == "10-05"
    assert F.meals.loc[F.meals["id"] == "2026-10-05 00:34", "late"].item()


def test_two_sleep_entries_on_the_same_date(tmp_path):
    # the 6th holds two nights: asleep 00:00 (night of the 5th) and 23:03 (night of the 6th)
    F = frames(tmp_path,
               f'<day date="2026-11-06">{meal("00:30")}{meal("09:00", "latte", "colazione")}{meal("23:30")}</day>'
               f'<day date="2026-11-07">{meal("08:30", "caffè", "colazione")}{meal("09:30", "latte", "colazione")}</day>',
               night("2026-11-06", "00:00", "09:52") + night("2026-11-06", "23:03", "08:54"))
    d = days_of(F)
    assert d["2026-11-06 00:30"] == "11-05"  # before waking at 09:52
    assert d["2026-11-06 09:00"] == "11-05"  # still before waking up
    assert d["2026-11-06 23:30"] == "11-06"
    assert d["2026-11-07 08:30"] == "11-06"  # before waking at 08:54 on the 7th
    assert d["2026-11-07 09:30"] == "11-07"


def test_naps_never_end_a_day_and_fallback_is_five_am(tmp_path):
    F = frames(tmp_path,
               f'<day date="2026-11-10">{meal("02:00", "birra 33cl", "merenda")}{meal("06:00", "latte", "colazione")}'
               f'{meal("12:00")}</day>'
               f'<day date="2026-11-11">{meal("04:59")}{meal("05:01")}</day>',
               # only a 1 h nap, at 10:00 on the 10th
               night("2026-11-10", "10:00", "11:00", total=60))
    d = days_of(F)
    assert d["2026-11-10 02:00"] == "11-09"
    assert d["2026-11-10 06:00"] == "11-10" and d["2026-11-10 12:00"] == "11-10"
    assert d["2026-11-11 04:59"] == "11-10" and d["2026-11-11 05:01"] == "11-11"
    assert not F.meals["woke_known"].any()


def test_all_nighter_sleeping_in_the_morning(tmp_path):
    # never went to bed: asleep 08:00 → 14:00, so lunch at 13:00 is still the day before
    F = frames(tmp_path, f'<day date="2026-11-12">{meal("03:00")}{meal("13:00")}{meal("15:00")}</day>',
               night("2026-11-12", "08:00", "14:00", total=360))
    d = days_of(F)
    assert d["2026-11-12 03:00"] == "11-11" and d["2026-11-12 13:00"] == "11-11"
    assert d["2026-11-12 15:00"] == "11-12"


def test_duplicate_times_get_distinct_ids(tmp_path):
    F = frames(tmp_path, f'<day date="2026-11-13">{meal("12:45", "UNKNOWN")}{meal("12:45", "UNKNOWN")}</day>', "")
    assert list(F.meals["id"]) == ["2026-11-13 12:45 #1", "2026-11-13 12:45 #2"]


# --------------------------------------------------------------------------- #
# prompt and answer
# --------------------------------------------------------------------------- #


@pytest.fixture
def sample(tmp_path):
    (tmp_path / SAMPLE.name).write_text(SAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
    return build(load_folder(tmp_path))


def test_prompt_lists_days_with_context(sample):
    F = sample
    p = food.build_prompt(F.meals, F.sleep, F.vitals, F.activities, "Weight: 70 kg", "October 2026",
                          incomplete={"2026-10-06"})
    assert "Weight: 70 kg" in p
    assert "### 2026-10-05 (Mon): woke 09:32, asleep 00:00 (+1) · 4280 steps, 0 active min" in p
    assert "- `2026-10-05 00:34` merenda (after midnight): tisana alla camomilla e lavanda" in p
    assert p.index("### 2026-10-04") < p.index("`2026-10-05 00:34`") < p.index("### 2026-10-05")
    assert "next day's export is not loaded yet" in p
    assert "{" in p and "{{" not in p  # format escapes resolved


def meal_answer(i, **kw):
    v = dict(id=i, kcal=400, protein_g=20, carbs_g=50, sugars_g=10, fat_g=13, sat_fat_g=4, fiber_g=5,
             alcohol_g=0, score=70, confidence="medium", note="ok")
    return {**v, **kw}


def answer(F, days=None, **meal_kw):
    out = []
    for d, g in F.meals.groupby("day"):
        key = d.strftime("%Y-%m-%d")
        if days is None or key in days:
            out.append({"day": key, "meals": [meal_answer(i, **meal_kw) for i in g["id"]], "score": 60,
                        "note": "fine"})
    return {"days": out, "month_summary": "Good month."}


def test_parse_full_answer_and_save(sample, tmp_path):
    F = sample
    text = "Here you go:\n```json\n" + json.dumps(answer(F)) + "\n```"
    a = food.parse_answer(text, F.meals)
    assert a.error is None and a.rejected == {} and a.summary == "Good month."
    assert set(a.days) == {"2026-10-04", "2026-10-05", "2026-10-06"}
    d5 = a.days["2026-10-05"]["meals"]
    assert d5[0] == {"at": "2026-10-05 11:34", "type": "colazione", "text": F.meals.iloc[1]["text"], "kcal": 400,
                     "protein_g": 20, "carbs_g": 50, "sugars_g": 10, "fat_g": 13, "sat_fat_g": 4, "fiber_g": 5,
                     "alcohol_g": 0, "score": 70, "confidence": "medium", "note": "ok"}

    store = tmp_path / "food"
    food.save(store, a, month="2026-10")
    days, summaries, problems = food.load_store(store)
    assert problems == [] and summaries == {"2026-10": "Good month."} and set(days) == set(a.days)
    assert set(food.day_status(F.meals, days)) == {"ok"}
    D, M = food.tables(days)
    assert D.loc[D["day"] == "2026-10-05", "kcal"].item() == 400 * 5  # 00:34 tea moved to the 4th
    assert len(M) == len(F.meals)

    # a later answer for one day replaces only that day
    b = food.parse_answer(json.dumps(answer(F, days={"2026-10-06"}, score=20)), F.meals)
    food.save(store, b, month="2026-10")
    days, summaries, _ = food.load_store(store)
    assert {m["score"] for m in days["2026-10-06"]["meals"]} == {20}
    assert {m["score"] for m in days["2026-10-05"]["meals"]} == {70}


def test_truncated_answer_keeps_complete_days(sample):
    text = json.dumps(answer(sample))
    cut = text[: text.index('"2026-10-06"') + 40]  # cut inside the last day
    a = food.parse_answer("```json\n" + cut, sample.meals)
    assert a.truncated and set(a.days) == {"2026-10-04", "2026-10-05"}
    assert "cut off" in a.warnings[0]


def test_bad_days_are_rejected_one_by_one(sample):
    data = answer(sample)
    data["days"][1]["meals"].pop()  # 10-05 misses a meal
    data["days"][2]["meals"][0]["kcal"] = "a lot"  # 10-06 has a bad value
    data["days"].append({"day": "2026-10-20", "meals": [], "score": 50})
    a = food.parse_answer(json.dumps(data), sample.meals)
    assert set(a.days) == {"2026-10-04"}
    assert "missing meal" in a.rejected["2026-10-05"]
    assert "bad kcal" in a.rejected["2026-10-06"]
    assert "no meals" in a.rejected["2026-10-20"]


def test_unknown_ids_and_checks(sample):
    data = answer(sample)
    data["days"][0]["meals"][0]["id"] = "2026-10-05 00:35"
    data["days"][1]["meals"][0].update(kcal=900)  # macros add up to ~397 kcal
    a = food.parse_answer(json.dumps(data), sample.meals)
    assert "copy the prompt again" in a.rejected["2026-10-04"]
    assert any("macros add up" in w for w in a.warnings)
    # score null is only allowed for UNKNOWN meals
    data = answer(sample, score=None)
    assert set(food.parse_answer(json.dumps(data), sample.meals).days) == set()


def test_changed_meal_marks_day_changed(sample, tmp_path):
    a = food.parse_answer(json.dumps(answer(sample)), sample.meals)
    meals = sample.meals.copy()
    meals.loc[meals["id"] == "2026-10-06 17:25", "text"] = "yogurt greco, miele"
    st = food.day_status(meals, a.days)
    assert st["2026-10-06"] == "changed" and st["2026-10-05"] == "ok"
    assert food.day_status(meals, {})["2026-10-05"] == "missing"
    assert pd.isna(food.tables({})[0]["score"]).all()
