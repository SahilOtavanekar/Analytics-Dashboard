import altair as alt
import pandas as pd

from src.theme import CATEGORICAL, SEQUENTIAL_BLUE


def funnel_bar(df: pd.DataFrame, stage_col: str, value_col: str) -> alt.LayerChart:
    """Nested stages held in the given order, never value-sorted.

    Sorting by value would let a wider lower stage jump above a narrower upper one
    and imply a journey that doesn't exist, so the row order carries the sequence.
    Each bar is labelled with its count and its share of the first stage.
    """
    data = df.copy()
    base = float(data[value_col].iloc[0]) if len(data) else 0.0
    data["PCT"] = data[value_col] / base * 100 if base else 0.0
    data["LABEL"] = [f"{int(v):,}  ({p:.1f}%)" for v, p in zip(data[value_col], data["PCT"])]
    order = data[stage_col].astype(str).tolist()

    bars = alt.Chart(data).mark_bar(color=SEQUENTIAL_BLUE).encode(
        x=alt.X(f"{value_col}:Q", title="Sessions"),
        y=alt.Y(f"{stage_col}:N", title=None, sort=order),
        tooltip=[
            alt.Tooltip(f"{stage_col}:N", title="Stage"),
            alt.Tooltip(f"{value_col}:Q", title="Sessions", format=","),
            alt.Tooltip("PCT:Q", title="% of first stage", format=".1f"),
        ],
    )
    labels = alt.Chart(data).mark_text(align="left", dx=6).encode(
        x=alt.X(f"{value_col}:Q"),
        y=alt.Y(f"{stage_col}:N", sort=order),
        text="LABEL:N",
    )
    return (bars + labels).properties(height=alt.Step(44))


def top_events_bar(df: pd.DataFrame, name_col: str, value_col: str, x_title: str = "Events") -> alt.Chart:
    """Ranked magnitude: single hue, sorted, axis + tooltip carry values."""
    return (
        alt.Chart(df)
        .mark_bar(color=SEQUENTIAL_BLUE)
        .encode(
            x=alt.X(f"{value_col}:Q", title=x_title),
            y=alt.Y(f"{name_col}:N", title=None, sort="-x"),
            tooltip=[
                alt.Tooltip(f"{name_col}:N", title=name_col.replace("_", " ").title()),
                alt.Tooltip(f"{value_col}:Q", title=x_title, format=","),
            ],
        )
    )


def share_stacked_bar(
    df: pd.DataFrame, name_col: str, value_col: str, max_segments: int = 6
) -> alt.LayerChart:
    """Part-to-whole: single horizontal 100% bar, categorical colors, Other fold past max_segments."""
    ranked = df.sort_values(value_col, ascending=False).reset_index(drop=True)
    if len(ranked) > max_segments:
        head = ranked.iloc[: max_segments - 1]
        other_total = ranked.iloc[max_segments - 1 :][value_col].sum()
        ranked = pd.concat(
            [head, pd.DataFrame({name_col: ["Other"], value_col: [other_total]})],
            ignore_index=True,
        )

    ranked["share"] = ranked[value_col] / ranked[value_col].sum()
    # Explicit start/end beats Vega's normalize stack here - the label layer needs
    # segment midpoints, and computing them in pandas keeps both layers in step.
    ranked["seg_end"] = ranked["share"].cumsum()
    ranked["seg_start"] = ranked["seg_end"] - ranked["share"]
    ranked["seg_mid"] = (ranked["seg_start"] + ranked["seg_end"]) / 2
    ranked["pct"] = ranked["share"].map(lambda s: f"{s * 100:.0f}%" if s * 100 >= 8 else "")
    ranked["tip"] = ranked["share"].map(lambda s: f"{s * 100:.1f}%")

    order = ranked[name_col].astype(str).tolist()
    bars = (
        alt.Chart(ranked)
        .mark_bar(stroke="#ffffff", strokeWidth=2)
        .encode(
            x=alt.X("seg_start:Q", title=None, axis=None, scale=alt.Scale(domain=[0, 1])),
            x2="seg_end:Q",
            color=alt.Color(
                f"{name_col}:N",
                scale=alt.Scale(domain=order, range=CATEGORICAL[: len(order)]),
                legend=alt.Legend(orient="bottom", title=None, columns=3),
            ),
            tooltip=[alt.Tooltip(f"{name_col}:N", title="Type"), alt.Tooltip("tip:N", title="Share")],
        )
    )
    labels = (
        alt.Chart(ranked)
        .mark_text(color="#ffffff", fontWeight="bold")
        .encode(x=alt.X("seg_mid:Q", scale=alt.Scale(domain=[0, 1])), text="pct:N")
    )
    return (bars + labels).properties(height=180)


def trend_line(df: pd.DataFrame, x_col: str, y_col: str, y_title: str) -> alt.Chart:
    """Trend over time: single series, thin line, markers only when points are sparse."""
    base = alt.Chart(df).encode(
        x=alt.X(f"{x_col}:T", title=None),
        y=alt.Y(f"{y_col}:Q", title=y_title),
        tooltip=[
            alt.Tooltip(f"{x_col}:T", title="Date", format="%b %d"),
            alt.Tooltip(f"{y_col}:Q", title=y_title, format=","),
        ],
    )
    line = base.mark_line(color=SEQUENTIAL_BLUE, strokeWidth=2)
    if len(df) <= 14:
        return line + base.mark_point(color=SEQUENTIAL_BLUE, size=45, filled=True)
    return line


def distribution_histogram(
    df: pd.DataFrame,
    value_col: str,
    x_title: str,
    y_title: str = "Sessions",
    nbins: int = 30,
    clip_percentile: float = 0.95,
) -> alt.Chart:
    """Magnitude distribution, binned to the clip_percentile.

    A long tail of outliers (a handful of stale sessions) would otherwise crush
    the chart into one bar; the full range stays available in the table view.
    """
    values = df[value_col].astype(float)
    lower = min(values.min(), 0.0)
    upper = max(values.quantile(clip_percentile), lower + 1)

    return (
        alt.Chart(pd.DataFrame({value_col: values}))
        .mark_bar(color=SEQUENTIAL_BLUE, stroke="#ffffff", strokeWidth=1)
        .encode(
            x=alt.X(
                f"{value_col}:Q",
                bin=alt.Bin(extent=[lower, upper], maxbins=nbins),
                title=x_title,
                scale=alt.Scale(domain=[lower, upper]),
            ),
            y=alt.Y("count():Q", title=y_title),
            tooltip=[alt.Tooltip("count():Q", title=y_title, format=",")],
        )
    )
