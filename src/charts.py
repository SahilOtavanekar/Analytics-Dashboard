import pandas as pd
import plotly.graph_objects as go

from src.theme import (
    AXIS,
    CATEGORICAL,
    FONT_FAMILY,
    GRIDLINE,
    INK_MUTED,
    INK_PRIMARY,
    SEQUENTIAL_BLUE,
    SURFACE,
)


def _base_layout(**overrides) -> dict:
    layout = dict(
        plot_bgcolor=SURFACE,
        paper_bgcolor=SURFACE,
        font=dict(family=FONT_FAMILY, color=INK_PRIMARY),
        margin=dict(l=8, r=8, t=8, b=8),
    )
    layout.update(overrides)
    return layout


def top_events_bar(df: pd.DataFrame, name_col: str, value_col: str) -> go.Figure:
    """Ranked magnitude: single hue, sorted, no per-bar labels (axis + tooltip carry values)."""
    ordered = df.sort_values(value_col, ascending=True)
    fig = go.Figure(
        go.Bar(
            x=ordered[value_col],
            y=ordered[name_col],
            orientation="h",
            marker_color=SEQUENTIAL_BLUE,
            hovertemplate="%{y}: %{x:,}<extra></extra>",
        )
    )
    fig.update_layout(
        **_base_layout(
            xaxis=dict(title="Events", gridcolor=GRIDLINE, linecolor=AXIS, tickfont=dict(color=INK_MUTED)),
            yaxis=dict(title=None, linecolor=AXIS, tickfont=dict(color=INK_MUTED)),
            showlegend=False,
        )
    )
    return fig


def share_stacked_bar(df: pd.DataFrame, name_col: str, value_col: str, max_segments: int = 6) -> go.Figure:
    """Part-to-whole: single horizontal 100% stacked bar, categorical colors, Other fold past max_segments."""
    ranked = df.sort_values(value_col, ascending=False).reset_index(drop=True)
    if len(ranked) > max_segments:
        head = ranked.iloc[: max_segments - 1]
        other_total = ranked.iloc[max_segments - 1 :][value_col].sum()
        ranked = pd.concat(
            [head, pd.DataFrame({name_col: ["Other"], value_col: [other_total]})],
            ignore_index=True,
        )
    total = ranked[value_col].sum()
    ranked["share"] = ranked[value_col] / total

    fig = go.Figure()
    for i, row in ranked.iterrows():
        pct = row["share"] * 100
        fig.add_trace(
            go.Bar(
                x=[row["share"]],
                y=["Share"],
                orientation="h",
                name=str(row[name_col]),
                marker=dict(color=CATEGORICAL[i % len(CATEGORICAL)], line=dict(color=SURFACE, width=2)),
                text=f"{pct:.0f}%" if pct >= 8 else None,
                textposition="inside",
                insidetextanchor="middle",
                textfont=dict(color="#ffffff"),
                hovertemplate=f"{row[name_col]}: {pct:.1f}%<extra></extra>",
            )
        )
    fig.update_layout(
        **_base_layout(
            barmode="stack",
            xaxis=dict(visible=False, range=[0, 1]),
            yaxis=dict(visible=False),
            legend=dict(orientation="h", yanchor="bottom", y=-0.35),
            height=180,
        )
    )
    return fig


def trend_line(df: pd.DataFrame, x_col: str, y_col: str, y_title: str) -> go.Figure:
    """Trend over time: single series, thin line, hairline grid, unified hover."""
    mode = "lines+markers" if len(df) <= 14 else "lines"
    fig = go.Figure(
        go.Scatter(
            x=df[x_col],
            y=df[y_col],
            mode=mode,
            line=dict(color=SEQUENTIAL_BLUE, width=2),
            marker=dict(color=SEQUENTIAL_BLUE, size=6),
            hovertemplate="%{x|%b %d}: %{y:,}<extra></extra>",
        )
    )
    fig.update_layout(
        **_base_layout(
            xaxis=dict(title=None, gridcolor=GRIDLINE, linecolor=AXIS, tickfont=dict(color=INK_MUTED)),
            yaxis=dict(title=y_title, gridcolor=GRIDLINE, linecolor=AXIS, tickfont=dict(color=INK_MUTED)),
            hovermode="x unified",
            showlegend=False,
        )
    )
    return fig


def distribution_histogram(
    df: pd.DataFrame,
    value_col: str,
    x_title: str,
    y_title: str = "Sessions",
    nbins: int = 30,
    clip_percentile: float = 0.95,
) -> go.Figure:
    """Magnitude distribution: single hue, hairline grid.

    View is zoomed to the clip_percentile so a long tail of outliers (e.g. a
    handful of stale sessions) doesn't crush the chart into one bar; all data
    still contributes to the bins, and the full range remains in the table view.
    """
    values = df[value_col].astype(float)
    lower = min(values.min(), 0.0)
    upper = max(values.quantile(clip_percentile), lower + 1)
    bin_size = (upper - lower) / nbins
    fig = go.Figure(
        go.Histogram(
            x=values,
            xbins=dict(start=lower, end=upper, size=bin_size),
            marker=dict(color=SEQUENTIAL_BLUE, line=dict(color=SURFACE, width=1)),
            hovertemplate=f"{x_title}: %{{x}}<br>{y_title}: %{{y:,}}<extra></extra>",
        )
    )
    fig.update_layout(
        **_base_layout(
            xaxis=dict(
                title=x_title,
                gridcolor=GRIDLINE,
                linecolor=AXIS,
                tickfont=dict(color=INK_MUTED),
                range=[lower, upper],
            ),
            yaxis=dict(title=y_title, gridcolor=GRIDLINE, linecolor=AXIS, tickfont=dict(color=INK_MUTED)),
            showlegend=False,
            bargap=0.05,
        )
    )
    return fig
