# puffo-analista

Local dashboard for the monthly context exports of [puffo-coach](https://github.com/Favo02/puffo-coach).

## Usage

1. Paste the monthly `.md` exports into `data/`.
2. Run:

   ```sh
   uv run streamlit run app.py
   ```

   The browser opens at http://localhost:8501. Use another folder with `PUFFO_DATA_DIR=/path uv run streamlit run app.py`
   or the "Data folder" field in the sidebar.

New or changed files are picked up automatically on the next page interaction.

## Structure

| File | Role |
|---|---|
| `puffo_analista/parse.py` | Reads exports into records, validates them against a known schema, detects duplicates |
| `puffo_analista/frames.py` | Builds pandas DataFrames + derived columns (pace, speed, bedtime, partial splits…) |
| `puffo_analista/fmt.py` | Units & formatting: km, 24h clock, min/km (run, hike), km/h (ride), min/100m elev (hike) |
| `puffo_analista/charts.py` | Plotly theme (light/dark palette) and axis helpers |
| `app.py` | Streamlit dashboard: Overview, Sleep, Vitals, Activities |

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

  | Kind | Shown |
  |---|---|
  | Run | distance, pace (min/km), elevation, cadence, per-km pace & HR, HR |
  | Ride | distance, speed (km/h), elevation, cadence, per-km speed & HR, HR |
  | Hike | distance, pace (min/km), elevation, climb rate (min/100m), per-km pace & HR, HR |
  | Workout, Volleyball | duration, avg/min/max HR, relative effort, HR over the session |
  | Soccer, Padel, Tennis | as above, plus distance covered when the watch recorded it (never a pace) |
  | anything else | shown as gray "Other" (duration, HR, effort, distance) with a ⚠️ warning |
- **Durations** are `MM:SS` or `HH:MM:SS`. The last km split is treated as a partial km (remaining distance).
- **Meals** and **medicines** (`<medicine time="HH:MM">…</medicine>` inside a meals `<day>`) are parsed but not
  analysed yet.

## Tests

```sh
uv run pytest
```
