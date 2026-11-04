# Puffo Analista

Local dashboard for the monthly context exports of [puffo-coach](https://github.com/Favo02/puffo-coach).

## Usage

1. Paste the monthly `.md` exports into `data/`.
2. Run:

   ```sh
   uv run streamlit run app.py
   ```

   The browser opens at http://localhost:8501. Use another folder with `PUFFO_DATA_DIR=/path uv run streamlit run app.py`.

New or changed files are picked up automatically on the next page interaction.

## Structure

| File                       | Role                                                                                     |
| -------------------------- | ---------------------------------------------------------------------------------------- |
| `puffo_analista/parse.py`  | Reads exports into records, validates them against a known schema, detects duplicates    |
| `puffo_analista/frames.py` | Builds pandas DataFrames + derived columns (pace, speed, bedtime, partial splits…)       |
| `puffo_analista/fmt.py`    | Units & formatting: km, 24h clock, min/km (run, hike), km/h (ride), min/100m elev (hike) |
| `puffo_analista/charts.py` | Plotly theme (light/dark palette) and axis helpers                                       |
| `puffo_analista/food.py`   | Food days, the LLM prompt, validation and storage of its answer                          |
| `app.py`                   | Streamlit dashboard: Overview, Sleep, Vitals, Activities, Food                           |

## Data rules

- **Unknown content** (tags, attributes, stray text, unknown activity types, unreadable values) is ignored and listed
  as a ⚠️ warning at the top of the dashboard: a hint that puffo-analista needs an update.
- **Duplicates**: if the same day appears in more than one file (or twice in the same file) for a section, that day
  is **not loaded** for that section and a ⛔ error is shown. Overlapping header date ranges are also reported.
- **Sleep date** is the day you fell asleep (23:30 on the 5th → date 5th; 00:45 on the 6th → date 6th). A date can
  therefore hold several sleep entries (asleep at 00:45 and again at 23:30): they are all valid, and sleep charts
  are placed at the fall-asleep time. Only an entry repeated with the same start time is a duplicate (not loaded);
  overlapping entries are warned about.
- **Activities** are grouped by kind (Strava `sport_type` matched like puffo-coach does, so `TrailRun` is a run and
  `WeightTraining` a workout). Each kind shows only what makes sense for it:

  | Kind                  | Shown                                                                           |
  | --------------------- | ------------------------------------------------------------------------------- |
  | Run                   | distance, pace (min/km), elevation, cadence, per-km pace & HR, HR               |
  | Ride                  | distance, speed (km/h), elevation, cadence, per-km speed & HR, HR               |
  | Hike                  | distance, pace (min/km), elevation, climb rate (min/100m), per-km pace & HR, HR |
  | Workout, Volleyball   | duration, avg/min/max HR, relative effort, HR over the session                  |
  | Soccer, Padel, Tennis | as above, plus distance covered when the watch recorded it (never a pace)       |
  | anything else         | shown as gray "Other" (duration, HR, effort, distance) with a ⚠️ warning        |

- **Durations** are `MM:SS` or `HH:MM:SS`. The last km split is treated as a partial km (remaining distance).
- **Food days** run from one wake-up to the next, so a snack at 00:45 before going to bed belongs to the day before.
  The wake-up is the end of the night before (the sleep entry asleep at 23:30 on D-1 or 00:30 on D; naps under 3 h
  never end a day); without sleep data, 05:00 is the cutoff.
- **Medicines** (`<medicine time="HH:MM">…</medicine>` inside a meals `<day>`) are parsed but not analysed.

## Food analysis

Nutrition is estimated by an external LLM, in the **Food** tab:

1. Fill in **About you** once (age, weight, goal, portion habits): saved in `data/food/profile.md`.
2. Pick a month and copy the prompt: by default it holds only the days not analysed yet. Each day lists its meals
   plus wake-up, bedtime, steps and workouts.
3. Paste the LLM's JSON answer back and press **Check and save**. Every meal gets kcal, macros, a 0–100 score and a
   note; every day a 0–100 score.

The answer is checked day by day: a day with missing or unknown meals or bad values is not saved (the rest is), and
an answer cut off halfway still saves every complete day. Days are stored in `data/food/YYYY-MM.json` together with
the meal text, so a meal edited in a later export shows its day as _changed_ and back in the next prompt.

## Colors

Single-metric charts color every point/bar from red (poor) through amber to green (good), using the rules in
`puffo_analista/quality.py`: fixed targets where they exist (sleep score, bedtime ≈ 23:00, wake-up ≈ 08:00,
7.5–8.5 h of sleep, steps, SpO₂, PAI…) and your own all-time baseline otherwise (resting HR, HRV, VO₂max, pace…).
The "How colors work" popover in the header lists them; tune the numbers there to taste.

Behind every time series runs a smoothed trend (`puffo_analista/trend.py`, Gaussian kernel): its width adapts to
the selected period (short-term in a month, seasonal over a year), and it is left empty across gaps in the data.

## Tests

```sh
uv run pytest
```
