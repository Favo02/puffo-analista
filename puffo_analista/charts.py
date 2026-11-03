"""Plotly styling and chart builders.

Colors come from a validated categorical palette (fixed slot order, separate
light/dark steps). Activity kinds and sleep stages always map to the same slot,
so filtering never repaints a series. The eight kinds use the eight slots in
order; anything else folds into a gray "Other".
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go

from . import fmt
from .activities import BY_KEY

PALETTE = {
    "light": ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"],
    "dark": ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"],
}
SEQ_BLUE = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]
INK = {
    "light": dict(primary="#0b0b0b", secondary="#52514e", muted="#898781", grid="#e1e0d9",
                  axis="#c3c2b7", surface="#ffffff"),
    "dark": dict(primary="#f0efec", secondary="#c3c2b7", muted="#898781", grid="#2c2c2a",
                 axis="#383835", surface="#0e1117"),
}

SLEEP_STAGES = [("deep", "Deep"), ("rem", "REM"), ("light", "Light"), ("awake", "Awake")]


class Theme:
    def __init__(self, mode: str):
        self.mode = mode if mode in PALETTE else "light"
        self.pal = PALETTE[self.mode]
        self.ink = INK[self.mode]

    def slot(self, i: int) -> str:
        return self.pal[i % len(self.pal)]

    def kind_color(self, kind_key: str) -> str:
        """Fixed color per activity kind; 'Other' is neutral gray (never a 9th hue)."""
        slot = BY_KEY[kind_key].slot
        return self.ink["muted"] if slot is None else self.slot(slot)

    @property
    def seq_scale(self):
        steps = SEQ_BLUE if self.mode == "light" else SEQ_BLUE[::-1]
        return [[i / (len(steps) - 1), c] for i, c in enumerate(steps)]

    def layout(self, fig: go.Figure, title: str | None = None, height: int = 300,
               hover: str = "x unified", legend: bool = True) -> go.Figure:
        ink = self.ink
        axis = dict(gridcolor=ink["grid"], linecolor=ink["axis"], zerolinecolor=ink["axis"], automargin=True,
                    tickfont=dict(color=ink["muted"], size=11), title_font=dict(color=ink["secondary"], size=12))
        fig.update_layout(
            title=dict(text=title, font=dict(size=14, color=ink["primary"]), x=0, xanchor="left") if title else None,
            height=height,
            margin=dict(l=8, r=8, t=40 if title else 10, b=8),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font=dict(family="system-ui, -apple-system, 'Segoe UI', sans-serif", color=ink["secondary"]),
            hovermode=hover,
            hoverlabel=dict(bgcolor=ink["surface"], font=dict(color=ink["primary"]), bordercolor=ink["axis"]),
            showlegend=legend,
            legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="right", x=1, traceorder="normal",
                        font=dict(color=ink["secondary"])),
            barcornerradius=4,
            bargap=0.3,
            xaxis=dict(**axis, showgrid=False),
            yaxis=dict(**axis, showgrid=True),
        )
        return fig

    def bar_gap_line(self):
        """2px surface gap between stacked segments / adjacent bars."""
        return dict(width=2, color=self.ink["surface"])


def date_axis(fig: go.Figure, asleep: bool = False):
    """Date x axis. `asleep=True` for charts keyed by fall-asleep time (hover shows it)."""
    fig.update_xaxes(tickformat="%d %b", hoverformat="Asleep %a %d %b, %H:%M" if asleep else "%a %d %b %Y")
    xs = pd.to_datetime(pd.concat([pd.Series(t.x) for t in fig.data if t.x is not None]), errors="coerce").dropna()
    if not xs.empty and (xs.max() - xs.min()).days < 7:
        # few days of data: never tick below one day (plotly would otherwise show hours)
        fig.update_xaxes(dtick=86_400_000, range=[xs.min() - pd.Timedelta(hours=18), xs.max() + pd.Timedelta(hours=18)])
    return fig


def clock_axis(fig: go.Figure, values: pd.Series, axis="y", step=60):
    """Label a minutes-from-midnight axis as HH:MM."""
    v = values.dropna()
    if v.empty:
        return fig
    lo, hi = int(v.min() // step * step - step), int(v.max() // step * step + 2 * step)
    ticks = list(range(lo, hi, step))
    getattr(fig, f"update_{axis}axes")(tickvals=ticks, ticktext=[fmt.clock(t) for t in ticks])
    return fig


def pace_axis(fig: go.Figure, values: pd.Series, axis="y"):
    """Label a sec/km axis as M:SS, faster on top."""
    v = values.dropna()
    if v.empty:
        return fig
    step = 30 if v.max() - v.min() < 240 else 60
    lo, hi = int(v.min() // step * step - step), int(v.max() // step * step + 2 * step)
    ticks = list(range(max(lo, 0), hi, step))
    getattr(fig, f"update_{axis}axes")(
        tickvals=ticks, ticktext=[fmt.pace(t).replace(" /km", "") for t in ticks],
        autorange="reversed", title_text="min/km",
    )
    return fig


def line(theme: Theme, df: pd.DataFrame, x: str, y: str, name: str, color: str,
         hover_fmt: str = "%{y}", markers: bool = True) -> go.Scatter:
    return go.Scatter(
        x=df[x], y=df[y], name=name, mode="lines+markers" if markers else "lines",
        line=dict(color=color, width=2), marker=dict(size=8, line=dict(width=2, color=theme.ink["surface"])),
        connectgaps=True, hovertemplate=f"{hover_fmt}<extra>{name}</extra>",
    )
