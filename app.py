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
from puffo_analista.charts import SLEEP_STAGES, Theme, clock_axis, date_axis, line, pace_axis
from puffo_analista.frames import Frames, build
from puffo_analista.parse import Dataset, load_folder

st.set_page_config(page_title="puffo-analista", page_icon="📈", layout="wide")

DEFAULT_DATA_DIR = Path(os.environ.get("PUFFO_DATA_DIR", Path(__file__).parent / "data"))
PLOT_CFG = {"displayModeBar": False}


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #


def _folder_signature(folder: Path) -> tuple:
    return tuple(sorted((p.name, p.stat().st_mtime_ns) for p in folder.glob("*.md")))


@st.cache_data(show_spinner="Parsing exports…")
def load(folder: str, _sig: tuple) -> tuple[Dataset, Frames]:
    ds = load_folder(Path(folder))
    return ds, build(ds)


def plot(fig: go.Figure):
    st.plotly_chart(fig, width="stretch", config=PLOT_CFG, theme=None)


def empty(msg="No data in the selected period."):
    st.info(msg, icon="ℹ️")


# --------------------------------------------------------------------------- #
# Sidebar
# --------------------------------------------------------------------------- #

with st.sidebar:
    st.header("puffo-analista")
    folder = Path(st.text_input("Data folder", str(DEFAULT_DATA_DIR)))
    if not folder.is_dir():
        st.error(f"Folder not found: {folder}")
        st.stop()
    ds, F = load(str(folder), _folder_signature(folder))

dates = F.all_dates()
if dates.empty:
    st.title("puffo-analista")
    st.warning(f"No data found in `{folder}`. Paste your monthly `.md` exports there.")
    for i in ds.issues:
        (st.error if i.level == "error" else st.warning)(f"**{i.file}** — {i.message}")
    st.stop()

first, last = dates.min().date(), dates.max().date()
months = sorted({d.strftime("%Y-%m") for d in dates}, reverse=True)

with st.sidebar:
    period = st.selectbox("Period", ["All time", *months, "Custom range"])
    if period == "All time":
        start, end = first, last
    elif period == "Custom range":
        rng = st.date_input("Range", (first, last), min_value=first, max_value=last, format="YYYY-MM-DD")
        start, end = (rng[0], rng[1]) if len(rng) == 2 else (rng[0], rng[0])
    else:
        p = pd.Period(period, "M")
        start, end = p.start_time.date(), p.end_time.date()
    st.caption(f"{start} → {end}")
    st.divider()
    st.caption(f"{len(ds.files)} file(s) loaded")
    for f in ds.files:
        st.caption(f"• {f.path.name}")

theme = Theme(getattr(getattr(st.context, "theme", None), "type", None) or "light")
P = F.filter(start, end)
n_days = (end - start).days + 1
prev = F.filter(start - timedelta(days=n_days), start - timedelta(days=1)) if period != "All time" else None

# --------------------------------------------------------------------------- #
# Header + issues
# --------------------------------------------------------------------------- #

st.title("puffo-analista")

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

ALL_TYPES = sorted(F.activities["type"].unique().tolist())


def act_color(t: str) -> str:
    return theme.activity_color(t, ALL_TYPES)


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
        by_type = P.activities.groupby("type").agg(
            n=("id", "count"), km=("distance_km", "sum"), secs=("duration_s", "sum"),
            elev=("elevation_m", "sum"),
        )
        cols = st.columns(max(len(by_type), 1))
        for col, (t, r) in zip(cols, by_type.iterrows()):
            sub = f"{int(r.n)} session(s) · {fmt.duration(r.secs)}"
            col.metric(t, fmt.num(r.km, 1, "km") if r.km else fmt.duration(r.secs), help=sub)
            col.caption(sub)

    left, right = st.columns(2)
    with left:
        if P.activities.empty:
            empty("No activities in the selected period.")
        else:
            wk = P.activities.groupby(["week", "type"], as_index=False)["duration_s"].sum()
            fig = go.Figure()
            for t in ALL_TYPES:
                d = wk[wk["type"] == t]
                if d.empty:
                    continue
                fig.add_bar(x=d["week"], y=d["duration_s"] / 60, name=t, marker_color=act_color(t),
                            marker_line=theme.bar_gap_line(),
                            customdata=[fmt.duration(s) for s in d["duration_s"]],
                            hovertemplate="%{customdata}<extra>" + t + "</extra>")
            theme.layout(fig, "Weekly training time (min)").update_layout(barmode="stack")
            fig.update_xaxes(tickformat="W%V · %d %b", hoverformat="Week of %d %b %Y")
            plot(fig)
    with right:
        if P.vitals.empty:
            empty()
        else:
            fig = go.Figure(go.Bar(x=P.vitals["date"], y=P.vitals["steps"], marker_color=theme.slot(0),
                                   hovertemplate="%{y:,} steps<extra></extra>"))
            plot(date_axis(theme.layout(fig, "Daily steps", legend=False)))

    left, right = st.columns(2)
    with left:
        if P.sleep.empty:
            empty()
        else:
            fig = go.Figure(line(theme, P.sleep, "asleep_at", "score", "Sleep score", theme.slot(0)))
            plot(date_axis(theme.layout(fig, "Sleep score", legend=False), asleep=True))
    with right:
        if P.vitals.empty:
            empty()
        else:
            fig = go.Figure(line(theme, P.vitals, "date", "resting_hr", "Resting HR", theme.slot(0),
                                 "%{y:.0f} bpm"))
            plot(date_axis(theme.layout(fig, "Resting heart rate (bpm)", legend=False)))

    if not P.meals.empty:
        st.caption(f"🍝 {len(P.meals)} meals recorded in this period — food analysis is coming later.")

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
                        marker_line=theme.bar_gap_line(),
                        customdata=[fmt.hours_minutes(m) for m in S[col]],
                        hovertemplate="%{customdata}<extra>" + name + "</extra>")
        theme.layout(fig, "Sleep stages (hours)", height=340).update_layout(barmode="stack")
        plot(date_axis(fig, asleep=True))

        left, right = st.columns(2)
        with left:
            fig = go.Figure(go.Bar(
                x=S["asleep_at"], y=S["in_bed_min"], base=S["bed_min"], marker_color=theme.slot(0),
                customdata=list(zip([fmt.clock(m) for m in S["bed_min"]], [fmt.clock(m) for m in S["end_min"]])),
                hovertemplate="%{customdata[0]} → %{customdata[1]}<extra></extra>",
            ))
            theme.layout(fig, "Sleep window", legend=False)
            clock_axis(fig, pd.concat([S["bed_min"], S["bed_min"] + S["in_bed_min"]]), step=120)
            fig.update_yaxes(autorange="reversed")
            plot(date_axis(fig, asleep=True))
        with right:
            fig = go.Figure(line(theme, S, "asleep_at", "score", "Score", theme.slot(0)))
            plot(date_axis(theme.layout(fig, "Sleep score", legend=False), asleep=True))

        left, right = st.columns(2)
        with left:
            fig = go.Figure(line(theme, S, "asleep_at", "sleep_hrv", "HRV", theme.slot(0), "%{y:.0f} ms"))
            plot(date_axis(theme.layout(fig, "Overnight HRV (ms)", legend=False), asleep=True))
        with right:
            fig = go.Figure(line(theme, S, "asleep_at", "sleep_rhr", "RHR", theme.slot(0), "%{y:.0f} bpm"))
            plot(date_axis(theme.layout(fig, "Overnight resting HR (bpm)", legend=False), asleep=True))

        left, mid, right = st.columns(3)
        with left:
            fig = go.Figure(go.Bar(x=S["asleep_at"], y=S["wakes"], marker_color=theme.slot(0),
                                   hovertemplate="%{y:.0f} wakes<extra></extra>"))
            plot(date_axis(theme.layout(fig, "Wakes per night", height=240, legend=False), asleep=True))
        with mid:
            fig = go.Figure(line(theme, S, "asleep_at", "resp_rate", "Resp. rate", theme.slot(0), "%{y:.1f} br/min"))
            plot(date_axis(theme.layout(fig, "Respiratory rate (br/min)", height=240, legend=False), asleep=True))
        with right:
            fig = go.Figure(line(theme, S, "asleep_at", "spo2_night", "SpO₂", theme.slot(0), "%{y:.0f}%"))
            plot(date_axis(theme.layout(fig, "Night SpO₂ (%)", height=240, legend=False), asleep=True))

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
            ("resting_hr", "Resting HR (bpm)", "%{y:.0f} bpm", "line"),
            ("steps", "Steps", "%{y:,} steps", "bar"),
            ("training_load", "Training load", "%{y:.0f}", "line"),
            ("active_minutes", "Active minutes", "%{y:.0f} min", "bar"),
            ("vo2max", "VO₂max", "%{y:.1f}", "line"),
            ("pai_total", "PAI (total)", "%{y:.1f}", "line"),
        ]
        for pair in (charts[i:i + 2] for i in range(0, len(charts), 2)):
            for col, (key, title, hv, kind) in zip(st.columns(2), pair):
                with col:
                    d = V.dropna(subset=[key])
                    if d.empty:
                        empty(f"No {title} data in this period.")
                        continue
                    if kind == "bar":
                        fig = go.Figure(go.Bar(x=d["date"], y=d[key], marker_color=theme.slot(0),
                                               hovertemplate=hv + "<extra></extra>"))
                    else:
                        fig = go.Figure(line(theme, d, "date", key, title, theme.slot(0), hv))
                    plot(date_axis(theme.layout(fig, title, height=260, legend=False)))

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
        present = [t for t in ALL_TYPES if t in set(A_all["type"])]
        types = st.multiselect("Activity types", present, default=present)
        A = A_all[A_all["type"].isin(types)]

        if A.empty:
            empty("No activities of the selected types.")
        else:
            # Summary per type
            rows = []
            for t, g in A.groupby("type"):
                secs, km, elev = g["duration_s"].sum(), g["distance_km"].sum(), g["elevation_m"].sum()
                r = {"Type": t, "Sessions": len(g), "Time": fmt.duration(secs),
                     "Distance": fmt.num(km, 1, "km") if km else "–",
                     "Elevation": fmt.num(elev, 0, "m") if elev else "–",
                     "Avg HR": fmt.num(g["avg_hr"].mean(), 0, "bpm")}
                if km:
                    agg = {"type": t, "distance_km": km, "pace_s_km": secs / km, "speed_kmh": km / (secs / 3600),
                           "elev_pace": (secs / 60) / (elev / 100) if elev else None}
                    r["Pace / speed"] = fmt.activity_speed(agg)
                else:
                    r["Pace / speed"] = "–"
                rows.append(r)
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

            left, right = st.columns(2)
            with left:
                wk = A[A["distance_km"] > 0].groupby(["week", "type"], as_index=False)["distance_km"].sum()
                if wk.empty:
                    empty("No distance-based activities.")
                else:
                    fig = go.Figure()
                    for t in ALL_TYPES:
                        d = wk[wk["type"] == t]
                        if not d.empty:
                            fig.add_bar(x=d["week"], y=d["distance_km"], name=t, marker_color=act_color(t),
                                        marker_line=theme.bar_gap_line(),
                                        hovertemplate="%{y:.1f} km<extra>" + t + "</extra>")
                    theme.layout(fig, "Weekly distance (km)").update_layout(barmode="stack")
                    fig.update_xaxes(tickformat="W%V · %d %b", hoverformat="Week of %d %b %Y")
                    plot(fig)
            with right:
                fig = go.Figure()
                for t in ALL_TYPES:
                    d = A[A["type"] == t]
                    if not d.empty:
                        fig.add_scatter(
                            x=d["date"], y=d["relative_effort"], name=t, mode="markers",
                            marker=dict(size=10, color=act_color(t), line=dict(width=2, color=theme.ink["surface"])),
                            customdata=d["name"], hovertemplate="%{customdata}<br>effort %{y:.0f}<extra>" + t + "</extra>",
                        )
                plot(date_axis(theme.layout(fig, "Relative effort per session", hover="closest")))

            # Trends per type, in the type's own units
            trend_cols = st.columns(3)
            run = A[(A["type"] == "Run") & A["pace_s_km"].notna()]
            ride = A[(A["type"] == "Ride") & A["speed_kmh"].notna()]
            hike = A[(A["type"] == "Hike") & A["pace_s_km"].notna()]
            with trend_cols[0]:
                if not run.empty:
                    fig = go.Figure(line(theme, run, "date", "pace_s_km", "Pace", act_color("Run"), "%{customdata}"))
                    fig.data[0].customdata = run["pace_s_km"].map(fmt.pace)
                    theme.layout(fig, "Run pace", height=260, legend=False)
                    plot(date_axis(pace_axis(fig, run["pace_s_km"])))
                    fig = go.Figure(line(theme, run, "date", "avg_hr", "Avg HR", act_color("Run"), "%{y:.0f} bpm"))
                    plot(date_axis(theme.layout(fig, "Run average HR (bpm)", height=260, legend=False)))
            with trend_cols[1]:
                if not ride.empty:
                    fig = go.Figure(line(theme, ride, "date", "speed_kmh", "Speed", act_color("Ride"), "%{y:.1f} km/h"))
                    plot(date_axis(theme.layout(fig, "Ride speed (km/h)", height=260, legend=False)))
                    fig = go.Figure(line(theme, ride, "date", "avg_hr", "Avg HR", act_color("Ride"), "%{y:.0f} bpm"))
                    plot(date_axis(theme.layout(fig, "Ride average HR (bpm)", height=260, legend=False)))
            with trend_cols[2]:
                if not hike.empty:
                    fig = go.Figure(line(theme, hike, "date", "pace_s_km", "Pace", act_color("Hike"), "%{customdata}"))
                    fig.data[0].customdata = hike["pace_s_km"].map(fmt.pace)
                    theme.layout(fig, "Hike pace", height=260, legend=False)
                    plot(date_axis(pace_axis(fig, hike["pace_s_km"])))
                    h2 = hike.dropna(subset=["elev_pace"])
                    if not h2.empty:
                        fig = go.Figure(line(theme, h2, "date", "elev_pace", "Climb", act_color("Hike"),
                                             "%{y:.1f} min/100m"))
                        plot(date_axis(theme.layout(fig, "Hike climb rate (min/100m elev)", height=260, legend=False)))

            # Sessions table
            st.subheader("Sessions")
            table = pd.DataFrame({
                "Date": A["date"].dt.date, "Type": A["type"], "Name": A["name"],
                "Duration": A["duration_s"].map(fmt.duration),
                "Distance": A["distance_km"].map(lambda x: fmt.num(x, 1, "km") if x else "–"),
                "Pace / speed": [fmt.activity_speed(r) for _, r in A.iterrows()],
                "Elevation": A["elevation_m"].map(lambda x: fmt.num(x, 0, "m")),
                "Avg HR": A["avg_hr"].map(lambda x: fmt.num(x, 0)),
                "Max HR": A["max_hr"].map(lambda x: fmt.num(x, 0)),
                "Cadence": A["avg_cadence"].map(lambda x: fmt.num(x, 0)),
                "Effort": A["relative_effort"].map(lambda x: fmt.num(x, 0)),
            }).iloc[::-1]
            st.dataframe(table, hide_index=True, width="stretch")

            # Session detail
            st.subheader("Session detail")
            labels = A["label"].iloc[::-1].tolist()
            chosen = st.selectbox("Session", labels)
            act = A[A["label"] == chosen].iloc[0]
            color = act_color(act["type"])
            c = st.columns(5)
            c[0].metric("Duration", fmt.duration(act["duration_s"]))
            c[1].metric("Distance", fmt.num(act["distance_km"], 2, "km") if act["distance_km"] else "–")
            c[2].metric("Pace / speed", fmt.activity_speed(act))
            c[3].metric("Avg / max HR", f"{fmt.num(act['avg_hr'])} / {fmt.num(act['max_hr'])}")
            c[4].metric("Relative effort", fmt.num(act["relative_effort"]))

            sp = P.splits[P.splits["activity_id"] == act["id"]]
            ch = P.hr_chunks[P.hr_chunks["activity_id"] == act["id"]]
            if not sp.empty:
                left, right = st.columns(2)
                xs = sp["n"].astype(str).where(sp["km"] >= 1, sp["n"].astype(str) + f" ({sp['km'].iloc[-1]:.2f})")
                with left:
                    if act["type"] == "Ride":
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
