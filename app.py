"""puffo-analista: dashboard for puffo-coach monthly exports.

Run with:  uv run streamlit run app.py
"""

from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from puffo_analista import fmt
from puffo_analista.activities import BY_KEY, HIKE, KINDS, RIDE, RUN, Kind
from puffo_analista import quality, trend
from puffo_analista.charts import SLEEP_STAGES, Theme, clock_axis, date_axis, pace_axis, quality_color, series
from puffo_analista.frames import Frames, build
from puffo_analista.parse import Dataset, load_folder

st.set_page_config(page_title="puffo-analista", page_icon="📈", layout="wide", initial_sidebar_state="collapsed")

DEFAULT_DATA_DIR = Path(os.environ.get("PUFFO_DATA_DIR", Path(__file__).parent / "data"))
PLOT_CFG = {"displayModeBar": False}
# Nightly bars sit at the fall-asleep time, which is irregular (two entries can share
# a date), so plotly's automatic width collapses to hairlines: fix it to 18 h.
NIGHT_BAR_MS = 18 * 3600 * 1000


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #


def _folder_signature(folder: Path) -> tuple:
    return tuple(sorted((p.name, p.stat().st_mtime_ns) for p in folder.glob("*.md")))


@st.cache_data(show_spinner="Parsing exports…")
def load(folder: str, _sig: tuple) -> tuple[Dataset, Frames]:
    ds = load_folder(Path(folder))
    return ds, build(ds)


_chart_ids = iter(range(1_000_000))


def plot(fig: go.Figure):
    # explicit key: the same chart may appear in two tabs (e.g. sleep score)
    st.plotly_chart(fig, width="stretch", config=PLOT_CFG, theme=None, key=f"chart-{next(_chart_ids)}")


def empty(msg="No data in the selected period."):
    st.info(msg, icon="ℹ️")


# --------------------------------------------------------------------------- #
# Header: title + period picker
# --------------------------------------------------------------------------- #

folder = DEFAULT_DATA_DIR
st.title("puffo-analista")
if not folder.is_dir():
    st.error(f"Data folder not found: `{folder}`. Create it and paste your monthly `.md` exports there.")
    st.stop()
ds, F = load(str(folder), _folder_signature(folder))

dates = F.all_dates()
if dates.empty:
    st.warning(f"No data found in `{folder}`. Paste your monthly `.md` exports there.")
    for i in ds.issues:
        (st.error if i.level == "error" else st.warning)(f"**{i.file}** — {i.message}")
    st.stop()

first, last = dates.min().date(), dates.max().date()
months = sorted({d.strftime("%Y-%m") for d in dates}, reverse=True)

c_period, c_range, c_info, c_colors = st.columns([1, 1, 2, 1], vertical_alignment="bottom")
with c_colors.popover("🎨 How colors work", width="stretch"):
    st.markdown(
        "Each point or bar is rated from **red** (poor) through **amber** (fair) to **green** (good); "
        "hover shows the value and the rating. Metrics without a clear good/bad (e.g. training load, "
        "heart rate during sessions) stay neutral.\n\n"
        "**Fixed targets**\n"
        "- Sleep score: 50 → 90\n- Sleep duration: 7.5–8.5 h best\n"
        "- Bedtime: closer to 23:00 · Wake-up: closer to 08:00\n- Wakes: fewer is better\n"
        "- SpO₂: 92% → 98%\n- Steps: 3k → 10k · Active minutes: 0 → 60 · PAI: 0 → 100\n\n"
        "**Your own baseline** (all-time median ± 2 robust SD)\n"
        "- Resting HR (day & night): lower is better\n- HRV, VO₂max: higher is better\n"
        "- Respiratory rate: closer to your usual is better\n"
        "- Run / hike pace, hike climb rate: faster is better · Ride speed: higher is better"
    )
period = c_period.selectbox("Period", ["All time", *months, "Custom range"])
if period == "All time":
    start, end = first, last
elif period == "Custom range":
    rng = c_range.date_input("Range", (first, last), min_value=first, max_value=last, format="YYYY-MM-DD")
    start, end = (rng[0], rng[1]) if len(rng) == 2 else (rng[0], rng[0])
else:
    p = pd.Period(period, "M")
    start, end = p.start_time.date(), p.end_time.date()
c_info.caption(
    f"{start} → {end} · {len(ds.files)} file(s) loaded",
    help="Files in `" + str(folder) + "`:\n\n" + "\n".join(f"- {f.path.name}" for f in ds.files),
)

theme = Theme(getattr(getattr(st.context, "theme", None), "type", None) or "light")
P = F.filter(start, end)
n_days = (end - start).days + 1
prev = F.filter(start - timedelta(days=n_days), start - timedelta(days=1)) if period != "All time" else None

# --------------------------------------------------------------------------- #
# Issues
# --------------------------------------------------------------------------- #

errors = [i for i in ds.issues if i.level == "error"]
warnings = [i for i in ds.issues if i.level == "warning"]
if errors:
    st.error(f"**{len(errors)} problem(s) prevented some data from loading.** See details below.", icon="⛔")
if warnings:
    st.warning(
        f"**{len(warnings)} unexpected item(s) in the exports.** They were ignored — "
        "puffo-analista may need an update.", icon="⚠️",
    )
if ds.issues:
    with st.expander("Details", expanded=bool(errors)):
        for i in errors + warnings:
            st.markdown(f"{'⛔' if i.level == 'error' else '⚠️'} `{i.file}` — {i.message}")

tab_over, tab_sleep, tab_vitals, tab_act = st.tabs(["Overview", "Sleep", "Vitals", "Activities"])

def kinds_in(df: pd.DataFrame) -> list[Kind]:
    """Activity kinds present in df, in fixed display order."""
    present = set(df["kind"])
    return [k for k in KINDS if k.key in present]


def kind_bars(fig: go.Figure, df: pd.DataFrame, x: str, y: str, hover: str, scale=1.0, customdata=None):
    """One stacked bar trace per activity kind, fixed order and color."""
    for k in kinds_in(df):
        d = df[df["kind"] == k.key]
        fig.add_bar(x=d[x], y=d[y] * scale, name=k.label, marker_color=theme.kind_color(k.key),
                    marker_line=theme.bar_gap_line(),
                    customdata=None if customdata is None else d[y].map(customdata),
                    hovertemplate=hover + "<extra>" + k.label + "</extra>")
    return fig


def kind_dots(fig: go.Figure, df: pd.DataFrame, y: str, hover: str):
    """One scatter trace per activity kind (session-level values over time)."""
    for k in kinds_in(df):
        d = df[(df["kind"] == k.key) & df[y].notna()]
        if d.empty:
            continue
        fig.add_scatter(
            x=d["date"], y=d[y], name=k.label, mode="markers",
            marker=dict(size=10, color=theme.kind_color(k.key), line=dict(width=2, color=theme.ink["surface"])),
            customdata=d["name"], hovertemplate="%{customdata}<br>" + hover + "<extra>" + k.label + "</extra>",
        )
    return fig


def INT(v):
    return fmt.num(v)


def BPM(v):
    return fmt.num(v, 0, "bpm")


def MS(v):
    return fmt.num(v, 0, "ms")


def STEPS(v):
    return fmt.num(v, 0, "steps")


def ts(df: pd.DataFrame, x: str, y: str, title: str, value_fmt, history: pd.DataFrame | None = None, *,
       rule: str | None = "auto", color: str | None = None, bars: bool = False, height: int = 300,
       asleep: bool = False, scale: float = 1.0) -> go.Figure:
    """Single-metric time series, marks colored by quality.

    `rule="auto"` uses the quality rule named like the column (if any); `history`
    is the all-time table the personal baseline is computed from. `scale` changes
    only the plotted unit (e.g. minutes → hours), never the rating.
    """
    key = y if rule == "auto" else rule
    base = history[y] if history is not None and y in history else None
    fig = go.Figure(series(theme, df, x, y, name=title.split(" (")[0], value_fmt=value_fmt, rule_key=key,
                           baseline=base, color=color, bars=bars, scale=scale))
    theme.layout(fig, title, height=height, legend=False)
    if bars and asleep:
        fig.update_traces(width=NIGHT_BAR_MS, selector=dict(type="bar"))
    return date_axis(fig, asleep=asleep)


def mean(df: pd.DataFrame, col: str):
    return df[col].mean() if not df.empty and df[col].notna().any() else None


def delta(cur, before, digits=0):
    if prev is None or cur is None or before is None:
        return None
    return round(cur - before, digits)


# --------------------------------------------------------------------------- #
# Overview
# --------------------------------------------------------------------------- #

with tab_over:
    c = st.columns(6)
    v_score, v_sleep = mean(P.sleep, "score"), mean(P.sleep, "total_min")
    v_rhr, v_steps = mean(P.vitals, "resting_hr"), mean(P.vitals, "steps")
    pb = prev or P  # placeholder when no previous period
    c[0].metric("Sleep score", fmt.num(v_score), delta(v_score, mean(pb.sleep, "score"), 1),
                help="Average per night")
    c[1].metric("Sleep duration", fmt.hours_minutes(v_sleep),
                None if delta(v_sleep, mean(pb.sleep, "total_min")) is None
                else f"{delta(v_sleep, mean(pb.sleep, 'total_min')):+.0f} min",
                help="Average per night")
    c[2].metric("Resting HR", fmt.num(v_rhr, 0, "bpm"), delta(v_rhr, mean(pb.vitals, "resting_hr"), 1),
                delta_color="inverse", help="Daily average")
    c[3].metric("Steps", fmt.num(v_steps), delta(v_steps, mean(pb.vitals, "steps")), help="Daily average")
    c[4].metric("Sessions", len(P.activities),
                delta(len(P.activities), len(pb.activities)) if prev is not None else None)
    c[5].metric("Training time", fmt.hours_minutes(P.activities["duration_s"].sum() / 60),
                help="Total moving time of all activities")
    if prev is not None:
        st.caption(f"Deltas compare with the previous {n_days} days.")

    if not P.activities.empty:
        # Endurance kinds headline their distance, sports their time.
        kinds = kinds_in(P.activities)
        for row in (kinds[i:i + 5] for i in range(0, len(kinds), 5)):
            for col, k in zip(st.columns(5), row):
                g = P.activities[P.activities["kind"] == k.key]
                secs, km = g["duration_s"].sum(), g["distance_km"].sum()
                sub = f"{len(g)} session(s) · {fmt.hours_minutes(secs / 60)}"
                col.metric(k.label, fmt.num(km, 1, "km") if k.endurance and km else fmt.hours_minutes(secs / 60))
                col.caption(sub)

    left, right = st.columns(2)
    with left:
        if P.activities.empty:
            empty("No activities in the selected period.")
        else:
            wk = P.activities.groupby(["week", "kind"], as_index=False)["duration_s"].sum()
            fig = kind_bars(go.Figure(), wk, "week", "duration_s", "%{customdata}", scale=1 / 60,
                            customdata=fmt.duration)
            theme.layout(fig, "Weekly training time (min)").update_layout(barmode="stack")
            fig.update_xaxes(tickformat="W%V · %d %b", hoverformat="Week of %d %b %Y")
            plot(fig)
    with right:
        if P.vitals.empty:
            empty()
        else:
            plot(ts(P.vitals, "date", "steps", "Daily steps", STEPS, F.vitals, bars=True))

    left, right = st.columns(2)
    with left:
        if P.sleep.empty:
            empty()
        else:
            plot(ts(P.sleep, "asleep_at", "score", "Sleep score", INT, F.sleep, asleep=True))
    with right:
        if P.vitals.empty:
            empty()
        else:
            plot(ts(P.vitals, "date", "resting_hr", "Resting heart rate (bpm)", BPM, F.vitals))

    if not (P.meals.empty and P.medicines.empty):
        st.caption(f"🍝 {len(P.meals)} meals and 💊 {len(P.medicines)} medicine entries recorded in this period "
                   "— food analysis is coming later.")

# --------------------------------------------------------------------------- #
# Sleep
# --------------------------------------------------------------------------- #

with tab_sleep:
    S = P.sleep
    if S.empty:
        empty()
    else:
        c = st.columns(5)
        c[0].metric("Avg score", fmt.num(mean(S, "score")))
        c[1].metric("Avg duration", fmt.hours_minutes(mean(S, "total_min")))
        c[2].metric("Avg bedtime", fmt.clock(mean(S, "bed_min")))
        c[3].metric("Avg wake-up", fmt.clock(mean(S, "end_min")))
        c[4].metric("Avg wakes", fmt.num(mean(S, "wakes"), 1))

        fig = go.Figure()
        for i, (col, name) in enumerate(SLEEP_STAGES):
            fig.add_bar(x=S["asleep_at"], y=S[col] / 60, name=name, marker_color=theme.slot(i),
                        # the 2px gap would swallow bars only a few px wide over long periods
                        marker_line=theme.bar_gap_line() if len(S) <= 60 else dict(width=0), width=NIGHT_BAR_MS,
                        customdata=[fmt.hours_minutes(m) for m in S[col]],
                        hovertemplate="%{customdata}<extra>" + name + "</extra>")
        theme.layout(fig, "Sleep stages (hours)", height=340).update_layout(barmode="stack")
        plot(date_axis(fig, asleep=True))

        left, right = st.columns(2)
        with left:
            # Neutral bar = time in bed; the two ends are rated: bedtime vs 23:00, wake-up vs 08:00.
            wake_pos = S["bed_min"] + S["in_bed_min"]
            bed_s = quality.score(quality.RULES["bed_min"], S["bed_min"])
            wake_s = quality.score(quality.RULES["end_min"], S["end_min"])
            fig = go.Figure([
                go.Bar(x=S["asleep_at"], y=S["in_bed_min"], base=S["bed_min"], marker_color=theme.ink["grid"],
                       hoverinfo="skip", width=NIGHT_BAR_MS),
                go.Scatter(x=S["asleep_at"], y=S["bed_min"], mode="markers", name="Bedtime",
                           marker=dict(size=9, color=[quality_color(s, theme.slot(0)) for s in bed_s],
                                       line=dict(width=1.5, color=theme.ink["surface"])),
                           customdata=[[fmt.clock(m), quality.label(s)] for m, s in zip(S["bed_min"], bed_s)],
                           hovertemplate="asleep %{customdata[0]} · %{customdata[1]}<extra></extra>"),
                go.Scatter(x=S["asleep_at"], y=wake_pos, mode="markers", name="Wake-up",
                           marker=dict(size=9, symbol="diamond",
                                       color=[quality_color(s, theme.slot(0)) for s in wake_s],
                                       line=dict(width=1.5, color=theme.ink["surface"])),
                           customdata=[[fmt.clock(m), quality.label(s)] for m, s in zip(S["end_min"], wake_s)],
                           hovertemplate="awake %{customdata[0]} · %{customdata[1]}<extra></extra>"),
            ])
            for ys in (S["bed_min"], wake_pos):  # bedtime and wake-up trends
                gx, gy = trend.smooth(S["asleep_at"], ys)
                if len(gx):
                    fig.add_scatter(x=gx, y=gy, mode="lines", hoverinfo="skip", opacity=0.6,
                                    line=dict(color=theme.ink["secondary"], width=3, shape="spline", smoothing=0.6))
            theme.layout(fig, "Sleep window (● asleep vs 23:00, ◆ awake vs 08:00)", legend=False, hover="closest")
            clock_axis(fig, pd.concat([S["bed_min"], wake_pos]), step=120)
            fig.update_yaxes(autorange="reversed")
            plot(date_axis(fig, asleep=True))
        with right:
            plot(ts(S, "asleep_at", "total_min", "Sleep duration (h)", fmt.hours_minutes, F.sleep, asleep=True,
                    scale=1 / 60))

        left, right = st.columns(2)
        with left:
            plot(ts(S, "asleep_at", "score", "Sleep score", INT, F.sleep, asleep=True))
        with right:
            plot(ts(S, "asleep_at", "sleep_hrv", "Overnight HRV (ms)", MS, F.sleep, asleep=True))

        left, right = st.columns(2)
        with left:
            plot(ts(S, "asleep_at", "sleep_rhr", "Overnight resting HR (bpm)", BPM, F.sleep, asleep=True))
        with right:
            plot(ts(S, "asleep_at", "wakes", "Wakes per night", INT, F.sleep, bars=True, asleep=True))

        left, right = st.columns(2)
        with left:
            plot(ts(S, "asleep_at", "resp_rate", "Respiratory rate (br/min)", lambda v: fmt.num(v, 1, "br/min"),
                    F.sleep, height=260, asleep=True))
        with right:
            plot(ts(S, "asleep_at", "spo2_night", "Night SpO₂ (%)", lambda v: fmt.num(v, 0, "%"),
                    F.sleep, height=260, asleep=True))

        with st.expander("Table"):
            t = S.assign(
                date=S["date"].dt.date, bedtime=S["start_min"].map(fmt.clock),
                wake=S["end_min"].map(fmt.clock), total=S["total_min"].map(fmt.hours_minutes),
            )[["date", "score", "total", "bedtime", "wake", "deep", "rem", "light", "awake", "wakes",
               "sleep_hrv", "sleep_rhr", "resp_rate", "spo2_night"]]
            st.dataframe(t, hide_index=True, width="stretch")

# --------------------------------------------------------------------------- #
# Vitals
# --------------------------------------------------------------------------- #

with tab_vitals:
    V = P.vitals
    if V.empty:
        empty()
    else:
        c = st.columns(5)
        c[0].metric("Avg resting HR", fmt.num(mean(V, "resting_hr"), 0, "bpm"))
        c[1].metric("Avg steps", fmt.num(mean(V, "steps")))
        c[2].metric("Avg training load", fmt.num(mean(V, "training_load")))
        vo2 = V.dropna(subset=["vo2max"])
        c[3].metric("Latest VO₂max", fmt.num(vo2["vo2max"].iloc[-1], 1) if not vo2.empty else "–")
        pai = V.dropna(subset=["pai_total"])
        c[4].metric("Latest PAI", fmt.num(pai["pai_total"].iloc[-1], 0) if not pai.empty else "–")

        charts = [
            ("resting_hr", "Resting HR (bpm)", BPM, False),
            ("steps", "Steps", STEPS, True),
            ("training_load", "Training load", INT, False),  # no good/bad: stays neutral
            ("active_minutes", "Active minutes", lambda v: fmt.num(v, 0, "min"), True),
            ("vo2max", "VO₂max", lambda v: fmt.num(v, 1), False),
            ("pai_total", "PAI (total)", lambda v: fmt.num(v, 1), False),
        ]
        for pair in (charts[i:i + 2] for i in range(0, len(charts), 2)):
            for col, (key, title, value_fmt, bars) in zip(st.columns(2), pair):
                with col:
                    if V[key].dropna().empty:
                        empty(f"No {title} data in this period.")
                        continue
                    plot(ts(V, "date", key, title, value_fmt, F.vitals, bars=bars, height=260))

        B = P.hr_buckets
        if not B.empty:
            hm_opts = {"avg_hr": "Avg HR", "max_hr": "Max HR", "min_hr": "Min HR", "avg_hrv": "Avg HRV"}
            metric = st.segmented_control("Heatmap metric", list(hm_opts), default="avg_hr",
                                          format_func=hm_opts.get) or "avg_hr"
            unit = "ms" if "hrv" in metric else "bpm"
            grid = B.pivot_table(index="label", columns="date", values=metric, aggfunc="mean")
            order = B.drop_duplicates("label").sort_values("start_min")["label"]
            grid = grid.reindex(order)
            fig = go.Figure(go.Heatmap(
                z=grid.values, x=grid.columns, y=grid.index, colorscale=theme.seq_scale,
                xgap=2, ygap=2, hoverongaps=False,
                colorbar=dict(title=dict(text=unit), outlinewidth=0, thickness=12),
                hovertemplate="%{x|%a %d %b} · %{y}<br>%{z:.0f} " + unit + "<extra></extra>",
            ))
            theme.layout(fig, "Heart rate through the day", height=260, hover="closest", legend=False)
            fig.update_yaxes(autorange="reversed", showgrid=False)
            plot(date_axis(fig))

        with st.expander("Table"):
            st.dataframe(V.assign(date=V["date"].dt.date), hide_index=True, width="stretch")

# --------------------------------------------------------------------------- #
# Activities
# --------------------------------------------------------------------------- #

with tab_act:
    A_all = P.activities
    if A_all.empty:
        empty("No activities in the selected period.")
    else:
        present = [k.label for k in kinds_in(A_all)]
        chosen_kinds = st.multiselect("Activity types", present, default=present)
        A = A_all[A_all["kind_label"].isin(chosen_kinds)]

        if A.empty:
            empty("No activities of the selected types.")
        else:
            # ---- Summary per kind: endurance in distance units, sports in time + HR
            rows = []
            for k in kinds_in(A):
                g = A[A["kind"] == k.key]
                secs, km, elev = g["duration_s"].sum(), g["distance_km"].sum(), g["elevation_m"].sum()
                r = {"Type": k.label, "Sessions": len(g), "Time": fmt.duration(secs),
                     "Avg duration": fmt.duration(g["duration_s"].mean()),
                     "Distance": fmt.num(km, 1, "km") if km else "–",
                     "Elevation": fmt.num(elev, 0, "m") if k.endurance and elev else "–",
                     "Pace / speed": "–",
                     "Avg HR": fmt.num(g["avg_hr"].mean(), 0, "bpm"),
                     "Max HR": fmt.num(g["max_hr"].max(), 0, "bpm"),
                     "Avg effort": fmt.num(g["relative_effort"].mean())}
                if k.endurance and km:
                    r["Pace / speed"] = fmt.activity_speed({
                        "kind": k.key, "distance_km": km, "pace_s_km": secs / km,
                        "speed_kmh": km / (secs / 3600), "elev_pace": (secs / 60) / (elev / 100) if elev else None,
                    })
                rows.append(r)
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
            st.caption("Run, ride and hike are measured over distance (pace in min/km, speed in km/h, climb in "
                       "min/100m). Workout and court/team sports are measured over time and heart rate; their "
                       "distance, when recorded, is only distance covered.")

            left, right = st.columns(2)
            with left:
                end = A[A["kind"].map(lambda k: BY_KEY[k].endurance) & (A["distance_km"] > 0)]
                wk = end.groupby(["week", "kind"], as_index=False)["distance_km"].sum()
                if wk.empty:
                    empty("No run, ride or hike in the selection.")
                else:
                    fig = kind_bars(go.Figure(), wk, "week", "distance_km", "%{y:.1f} km")
                    theme.layout(fig, "Weekly distance (km)").update_layout(barmode="stack")
                    fig.update_xaxes(tickformat="W%V · %d %b", hoverformat="Week of %d %b %Y")
                    plot(fig)
            with right:
                fig = kind_dots(go.Figure(), A, "relative_effort", "effort %{y:.0f}")
                plot(date_axis(theme.layout(fig, "Relative effort per session", hover="closest")))

            # ---- Endurance trends, each in its own units
            run = A[(A["kind"] == RUN.key) & A["pace_s_km"].notna()]
            ride = A[(A["kind"] == RIDE.key) & A["speed_kmh"].notna()]
            hike = A[(A["kind"] == HIKE.key) & A["pace_s_km"].notna()]
            if not (run.empty and ride.empty and hike.empty):
                st.subheader("Run, ride & hike")
                # baselines: all-time sessions of the same kind
                hist = {k.key: F.activities[F.activities["kind"] == k.key] for k in (RUN, RIDE, HIKE)}
                trend_cols = st.columns(3)
                with trend_cols[0]:
                    if not run.empty:
                        fig = ts(run, "date", "pace_s_km", "Run pace", fmt.pace, hist["run"],
                                 rule="run_pace", height=260)
                        plot(pace_axis(fig, run["pace_s_km"]))
                        plot(ts(run, "date", "avg_hr", "Run average HR (bpm)", BPM, rule=None,
                                color=theme.kind_color(RUN.key), height=260))
                with trend_cols[1]:
                    if not ride.empty:
                        plot(ts(ride, "date", "speed_kmh", "Ride speed (km/h)", fmt.speed, hist["ride"],
                                rule="ride_speed", height=260))
                        plot(ts(ride, "date", "avg_hr", "Ride average HR (bpm)", BPM, rule=None,
                                color=theme.kind_color(RIDE.key), height=260))
                with trend_cols[2]:
                    if not hike.empty:
                        fig = ts(hike, "date", "pace_s_km", "Hike pace", fmt.pace, hist["hike"],
                                 rule="hike_pace", height=260)
                        plot(pace_axis(fig, hike["pace_s_km"]))
                        if hike["elev_pace"].notna().any():
                            plot(ts(hike, "date", "elev_pace", "Hike climb rate (min/100m elev)", fmt.elev_pace,
                                    hist["hike"], rule="hike_climb", height=260))

            # ---- Time-based sports (workout, soccer, padel, tennis, volleyball, other)
            sports = A[~A["kind"].map(lambda k: BY_KEY[k].endurance)]
            if not sports.empty:
                st.subheader("Workout & sports")
                left, right = st.columns(2)
                with left:
                    s = sports.assign(duration_min=sports["duration_s"] / 60)
                    fig = kind_dots(go.Figure(), s, "duration_min", "%{y:.0f} min")
                    plot(date_axis(theme.layout(fig, "Session duration (min)", height=280, hover="closest")))
                with right:
                    fig = kind_dots(go.Figure(), sports, "avg_hr", "avg %{y:.0f} bpm")
                    plot(date_axis(theme.layout(fig, "Session average HR (bpm)", height=280, hover="closest")))
                covered = sports[sports["distance_km"] > 0]
                if not covered.empty:
                    fig = kind_dots(go.Figure(), covered, "distance_km", "%{y:.1f} km covered")
                    plot(date_axis(theme.layout(fig, "Distance covered (km)", height=240, hover="closest")))

            # ---- Sessions table
            st.subheader("Sessions")
            endurance = A["kind"].map(lambda k: BY_KEY[k].endurance)
            has_cadence = A["kind"].map(lambda k: BY_KEY[k].cadence)
            table = pd.DataFrame({
                "Date": A["date"].dt.date, "Type": A["type"], "Name": A["name"],
                "Duration": A["duration_s"].map(fmt.duration),
                "Distance": A["distance_km"].map(lambda x: fmt.num(x, 1, "km") if x else "–"),
                "Pace / speed": [fmt.activity_speed(r) for _, r in A.iterrows()],
                "Elevation": A["elevation_m"].where(endurance).map(lambda x: fmt.num(x, 0, "m")),
                "Avg HR": A["avg_hr"].map(lambda x: fmt.num(x, 0)),
                "Min HR": A["min_hr"].map(lambda x: fmt.num(x, 0)),
                "Max HR": A["max_hr"].map(lambda x: fmt.num(x, 0)),
                "Cadence": A["avg_cadence"].where(has_cadence).map(lambda x: fmt.num(x, 0)),
                "Effort": A["relative_effort"].map(lambda x: fmt.num(x, 0)),
            }).iloc[::-1]
            st.dataframe(table, hide_index=True, width="stretch")

            # ---- Session detail
            st.subheader("Session detail")
            labels = A["label"].iloc[::-1].tolist()
            chosen = st.selectbox("Session", labels)
            act = A[A["label"] == chosen].iloc[0]
            kind = BY_KEY[act["kind"]]
            color = theme.kind_color(kind.key)
            if kind.endurance:
                metrics = [
                    ("Duration", fmt.duration(act["duration_s"])),
                    ("Distance", fmt.num(act["distance_km"], 2, "km") if act["distance_km"] else "–"),
                    ("Speed" if kind.speed == "speed" else "Pace", fmt.activity_speed(act).split(" · ")[0]),
                    ("Elevation", fmt.num(act["elevation_m"], 0, "m")),
                ]
                if kind.elev_pace:
                    metrics.append(("Climb rate", fmt.elev_pace(act["elev_pace"])))
                if kind.cadence:
                    metrics.append(("Cadence", fmt.num(act["avg_cadence"])))
                metrics += [("Avg / max HR", f"{fmt.num(act['avg_hr'])} / {fmt.num(act['max_hr'])}"),
                            ("Relative effort", fmt.num(act["relative_effort"]))]
            else:
                metrics = [("Duration", fmt.duration(act["duration_s"]))]
                if act["distance_km"] and pd.notna(act["distance_km"]):
                    metrics.append(("Distance covered", fmt.num(act["distance_km"], 2, "km")))
                metrics += [("Avg HR", fmt.num(act["avg_hr"], 0, "bpm")),
                            ("Min / max HR", f"{fmt.num(act['min_hr'])} / {fmt.num(act['max_hr'])}"),
                            ("Relative effort", fmt.num(act["relative_effort"]))]
            for col, (label, value) in zip(st.columns(len(metrics)), metrics):
                col.metric(label, value)
            st.caption(f"{act['type']} · {kind.label}")

            sp = P.splits[P.splits["activity_id"] == act["id"]] if kind.endurance else P.splits.iloc[0:0]
            ch = P.hr_chunks[P.hr_chunks["activity_id"] == act["id"]]
            if not sp.empty:
                left, right = st.columns(2)
                xs = sp["n"].astype(str).where(sp["km"] >= 1, sp["n"].astype(str) + f" ({sp['km'].iloc[-1]:.2f})")
                with left:
                    if kind.speed == "speed":
                        fig = go.Figure(go.Bar(x=xs, y=sp["speed_kmh"], marker_color=color,
                                               hovertemplate="km %{x}<br>%{y:.1f} km/h<extra></extra>"))
                        theme.layout(fig, "Speed per km (km/h)", legend=False)
                    else:
                        fig = go.Figure(go.Bar(x=xs, y=sp["pace_s_km"], marker_color=color,
                                               customdata=sp["pace_s_km"].map(fmt.pace),
                                               hovertemplate="km %{x}<br>%{customdata}<extra></extra>"))
                        theme.layout(fig, "Pace per km", legend=False)
                        pace_axis(fig, sp["pace_s_km"])
                        lo, hi = sp["pace_s_km"].min(), sp["pace_s_km"].max()
                        fig.update_yaxes(range=[hi + 30, max(lo - 30, 0)])
                    fig.update_xaxes(title_text="km", type="category")
                    plot(fig)
                with right:
                    fig = go.Figure(go.Scatter(
                        x=xs, y=sp["avg_hr"], mode="lines+markers", line=dict(color=color, width=2),
                        marker=dict(size=8, line=dict(width=2, color=theme.ink["surface"])),
                        customdata=sp["elev_diff"],
                        hovertemplate="km %{x}<br>%{y:.0f} bpm · elev %{customdata:+.1f} m<extra></extra>",
                    ))
                    theme.layout(fig, "Average HR per km (bpm)", legend=False)
                    fig.update_xaxes(title_text="km", type="category")
                    plot(fig)
            if not ch.empty:
                fig = go.Figure([
                    go.Scatter(x=ch["mid_min"], y=ch["max_hr"], mode="lines", line=dict(width=0),
                               showlegend=False, hoverinfo="skip"),
                    go.Scatter(x=ch["mid_min"], y=ch["min_hr"], mode="lines", line=dict(width=0),
                               fill="tonexty", fillcolor="rgba(137,135,129,0.2)", name="Min–max",
                               hoverinfo="skip"),
                    go.Scatter(x=ch["mid_min"], y=ch["avg_hr"], mode="lines+markers", name="Avg HR",
                               line=dict(color=color, width=2),
                               marker=dict(size=8, line=dict(width=2, color=theme.ink["surface"])),
                               customdata=list(zip(ch["start_s"].map(fmt.duration), ch["end_s"].map(fmt.duration),
                                                   ch["min_hr"], ch["max_hr"])),
                               hovertemplate="%{customdata[0]}–%{customdata[1]}<br>avg %{y:.0f} bpm "
                                             "(%{customdata[2]:.0f}–%{customdata[3]:.0f})<extra></extra>"),
                ])
                theme.layout(fig, "Heart rate over the session (bpm)", hover="closest")
                fig.update_xaxes(title_text="minutes")
                plot(fig)
            if sp.empty and ch.empty:
                st.caption("No splits or HR chunks for this session.")
