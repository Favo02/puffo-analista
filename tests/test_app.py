import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from puffo_analista.frames import build
from puffo_analista.parse import load_folder

ROOT = Path(__file__).parent.parent
SAMPLE = Path(__file__).parent / "fixtures" / "context_from_2026-10-05_to_2026-10-06.md"


@pytest.fixture
def app(tmp_path, monkeypatch):
    (tmp_path / SAMPLE.name).write_text(SAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setenv("PUFFO_DATA_DIR", str(tmp_path))
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30)
    at.run()
    assert not at.exception, at.exception
    return at, tmp_path


def test_food_prompt_and_import_round_trip(app):
    at, data = app
    prompt = next(c.value for c in at.code if "## Log (October 2026)" in c.value)
    assert "`2026-10-05 00:34` merenda (after midnight)" in prompt

    F = build(load_folder(data))
    days = [{"day": d.strftime("%Y-%m-%d"), "score": 55, "note": "ok",
             "meals": [dict(id=i, kcal=300, protein_g=15, carbs_g=40, sugars_g=5, fat_g=9, sat_fat_g=2, fiber_g=3,
                            alcohol_g=0, score=60, confidence="high", note="") for i in g["id"]]}
            for d, g in F.meals.groupby("day")]
    at.text_area[-1].input("```json\n" + json.dumps({"days": days, "month_summary": "Fine."}) + "\n```")
    next(b for b in at.button if b.label == "Check and save").click()
    at.run()
    assert not at.exception, at.exception

    assert json.loads((data / "food" / "2026-10.json").read_text())["summary"] == "Fine."
    assert any("Saved 3 day(s)" in s.value for s in at.success)
    labels = {m.label: m.value for m in at.metric}
    assert labels["Food score"] == "55" and labels["Calories"] == "900 kcal"  # 9 meals × 300 over 3 food days (10-04 is the 00:34 tea)
    assert any("Every day of this month is analysed" in s.value for s in at.success)
