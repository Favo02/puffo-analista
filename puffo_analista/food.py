"""Food analysis: meals grouped into sleep-based days, an LLM prompt, and its answer.

A food day runs from one wake-up to the next, so a snack at 00:45 before going
to bed belongs to the previous day. The wake-up comes from the sleep entry of
the night before (main sleeps only, naps never end a day); without one, 05:00
is the cutoff.

The analysis is done by an external LLM: `build_prompt` writes the prompt,
`parse_answer` validates the JSON it returns, and `save` stores it in
`data/food/YYYY-MM.json` together with the meal text, so the stored analysis
can always be matched against the exports (and survives changes to them).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import pandas as pd

MAIN_SLEEP_MIN = 180  # shorter sleep entries are naps
FALLBACK_CUTOFF = pd.Timedelta(hours=5)  # no sleep data: meals before 05:00 belong to the previous day
NUMBERS = ["kcal", "protein_g", "carbs_g", "sugars_g", "fat_g", "sat_fat_g", "fiber_g", "alcohol_g"]
CONFIDENCE = ("high", "medium", "low")
MEAL_TYPES = {"colazione": "Breakfast", "pranzo": "Lunch", "merenda": "Snack", "cena": "Dinner"}
UNKNOWN = "UNKNOWN"
PROFILE_FILE = "profile.md"

DEFAULT_PROFILE = """\
- Age:
- Sex:
- Height (cm):
- Weight (kg):
- Goal (e.g. maintain weight, lose fat, build muscle, endurance performance):
- Diet notes (allergies, preferences, supplements):

Portion habits, used when a meal has no quantity (edit them to match yours):
- pasta / riso: 100 g dry
- pane in cassetta: 1 slice = 25 g
- yogurt greco: 150 g, 2% fat unless stated
- latte: 200 ml semi-skimmed
- avena: 40 g
- birra: 5% ABV unless stated
- "drink alcolico": one cocktail with 40 ml of spirits
"""


# --------------------------------------------------------------------------- #
# Days
# --------------------------------------------------------------------------- #


def _main_sleep(sleep: pd.DataFrame) -> pd.DataFrame:
    return sleep[(sleep["in_bed_min"] >= MAIN_SLEEP_MIN) & sleep["start_min"].notna() & sleep["end_min"].notna()]


def wake_times(sleep: pd.DataFrame) -> pd.Series:
    """The wake-up that starts each day (index: the day), from the night before it.

    The sleep date is the fall-asleep date, so the night before day D is the
    sleep whose `night_after` is D-1 (asleep at 23:30 on D-1 or 00:30 on D).
    Only a wake-up on D itself counts, never a long nap ending on D-1.
    """
    s = _main_sleep(sleep)
    if s.empty:
        return pd.Series(dtype="datetime64[us]")
    wake = pd.to_datetime(s["asleep_at"]) + pd.to_timedelta(s["in_bed_min"], unit="min")
    wake = wake[wake.dt.normalize() == pd.to_datetime(s["night_after"]) + pd.Timedelta(days=1)]
    return wake.groupby(wake.dt.normalize()).max()


def bedtimes(sleep: pd.DataFrame) -> pd.Series:
    """When you fell asleep at the end of each day (index: the day)."""
    s = _main_sleep(sleep).sort_values("in_bed_min", kind="stable")
    return s.groupby("night_after")["asleep_at"].last()


def assign_days(meals: pd.DataFrame, sleep: pd.DataFrame) -> pd.DataFrame:
    """Add `at` (meal datetime), `day` (food day), `late` (eaten after midnight,
    counted in the previous day), `woke_known` and a stable `id` to each meal."""
    m = meals.copy()
    m["at"] = m["date"] + pd.to_timedelta(m["time"].astype("string") + ":00", errors="coerce")
    cutoff = pd.to_datetime(m["date"].map(wake_times(sleep).to_dict()))
    m["woke_known"] = cutoff.notna()
    cutoff = cutoff.fillna(m["date"] + FALLBACK_CUTOFF)
    m["late"] = (m["at"] < cutoff).astype(bool)  # NaT compares False: unknown time stays on its date
    m["day"] = m["date"] - pd.to_timedelta(m["late"].astype(int), unit="D")
    m = m.sort_values(["day", "at"], kind="stable", na_position="last").reset_index(drop=True)
    base = m["at"].dt.strftime("%Y-%m-%d %H:%M").fillna(m["date"].dt.strftime("%Y-%m-%d") + " --:--")
    n = base.groupby(base).transform("size")
    k = (base.groupby(base).cumcount() + 1).astype(str)
    m["id"] = base.where(n == 1, base + " #" + k)
    return m


def _key(day) -> str:
    return pd.Timestamp(day).strftime("%Y-%m-%d")


def _at(ts) -> str | None:
    return None if pd.isna(ts) else pd.Timestamp(ts).strftime("%Y-%m-%d %H:%M")


def _signature(rows) -> list:
    return sorted((str(at), str(text)) for at, text in rows)


def day_status(meals: pd.DataFrame, stored: dict[str, dict]) -> pd.Series:
    """'ok' / 'changed' / 'missing' for each food day with meals (index: 'YYYY-MM-DD')."""
    out = {}
    for day, g in meals.groupby("day"):
        key = _key(day)
        if key not in stored:
            out[key] = "missing"
            continue
        now = _signature(zip(g["at"].map(_at), g["text"]))
        then = _signature((m.get("at"), m.get("text")) for m in stored[key].get("meals", []))
        out[key] = "ok" if now == then else "changed"
    return pd.Series(out, dtype="string")


# --------------------------------------------------------------------------- #
# Prompt
# --------------------------------------------------------------------------- #

PROMPT = """\
You are a sports nutritionist. Estimate the nutrition of my logged meals, then rate each meal and each day.

## About me

{profile}

## How to read the log

- A day runs from my wake-up to the next wake-up, not midnight to midnight: food eaten after midnight but \
before I went to sleep belongs to the previous day and is marked "(after midnight)". Keep every meal in the \
day it is listed under.
- Meal types are Italian: colazione = breakfast, pranzo = lunch, cena = dinner, merenda = snack (drinks too).
- Descriptions are short Italian notes typed on my phone (Italian products and brands). "2x" means two units \
or portions, "33cl" is a volume, a weight like "100g" is the edible amount.
- When a quantity is missing, use my portion habits above, otherwise a typical single adult portion. A plain \
"pasta" or "riso" includes about 5 g of olive oil unless a sauce is listed.
- "{unknown}" means I ate something but did not write what: estimate a typical meal of that type at that \
time, set confidence "low" and score null.
- Each day header shows when I woke up and fell asleep, my steps, active minutes and workouts (effort is \
Strava's relative effort). Use them to judge my energy and protein needs that day.

## What to estimate

For each meal:
- `kcal`, `protein_g`, `carbs_g`, `sugars_g` (part of carbs), `fat_g`, `sat_fat_g` (part of fat), \
`fiber_g`, `alcohol_g` (pure ethanol: 330 ml of 5% beer is about 13 g). kcal must match \
4 × protein + 4 × carbs + 9 × fat + 7 × alcohol within 10%.
- `score` 0–100: nutritional quality of the meal for its type and moment: whole vs ultra-processed foods, \
protein, fiber, fruit and vegetables, added sugar, saturated fat, alcohol, and whether the portion fits.
- `confidence`: "high" (quantities given, standard foods), "medium" or "low" (vague or unknown).
- `note`: at most 120 characters, the single most useful remark (what made it good or bad, or one concrete \
swap).

For each day:
- `score` 0–100 for the whole day: total energy against my needs given that day's activity, total protein and \
how it is spread, fiber, fruit and vegetables, added sugar, saturated fat, alcohol, ultra-processed foods, \
meal timing and late-night eating.
- `note`: at most 200 characters, what drove the score.

Score anchors, valid for meals and days. Use the whole range and stay consistent across days and months:
- 90–100 excellent: balanced, mostly whole foods, adequate protein and fiber, little added sugar, no alcohol
- 75–89 good: solid, with one minor weakness
- 60–74 fair: acceptable but clearly improvable (low protein or fiber, some processed food or sugar)
- 40–59 poor: mostly processed or sugary, unbalanced, or a clearly wrong amount
- 0–39 very poor: heavy alcohol, mostly junk food, or a severe excess or deficit

Finally `month_summary`: 3–5 sentences on the patterns across these days, the biggest strengths and the \
three changes that would help most.

## Output format

Reply with exactly one ```json code block and nothing else, following this shape (values are only examples):

```json
{{
  "days": [
    {{
      "day": "2026-10-03",
      "meals": [
        {{"id": "2026-10-03 11:27", "kcal": 540, "protein_g": 9, "carbs_g": 68, "sugars_g": 34, "fat_g": 25, \
"sat_fat_g": 12, "fiber_g": 2, "alcohol_g": 0, "score": 35, "confidence": "medium", \
"note": "Biscuits and tiramisu cream: mostly sugar and saturated fat, almost no protein."}},
        {{"id": "2026-10-04 00:21", "kcal": 280, "protein_g": 2, "carbs_g": 24, "sugars_g": 2, "fat_g": 0, \
"sat_fat_g": 0, "fiber_g": 0, "alcohol_g": 26, "score": 15, "confidence": "high", \
"note": "Two beers after midnight: empty calories and worse sleep."}}
      ],
      "score": 38,
      "note": "Low protein and fiber, lots of sugar and alcohol, and eating until 1 am."
    }}
  ],
  "month_summary": "…"
}}
```

Rules:
- One object in `days` for every day of the log, in the same order, each with one object in `meals` for \
every meal of that day, with the `id` copied exactly as written in the log.
- Plain JSON: no comments, no trailing commas, numbers without units or ranges. kcal and scores are integers, \
grams have at most one decimal. Use 0 when a nutrient is absent, never null (null is only for the score of an \
{unknown} meal).
- If the answer would be too long, stop after a complete day, close the JSON properly and tell me: I will \
ask for the remaining days.

## Log ({period})
{incomplete}
{log}
"""


def _activity(a) -> str:
    out = f"{a['type']} {round(a['duration_s'] / 60)} min" if pd.notna(a["duration_s"]) else str(a["type"])
    if pd.notna(a["distance_km"]) and a["distance_km"] > 0:
        out += f", {a['distance_km']:.1f} km"
    if pd.notna(a["relative_effort"]):
        out += f", effort {a['relative_effort']:.0f}"
    return out


def _clock(ts, day) -> str:
    plus = (pd.Timestamp(ts).normalize() - day).days
    return pd.Timestamp(ts).strftime("%H:%M") + (f" (+{plus})" if plus > 0 else "")


def day_header(day: pd.Timestamp, sleep: pd.DataFrame, vitals: pd.DataFrame, activities: pd.DataFrame) -> str:
    parts = []
    wake, bed = wake_times(sleep).get(day), bedtimes(sleep).get(day)
    sleep_txt = [f"woke {_clock(wake, day)}" if wake is not None else "wake-up unknown"]
    if bed is not None:
        sleep_txt.append(f"asleep {_clock(bed, day)}")
    parts.append(", ".join(sleep_txt))
    v = vitals[vitals["date"] == day]
    if not v.empty:
        v = v.iloc[0]
        moves = []
        if pd.notna(v["steps"]):
            moves.append(f"{v['steps']:.0f} steps")
        if pd.notna(v["active_minutes"]):
            moves.append(f"{v['active_minutes']:.0f} active min")
        if moves:
            parts.append(", ".join(moves))
    acts = activities[activities["date"] == day]
    parts.append("; ".join(_activity(a) for _, a in acts.iterrows()) if not acts.empty else "no workout")
    return f"### {day:%Y-%m-%d} ({day:%a}): " + " · ".join(parts)


def build_prompt(meals: pd.DataFrame, sleep: pd.DataFrame, vitals: pd.DataFrame, activities: pd.DataFrame,
                 profile: str, period: str, incomplete: set[str] = frozenset()) -> str:
    """Prompt for the food days in `meals` (already assigned with `assign_days`)."""
    blocks = []
    for day, g in meals.groupby("day", sort=True):
        lines = [day_header(day, sleep, vitals, activities)]
        if _key(day) in incomplete:
            lines.append("(this day may be incomplete: the next day's export is not loaded yet)")
        for _, m in g.iterrows():
            late = " (after midnight)" if m["late"] else ""
            lines.append(f"- `{m['id']}` {m['type']}{late}: {m['text'] or UNKNOWN}")
        blocks.append("\n".join(lines))
    note = ""
    if incomplete:
        note = "\nSome days are marked incomplete: score them on what is logged.\n"
    return PROMPT.format(profile=(profile.strip() or "(no details given)"), unknown=UNKNOWN, period=period,
                         incomplete=note, log="\n\n".join(blocks))


# --------------------------------------------------------------------------- #
# Answer
# --------------------------------------------------------------------------- #


@dataclass
class Answer:
    days: dict[str, dict] = field(default_factory=dict)  # accepted, in storage format
    rejected: dict[str, str] = field(default_factory=dict)  # day -> reason
    warnings: list[str] = field(default_factory=list)
    summary: str | None = None
    error: str | None = None
    truncated: bool = False


def _json_text(text: str) -> str:
    m = re.search(r"```(?:json)?\s*(.*?)(?:```|\Z)", text, re.S)
    s = m.group(1) if m else text
    i = s.find("{")
    return s[i:] if i >= 0 else ""


def _stream_days(s: str) -> tuple[list, bool]:
    """Complete objects of the `days` array, even if the answer was cut off."""
    m = re.search(r'"days"\s*:\s*\[', s)
    if not m:
        return [], True
    dec, i, out = json.JSONDecoder(), m.end(), []
    while True:
        while i < len(s) and s[i] in " \t\r\n,":
            i += 1
        if i >= len(s):
            return out, True
        if s[i] == "]":
            return out, False
        try:
            obj, i = dec.raw_decode(s, i)
        except json.JSONDecodeError:
            return out, True
        out.append(obj)


def _number(v) -> float:
    if isinstance(v, bool):
        raise ValueError
    x = float(v.strip()) if isinstance(v, str) else float(v)
    if x != x or x < 0:
        raise ValueError
    return x


def _score(v, allow_null: bool) -> int | None:
    if v is None and allow_null:
        return None
    x = _number(v)
    if x > 100:
        raise ValueError
    return round(x)


def _meal(obj: dict, row, warnings: list[str]) -> dict:
    """Validate one meal of the answer; raises ValueError with a reason."""
    out = {"at": _at(row["at"]), "type": row["type"], "text": row["text"]}
    for k in NUMBERS:
        try:
            out[k] = round(_number(obj.get(k)), 1)
        except (TypeError, ValueError):
            raise ValueError(f"{row['id']}: bad {k} {obj.get(k)!r}") from None
    out["kcal"] = round(out["kcal"])
    try:
        out["score"] = _score(obj.get("score"), allow_null=(row["text"] or UNKNOWN) == UNKNOWN)
    except (TypeError, ValueError):
        raise ValueError(f"{row['id']}: bad score {obj.get('score')!r}") from None
    conf = str(obj.get("confidence", "")).lower()
    out["confidence"] = conf if conf in CONFIDENCE else None
    out["note"] = str(obj.get("note") or "").strip()

    calc = 4 * out["protein_g"] + 4 * out["carbs_g"] + 9 * out["fat_g"] + 7 * out["alcohol_g"]
    if abs(calc - out["kcal"]) > max(50, 0.2 * out["kcal"]):
        warnings.append(f"`{row['id']}`: {out['kcal']} kcal, but the macros add up to {calc:.0f} kcal")
    if out["sugars_g"] > out["carbs_g"] + 0.5 or out["sat_fat_g"] > out["fat_g"] + 0.5:
        warnings.append(f"`{row['id']}`: sugars or saturated fat exceed their total")
    return out


def parse_answer(text: str, meals: pd.DataFrame) -> Answer:
    """Validate an LLM answer against the current meals (assigned with `assign_days`).

    Days are accepted or rejected one by one, so an answer cut off halfway still
    saves every complete day.
    """
    ans = Answer()
    s = _json_text(text)
    if not s:
        ans.error = "No JSON found in the answer."
        return ans
    try:
        obj, _ = json.JSONDecoder().raw_decode(s)
        days = obj.get("days") if isinstance(obj, dict) else None
        if not isinstance(days, list):
            ans.error = 'The JSON has no "days" list.'
            return ans
        summary = obj.get("month_summary")
        ans.summary = summary.strip() if isinstance(summary, str) and summary.strip() else None
    except json.JSONDecodeError:
        days, ans.truncated = _stream_days(s)
        if not days:
            ans.error = "The answer is not valid JSON and no complete day could be read from it."
            return ans

    expected = {_key(d): g for d, g in meals.groupby("day")}
    saved_at = datetime.now().isoformat(timespec="seconds")
    for d in days:
        if not isinstance(d, dict):
            ans.warnings.append("Ignored an entry of `days` that is not an object.")
            continue
        key = str(d.get("day", "")).strip()
        if key not in expected:
            ans.rejected[key or "?"] = "no meals logged on this day"
            continue
        if key in ans.days or key in ans.rejected:
            ans.rejected[key] = "listed twice"
            ans.days.pop(key, None)
            continue
        rows = expected[key]
        got = {str(m.get("id", "")).strip(): m for m in d.get("meals") or [] if isinstance(m, dict)}
        missing = [i for i in rows["id"] if i not in got]
        unknown = [i for i in got if i not in set(rows["id"])]
        if unknown:
            ans.rejected[key] = (f"unknown meal id(s) {', '.join(unknown[:3])}: the data may have changed, "
                                 "copy the prompt again")
            continue
        if missing:
            ans.rejected[key] = f"missing meal(s) {', '.join(missing[:3])}" + ("…" if len(missing) > 3 else "")
            continue
        try:
            day_score = _score(d.get("score"), allow_null=False)
        except (TypeError, ValueError):
            ans.rejected[key] = f"bad day score {d.get('score')!r}"
            continue
        warnings: list[str] = []
        try:
            stored = [_meal(got[r["id"]], r, warnings) for _, r in rows.iterrows()]
        except ValueError as e:
            ans.rejected[key] = str(e)
            continue
        ans.warnings += warnings
        ans.days[key] = {"score": day_score, "note": str(d.get("note") or "").strip(), "saved_at": saved_at,
                         "meals": stored}
    if ans.truncated:
        ans.warnings.insert(0, f"The answer was cut off: {len(ans.days)} complete day(s) read.")
    return ans


# --------------------------------------------------------------------------- #
# Storage (data/food/YYYY-MM.json, data/food/profile.md)
# --------------------------------------------------------------------------- #


def _write(path: Path, text: str):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def load_store(folder: Path) -> tuple[dict[str, dict], dict[str, str], list[str]]:
    """(days by 'YYYY-MM-DD', month summaries by 'YYYY-MM', problems)."""
    days, summaries, problems = {}, {}, []
    for path in sorted(folder.glob("????-??.json")) if folder.is_dir() else []:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            days.update(data["days"])
            if data.get("summary"):
                summaries[path.stem] = data["summary"]
        except (OSError, ValueError, KeyError, TypeError) as e:
            problems.append(f"`{path.name}` could not be read ({e}); it is ignored.")
    return days, summaries, problems


def save(folder: Path, answer: Answer, month: str | None = None) -> list[Path]:
    """Merge the accepted days into their month files (replacing those days).
    The month summary is saved only for `month`, the month the prompt was for."""
    folder.mkdir(parents=True, exist_ok=True)
    by_month: dict[str, dict] = {}
    for key, d in answer.days.items():
        by_month.setdefault(key[:7], {})[key] = d
    paths = []
    for mo, new in sorted(by_month.items()):
        path = folder / f"{mo}.json"
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"version": 1, "month": mo}
        data["days"] = dict(sorted({**data.get("days", {}), **new}.items()))
        if answer.summary and mo == month:
            data["summary"] = answer.summary
        _write(path, json.dumps(data, ensure_ascii=False, indent=1) + "\n")
        paths.append(path)
    return paths


def load_profile(folder: Path) -> str:
    path = folder / PROFILE_FILE
    return path.read_text(encoding="utf-8") if path.exists() else DEFAULT_PROFILE


def save_profile(folder: Path, text: str):
    folder.mkdir(parents=True, exist_ok=True)
    _write(folder / PROFILE_FILE, text.strip() + "\n")


def tables(days: dict[str, dict]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(per-day table with totals, per-meal table) of the stored analysis."""
    meal_rows, day_rows = [], []
    for key, d in days.items():
        day = pd.Timestamp(key)
        meals = d.get("meals", [])
        for m in meals:
            meal_rows.append({"day": day, **{k: m.get(k) for k in ("at", "type", "text", "confidence", "note")},
                              **{k: m.get(k) for k in NUMBERS}, "score": m.get("score")})
        day_rows.append({"day": day, "score": d.get("score"), "note": d.get("note", ""), "n_meals": len(meals),
                         **{k: sum(m.get(k) or 0 for m in meals) for k in NUMBERS}})
    M = pd.DataFrame(meal_rows, columns=["day", "at", "type", "text", "confidence", "note", *NUMBERS, "score"])
    M["at"] = pd.to_datetime(M["at"])
    M["score"] = pd.to_numeric(M["score"]).astype(float)
    D = pd.DataFrame(day_rows, columns=["day", "score", "note", "n_meals", *NUMBERS])
    D["score"] = pd.to_numeric(D["score"]).astype(float)
    return D.sort_values("day").reset_index(drop=True), M.sort_values(["day", "at"]).reset_index(drop=True)
