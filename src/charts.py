import altair as alt
import pandas as pd

from src.theme import CATEGORICAL, SEQUENTIAL_BLUE


def _ordered_labelled_bar(df, label_col, value_col, x_title, denominator, pct_title, step):
    """Horizontal bars held in the order supplied, labelled with count and share.

    Row order carries meaning in both callers - funnel sequence, duration bands - so
    value-sorting is deliberately not applied anywhere here.
    """
    data = df.copy()
    denom = float(denominator or 0.0)
    data["PCT"] = data[value_col] / denom * 100 if denom else 0.0
    data["LABEL"] = [f"{int(v):,}  ({p:.1f}%)" for v, p in zip(data[value_col], data["PCT"])]
    order = data[label_col].astype(str).tolist()

    bars = alt.Chart(data).mark_bar(color=SEQUENTIAL_BLUE).encode(
        x=alt.X(f"{value_col}:Q", title=x_title, axis=alt.Axis(format="~s")),
        y=alt.Y(f"{label_col}:N", title=None, sort=order),
        tooltip=[
            alt.Tooltip(f"{label_col}:N", title=label_col.replace("_", " ").title()),
            alt.Tooltip(f"{value_col}:Q", title=x_title, format=","),
            alt.Tooltip("PCT:Q", title=pct_title, format=".1f"),
        ],
    )
    labels = alt.Chart(data).mark_text(align="left", dx=6).encode(
        x=alt.X(f"{value_col}:Q"),
        y=alt.Y(f"{label_col}:N", sort=order),
        text="LABEL:N",
    )
    return (bars + labels).properties(height=alt.Step(step))


def funnel_bar(df: pd.DataFrame, stage_col: str, value_col: str) -> alt.LayerChart:
    """Nested stages, each labelled with its share of the first stage."""
    base = float(df[value_col].iloc[0]) if len(df) else 0.0
    return _ordered_labelled_bar(df, stage_col, value_col, "Sessions", base, "% of first stage", 44)


def ordered_bar(df: pd.DataFrame, label_col: str, value_col: str, x_title: str = "Sessions") -> alt.LayerChart:
    """Ordered categories (e.g. duration bands), each labelled with its share of the total."""
    total = float(df[value_col].sum()) if len(df) else 0.0
    return _ordered_labelled_bar(df, label_col, value_col, x_title, total, "% of total", 38)


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
    # Snowflake returns DATE as Python date objects, which Altair cannot serialise to
    # JSON. Streamlit's Arrow transport tolerates them, so the app renders, but
    # to_json() and any notebook/export path raise TypeError. Coercing here keeps the
    # builder correct on every render path rather than relying on the caller.
    data = df.copy()
    data[x_col] = pd.to_datetime(data[x_col])

    base = alt.Chart(data).encode(
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


def multi_trend_line(df: pd.DataFrame, x_col: str, y_col: str, series_col: str, y_title: str) -> alt.Chart:
    """Several series over time - one line and one colour per series.

    Legend order follows total volume rather than the alphabet, so the dominant
    series reads first. Colours cycle if there are more series than the palette
    holds; slicing CATEGORICAL directly would hand Altair a short range and silently
    drop the colour encoding for the tail.
    """
    # Same date coercion as trend_line: Snowflake hands back Python date objects,
    # which Altair's JSON encoder rejects even though Streamlit's Arrow path accepts
    # them - so the app renders while to_json() raises.
    data = df.copy()
    data[x_col] = pd.to_datetime(data[x_col])

    order = data.groupby(series_col)[y_col].sum().sort_values(ascending=False).index.astype(str).tolist()
    palette = [CATEGORICAL[i % len(CATEGORICAL)] for i in range(len(order))]

    return (
        alt.Chart(data)
        .mark_line(strokeWidth=2, point=len(data[x_col].unique()) <= 14)
        .encode(
            x=alt.X(f"{x_col}:T", title=None),
            y=alt.Y(f"{y_col}:Q", title=y_title, axis=alt.Axis(format="~s")),
            color=alt.Color(
                f"{series_col}:N",
                scale=alt.Scale(domain=order, range=palette),
                legend=alt.Legend(orient="bottom", title=None, columns=3),
            ),
            tooltip=[
                alt.Tooltip(f"{x_col}:T", title="Week of", format="%b %d"),
                alt.Tooltip(f"{series_col}:N", title="Series"),
                alt.Tooltip(f"{y_col}:Q", title=y_title, format=","),
            ],
        )
    )


def diverging_bar(df: pd.DataFrame, label_col: str, value_col: str, x_title: str) -> alt.Chart:
    """Signed change per category: growth right in green, decline left in red.

    Row order is held as supplied - callers rank by magnitude of change, so the
    biggest movers in either direction stay at the top rather than all the growth
    sorting to one end.
    """
    data = df.copy()
    order = data[label_col].astype(str).tolist()
    return (
        alt.Chart(data)
        .mark_bar()
        .encode(
            x=alt.X(f"{value_col}:Q", title=x_title, axis=alt.Axis(format="~s")),
            y=alt.Y(f"{label_col}:N", title=None, sort=order),
            color=alt.condition(f"datum.{value_col} > 0", alt.value(CATEGORICAL[2]), alt.value(CATEGORICAL[7])),
            tooltip=[
                alt.Tooltip(f"{label_col}:N", title=label_col.replace("_", " ").title()),
                alt.Tooltip(f"{value_col}:Q", title=x_title, format="+,"),
            ],
        )
        .properties(height=alt.Step(30))
    )


# distribution_histogram was removed deliberately. It clipped its bin extent to the
# 95th percentile, which silently dropped ~5% of rows from the drawing - including
# the single largest value, which is usually the one worth seeing. Both callers now
# use ordered_bar over SQL-computed bands, where every row lands in a labelled
# bucket and the outliers get an explicit "over N" band.
