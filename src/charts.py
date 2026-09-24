import math

import altair as alt
import pandas as pd

from src.theme import (
    categorical,
    negative_color,
    positive_color,
    readable_on,
    series_color,
    surface,
)


# The gutter reserves `label_limit`, the text is allowed `label_limit - _LABEL_GAP`. Setting
# both to the same value left the longest names cut flat against the first bar with no ellipsis
# at all - the text filled the gutter exactly, so there was nowhere for Vega to draw one, and a
# name that merely stops reads as a name that ends. The gap is what turns a hard clip into a
# visible "...", and it doubles as the breathing room between the labels and the bars.
_LABEL_GAP = 14


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

    bars = alt.Chart(data).mark_bar(color=series_color()).encode(
        x=alt.X(f"{value_col}:Q", title=x_title, axis=alt.Axis(format="~s")),
        y=alt.Y(f"{label_col}:N", title=None, sort=order),
        tooltip=[
            alt.Tooltip(f"{label_col}:N", title=label_col.replace("_", " ").title()),
            alt.Tooltip(f"{value_col}:Q", title=x_title, format=","),
            alt.Tooltip("PCT:Q", title=pct_title, format=".1f"),
        ],
    )
    # The label sits in the gutter BESIDE the bar, not on the fill, so it is read against the
    # surface rather than against series_color() - hence readable_on(surface()) and not
    # readable_on(series_color()). Without an explicit colour Vega defaults to black, which
    # is invisible on the dark surface: the count and share simply vanished in dark mode.
    labels = alt.Chart(data).mark_text(align="left", dx=6, color=readable_on(surface())).encode(
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


def top_events_bar(
    df: pd.DataFrame, name_col: str, value_col: str, x_title: str = "Events", tooltip=None,
    label_limit: float | None = None,
) -> alt.Chart:
    """Ranked magnitude: single hue, sorted, axis + tooltip carry values.

    `tooltip` replaces the default two-field tooltip for callers with more to say, and
    `label_limit` widens the y-axis label gutter. Seven charts across five pages share this
    function, so both are arguments rather than richer defaults, which would have changed
    six charts to improve one.

    On `label_limit`: Vega's default is 180px, and it does two things at once when a label
    is wider. It ellipsises the text, AND the gutter it reserves stops growing - so on the
    campaign ranking the labels were both cut short and then clipped again by the left edge
    of the plot, losing their leading characters: "SG0626-015 Logitech" rendered as
    "S0626-015 Logit...", and "US0623-090" lost "US0" entirely. A truncated name is a
    nuisance; one missing its first characters is unidentifiable, and these IDs are
    prefixed by region, which is exactly the part that disappeared.

    The labels are also flush LEFT, which is what makes the ellipsis land at the end where
    it belongs. A left axis anchors its labels at the axis line and right-aligns them, so
    every name ends flush against its bar and starts at a different x - the eye has no
    column to run down, and any clipping eats the start. Anchoring at labelPadding out from
    the axis and aligning left puts the anchor at the far edge of the gutter instead: names
    start in one column, and what runs out of room is the tail.

    Deliberately NOT solved by shortening the strings in pandas. The y encoding is nominal,
    so two names sharing a truncated prefix would merge into one bar with summed values -
    the failure campaign_bar already disambiguates for, and the one campaign_pages_sql was
    rewritten to avoid. Vega's own ellipsis is presentation only and cannot merge two rows.
    """
    return (
        alt.Chart(df)
        .mark_bar(color=series_color())
        .encode(
            x=alt.X(f"{value_col}:Q", title=x_title),
            y=alt.Y(f"{name_col}:N", title=None, sort="-x",
                    axis=alt.Axis(labelLimit=label_limit - _LABEL_GAP, labelAlign="left",
                                  labelPadding=label_limit) if label_limit else alt.Undefined),
            tooltip=tooltip
            or [
                alt.Tooltip(f"{name_col}:N", title=name_col.replace("_", " ").title()),
                alt.Tooltip(f"{value_col}:Q", title=x_title, format=","),
            ],
        )
    )


# The y-axis label gutter for the two charts whose labels are identifiers rather than words:
# campaign names (up to 140 characters, one a chain of "-clone-<uuid>" suffixes) and page paths.
# Vega's 180px default clipped the leading characters off both. 300px is about 46 characters at
# the axis font and still leaves the plot the majority of a full-width chart; past that the bars
# become the smaller half of the figure, which trades one unreadable thing for another.
_LABEL_LIMIT = 300


def page_bar(df: pd.DataFrame) -> alt.Chart:
    """Pages for one campaign: the path labels the bar, the host rides in the tooltip.

    Its own function rather than a tooltip list built at the call site, for the reason
    campaign_bar is one: the encoding and the columns it depends on belong together, and
    src/campaign_detail.py does not otherwise import Altair.

    campaign_pages_sql guarantees PAGE is unique - it falls back to the full URL where two
    hosts share a path - so the nominal y encoding here cannot merge two pages into one bar.
    """
    return top_events_bar(df, "PAGE", "SESSIONS", x_title="Sessions", label_limit=_LABEL_LIMIT, tooltip=[
        alt.Tooltip("PAGE:N", title="Page"),
        alt.Tooltip("HOST:N", title="Host"),
        alt.Tooltip("SESSIONS:Q", title="Sessions", format=","),
        alt.Tooltip("VIEWS:Q", title="Views", format=","),
        alt.Tooltip("VIEWS_PER_SESSION:Q", title="Views / session", format=".1f"),
        alt.Tooltip("PCT_OF_SESSIONS:Q", title="% of campaign sessions", format=".1f"),
    ])


# Ranked by sessions where they exist, by events where they do not. The axis title has to
# follow, because a bar chart whose length silently changes meaning is worse than either
# metric alone - see top_campaigns_sql for why both regimes exist in one dataset.
_CAMPAIGN_FALLBACK = (
    "Ranked by events, not sessions: session IDs were not recorded on campaign rows "
    "before 2025-11, so campaigns in this range have no session count."
)
_CAMPAIGN_OPAQUE = (
    "{n} of these campaigns have no campaign name, so their tracking ID is shown "
    "instead. Name capture began 2026-03."
)
_CAMPAIGN_CLASH = (
    "{n} campaign names are used by more than one campaign, so the tracking ID is "
    "appended to tell them apart."
)
# Called Tenant, not Customer. The value is PROPERTIES:tenant_id and it is an opaque
# number - 1, 125, 45363964055 - with no name anywhere in the dataset to map it to. The
# rest of the app already calls these tenants, and "Customer" would promise a company
# name that the tooltip cannot deliver.
_CAMPAIGN_UNTAGGED = (
    "Tenant is not shown: tenant IDs were not recorded on campaign rows before 2025-11, "
    "so no campaign in this range is attributed to one."
)
_UNTAGGED = "(untagged)"

# Name of the Vega selection parameter on the campaign chart. The page reads the click
# back under this key, so it is defined here beside the chart that declares it rather than
# spelled twice.
CAMPAIGN_PICK = "campaign_pick"


def campaign_bar(df: pd.DataFrame, window_start, window_end):
    """Top Campaigns, with a tooltip that describes each campaign.

    Returns (chart, notes). The notes are caveats the chart cannot carry itself; the
    page prints them beneath it.

    Two things are done in pandas rather than Vega deliberately. Duplicate labels are
    disambiguated first, because the y encoding is nominal - two campaigns sharing a
    name would silently merge into one bar with summed values and a tooltip describing
    neither. 16 names are shared across the dataset; none reach a top 10 today, which
    makes this the kind of bug that would appear on one date range and no other. And
    every tooltip line is pre-rendered into a string, because Vega's `format` cannot
    express "17 of 92 days active" or a recency measured against the window end.
    """
    data = df.copy()
    notes = []
    window_days = (window_end - window_start).days + 1

    ranked_by_sessions = float(data["SESSIONS"].sum()) > 0
    metric = "SESSIONS" if ranked_by_sessions else "EVENTS"
    axis_title = "Sessions" if ranked_by_sessions else "Events"
    if not ranked_by_sessions:
        notes.append(_CAMPAIGN_FALLBACK)

    opaque = int(data["LABEL_IS_OPAQUE"].sum())
    if opaque:
        notes.append(_CAMPAIGN_OPAQUE.format(n=opaque))

    clashes = data["CAMPAIGN_LABEL"].duplicated(keep=False)
    if clashes.any():
        notes.append(_CAMPAIGN_CLASH.format(n=int(data.loc[clashes, "CAMPAIGN_LABEL"].nunique())))
        suffix = data["CAMPAIGN_ID"].astype(str).str.slice(0, 8)
        data.loc[clashes, "CAMPAIGN_LABEL"] = data.loc[clashes, "CAMPAIGN_LABEL"] + "  [" + suffix + "]"

    # Recency is measured against the end of the selected window, never against today -
    # on a range ending in March, "150 days idle" would be an artefact of when the page
    # was opened rather than anything about the campaign.
    data["RECENCY"] = [
        "Active on the last day of the range"
        if last >= window_end
        else f"Last active {(window_end - last).days:,} days before the range ended"
        for last in data["LAST_SEEN"]
    ]
    data["RAN"] = [
        f"{first} to {last}   ({int(active):,} of {window_days:,} days active)"
        for first, last, active in zip(data["FIRST_SEEN"], data["LAST_SEEN"], data["ACTIVE_DAYS"])
    ]
    data["REACH"] = [
        f"{int(s):,} sessions   ({share:.1f}% of campaign sessions)"
        for s, share in zip(data["SESSIONS"], data["SESSION_SHARE_PCT"])
    ]
    data["VOLUME"] = [
        f"{int(e):,} events   ({per:.1f} per session)" if per else f"{int(e):,} events"
        for e, per in zip(data["EVENTS"], data["EVENTS_PER_SESSION"])
    ]
    data["CONTENT"] = [
        f"{pct:.1f}% of sessions opened content   ({int(n):,} assets)"
        for pct, n in zip(data["ASSET_PCT"], data["ASSETS"])
    ]
    data["LEADS"] = [f"{pct:.2f}% of sessions submitted a form" for pct in data["LEAD_PCT"]]

    tooltip = [alt.Tooltip("CAMPAIGN_LABEL:N", title="Campaign")]
    # Withheld only when it would say (untagged) for every campaign, which means the
    # window predates tenant tagging. A single untagged campaign among tagged ones is a
    # real answer about that campaign and stays.
    tagged = (data["TENANT"].astype(str) != _UNTAGGED).any()
    if tagged:
        tooltip.append(alt.Tooltip("TENANT:N", title="Tenant"))
    else:
        notes.append(_CAMPAIGN_UNTAGGED)
    # Session-derived lines are withheld, not zeroed, where sessions do not exist: a
    # tooltip reading "0.0% of sessions opened content" asserts a measurement that was
    # never taken. Same for content where the window predates asset tagging (2026-03).
    if ranked_by_sessions:
        tooltip.append(alt.Tooltip("REACH:N", title="Reach"))
    tooltip.append(alt.Tooltip("VOLUME:N", title="Activity"))
    tooltip.append(alt.Tooltip("RAN:N", title="Ran"))
    tooltip.append(alt.Tooltip("RECENCY:N", title="Recency"))
    if ranked_by_sessions and float(data["ASSETS"].sum()) > 0:
        tooltip.append(alt.Tooltip("CONTENT:N", title="Content"))
    if ranked_by_sessions:
        tooltip.append(alt.Tooltip("LEADS:N", title="Leads"))

    # Only the encoded columns are handed to Altair, derived from the tooltip list itself
    # so the two cannot drift. Two reasons. FIRST_SEEN and LAST_SEEN arrive from Snowflake
    # as date objects, and a date is not JSON serialisable - chart.to_json() raises on
    # them even though Streamlit's own path survives by shipping data as Arrow, so the
    # chart was one serialisation route away from failing. And the raw columns are already
    # spent: they were read in pandas to build RAN and RECENCY, so sending them too would
    # push ten unused columns to the browser on every rerun.
    fields = [str(t.shorthand).split(":")[0] for t in tooltip]
    keep = list(dict.fromkeys(fields + ["CAMPAIGN_LABEL", metric, "CAMPAIGN_ID"]))
    chart = top_events_bar(data[keep], "CAMPAIGN_LABEL", metric, x_title=axis_title, tooltip=tooltip, label_limit=_LABEL_LIMIT)

    # The selection carries CAMPAIGN_ID, not the label. Labels are display strings - they
    # can be a name, a tracking ID, or a name with an ID appended to break a collision -
    # so keying a drill-down on one would break exactly where two campaigns share a name.
    # CAMPAIGN_ID rides in the data without being encoded or shown in the tooltip; Vega can
    # still read it off the datum.
    chart = chart.add_params(alt.selection_point(name=CAMPAIGN_PICK, fields=["CAMPAIGN_ID"]))
    return chart, notes


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
    hues = categorical()
    palette = [hues[i % len(hues)] for i in range(len(order))]
    # The share label sits on the fill, so its colour is chosen per segment rather
    # than fixed to white. The validated palette deliberately spans light and dark
    # slots - forcing every slot dark enough for white text is exactly what
    # collapsed colour-blind separation when this palette was first derived.
    ranked["ink"] = [readable_on(c) for c in palette]

    bars = (
        alt.Chart(ranked)
        .mark_bar(stroke=surface(), strokeWidth=2)
        .encode(
            x=alt.X("seg_start:Q", title=None, axis=None, scale=alt.Scale(domain=[0, 1])),
            x2="seg_end:Q",
            color=alt.Color(
                f"{name_col}:N",
                scale=alt.Scale(domain=order, range=palette),
                legend=alt.Legend(orient="bottom", title=None, columns=3),
            ),
            tooltip=[alt.Tooltip(f"{name_col}:N", title="Type"), alt.Tooltip("tip:N", title="Share")],
        )
    )
    labels = (
        alt.Chart(ranked)
        .mark_text(fontWeight="bold")
        .encode(
            x=alt.X("seg_mid:Q", scale=alt.Scale(domain=[0, 1])),
            text="pct:N",
            color=alt.Color("ink:N", scale=None, legend=None),
        )
    )
    return (bars + labels).properties(height=180)


# Arc geometry in one place. The label radius is DERIVED from the band rather than typed
# again: the two were independent numbers and the share label drifted off the ring.
_DONUT_INNER = 70
_DONUT_OUTER = 110
_DONUT_LABEL_R = (_DONUT_INNER + _DONUT_OUTER) / 2
# Explicit, because an arc chart with no width is rescaled to whatever container it lands
# in - and mark radii are absolute pixels, so the ring grew while the labels stayed put.
# Render this one with width="content", never "stretch".
# Wider than the ring so the bottom legend has room - at 2*outer+40 the third entry
# clipped to "Form subm".
#
# width/height size the PLOT AREA only, so height needs the ring plus a margin and nothing
# else. It briefly also reserved 56px for the bottom legend, which was surplus: autosize
# "pad" already grows the view to fit the legend, so that space was counted twice and left
# a large empty band above the ring. Let pad handle the legend; size the plot for the ring.
_DONUT_MARGIN = 24      # breathing room above and below the ring, inside the plot area
_DONUT_BOX_W = 2 * _DONUT_OUTER + 2 * _DONUT_MARGIN  # square; the legend sits outside it
_DONUT_BOX_H = 2 * _DONUT_OUTER + 2 * _DONUT_MARGIN

def donut_chart(df: pd.DataFrame, name_col: str, value_col: str, order=None,
                centre_label: str = "", centre_sublabel: str = "") -> alt.LayerChart:
    """Part-to-whole ring, total in the hole.

    A donut is only legible as part-to-whole at a glance and only up to about six segments -
    past that, or for comparing close values, a bar is the right form and this is the wrong
    one. Callers are expected to have partitioned their data before arriving here.

    `order` pins the slice-to-colour mapping to a FIXED list rather than to rank. Without it a
    campaign whose clicks outnumber its page visits would repaint both slices, and colour that
    moves with rank stops being an identity cue the moment two campaigns are compared.

    The hole exists to hold a number - this chart replaced a KPI tile, and `centre_label` is
    how that tile's figure survives.
    """
    data = df.copy()
    total = float(data[value_col].sum())
    data["share"] = data[value_col] / total if total else 0.0
    # Below ~8% the arc is too short to seat a label without it overrunning its own slice, so
    # the number falls back to the tooltip and the table view rather than being drawn badly.
    data["pct"] = data["share"].map(lambda v: f"{v * 100:.0f}%" if v * 100 >= 8 else "")
    data["tip"] = data["share"].map(lambda v: f"{v * 100:.1f}%")

    domain = list(order) if order else data[name_col].astype(str).tolist()
    data = data[data[name_col].isin(domain)]
    hues = categorical()
    palette = [hues[i % len(hues)] for i in range(len(domain))]
    inks = dict(zip(domain, [readable_on(c) for c in palette]))
    data["ink"] = data[name_col].map(inks)

    # The legend carries the VALUE and SHARE, not just the name, because a slice small enough
    # to matter is a slice too thin to label. At a tenth of a percent the arc is under a pixel
    # wide: no font size, radius or offset makes that band readable, and inflating it to a
    # minimum angle would make the ring stop matching its own numbers. The legend has room,
    # always renders, and its swatch is the only place that colour is visible at all.
    #
    # A real count must never be captioned "0%". One decimal is not enough on its own -
    # 7 of 100,507 rounds to "0.0%", which reads exactly as broken as "0%" - so anything
    # under a tenth of a percent is reported as a bound instead of a rounded zero.
    def _legend(name, value, share):
        pct_val = share * 100
        if pct_val <= 0:
            pct = "0%"
        elif pct_val < 0.1:
            pct = "<0.1%"
        elif pct_val < 1:
            pct = f"{pct_val:.1f}%"
        else:
            pct = f"{pct_val:.0f}%"
        return f"{name} - {int(value):,} ({pct})"

    labelled = {r[name_col]: _legend(r[name_col], r[value_col], r["share"]) for _, r in data.iterrows()}
    # Ordered by the fixed domain, so legend order and palette stay pinned to the entity.
    legend_domain = [labelled[n] for n in domain if n in labelled]
    legend_range = [palette[i] for i, n in enumerate(domain) if n in labelled]
    data["legend_label"] = data[name_col].map(labelled)
    # Sorted to the fixed order so the arcs are laid out in the same sequence as the legend
    # and the palette, whatever order the query happened to return.
    data["_seq"] = data[name_col].map({n: i for i, n in enumerate(domain)})
    data = data.sort_values("_seq")

    # Explicit start/end angles in radians, for the same reason share_stacked_bar computes its
    # own seg_start/seg_end: with theta=...stack=True the arc layer and the label layer each
    # stack independently, and the arc layer's colour encoding reorders it. The layers then
    # disagree about where a slice begins - the share label for Clicks was drawn over the Page
    # visit arc. Computing the angles once in pandas keeps both layers on the same geometry.
    turn = 2 * math.pi
    data["t_end"] = data["share"].cumsum() * turn
    data["t_start"] = data["t_end"] - data["share"] * turn
    data["t_mid"] = (data["t_start"] + data["t_end"]) / 2

    base = alt.Chart(data)
    arcs = base.mark_arc(innerRadius=_DONUT_INNER, outerRadius=_DONUT_OUTER, stroke=surface(), strokeWidth=2).encode(
        theta=alt.Theta("t_start:Q", scale=None),
        theta2="t_end:Q",
        color=alt.Color(
            "legend_label:N",
            scale=alt.Scale(domain=legend_domain, range=legend_range),
            # Right, not bottom. The entries carry counts now, so they are too long to sit
            # three-across without clipping, and stacked underneath they made the element
            # tall and narrow - a 340px column of content on a page three times that wide.
            # Beside the ring they use the horizontal room the page already has, and the
            # whole chart gets shorter instead of taller.
            # offset pulls the legend in towards the ring - the Vega default of 18 pushed
            # these long entries out to the card edge. labelLimit stays generous enough
            # for the longest entry ("Page visit - 139,825 (98%)") so none of them ellipse.
            legend=alt.Legend(orient="right", title=None, columns=1, labelLimit=230,
                              offset=2, labelFontSize=12, symbolSize=90, rowPadding=4),
        ),
        tooltip=[
            alt.Tooltip(f"{name_col}:N", title="Event"),
            alt.Tooltip(f"{value_col}:Q", title="Events", format=","),
            alt.Tooltip("tip:N", title="Share"),
        ],
    )
    # The share sits ON the fill, so its colour is per slice for the same reason
    # share_stacked_bar chooses per segment: the validated palette spans light and dark slots.
    labels = base.mark_text(radius=_DONUT_LABEL_R, fontWeight="bold", fontSize=11).encode(
        theta=alt.Theta("t_mid:Q", scale=None),
        text="pct:N",
        color=alt.Color("ink:N", scale=None, legend=None),
    )
    layers = [arcs, labels]

    # The hole holds the total, and `centre_sublabel` names it - a bare number in a ring is
    # ambiguous about which of the page's several totals it is. Nudged apart with dy so the
    # pair reads as one block rather than two overlapping marks.
    if centre_label:
        centre = alt.Chart(pd.DataFrame({"t": [centre_label]})).mark_text(
            fontSize=22, fontWeight="bold", dy=-9 if centre_sublabel else 0,
            color=readable_on(surface()),
        ).encode(text="t:N")
        layers.append(centre)
    if centre_sublabel:
        sub = alt.Chart(pd.DataFrame({"t": [centre_sublabel]})).mark_text(
            fontSize=11, dy=13, opacity=0.75, color=readable_on(surface()),
        ).encode(text="t:N")
        layers.append(sub)
    # autosize "pad" grows the view to fit its contents. The default "fit" does the opposite -
    # it shrinks the view to the given size, and since the arc cannot shrink with it, the ring
    # is what gets clipped.
    # autosize "pad" grows the view to fit its contents. The default "fit" does the opposite -
    # it shrinks the view to the given size, and since an arc cannot shrink with it, the ring
    # is what gets clipped.
    #
    # `padding` is separate from, and does more than, the height arithmetic above: width and
    # height describe the PLOT AREA only, and a top-anchored label or the ring's own edge can
    # still sit against the view boundary. This reserves space outside the plot area, which is
    # the part that was cropping along the top.
    return alt.layer(*layers).properties(
        width=_DONUT_BOX_W,
        height=_DONUT_BOX_H,
        padding={"left": 10, "right": 10, "top": 8, "bottom": 8},
        autosize=alt.AutoSizeParams(type="pad", contains="padding"),
    )


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
    line = base.mark_line(color=series_color(), strokeWidth=2)
    if len(df) <= 14:
        return line + base.mark_point(color=series_color(), size=45, filled=True)
    return line


def trend_bar(df: pd.DataFrame, x_col: str, y_col: str, y_title: str) -> alt.Chart:
    """Daily totals as columns. Same data as trend_line, a different claim about it.

    A line interpolates: it draws a value for every instant between two days, and there is no
    such thing as half a session. One column per day says the quantity belongs to that day and
    that nothing sits between them - which is what a daily count is.

    Deliberately still a TEMPORAL axis rather than one band per day. The daily query returns only
    days that had activity, so on a temporal axis a silent day is a gap where it actually
    happened; on an ordinal axis the inactive days collapse together and a quiet week disappears
    from the chart entirely. This campaign is active on 27 of 31 days, so that is not a
    hypothetical.

    A sibling of trend_line rather than a flag on it: trend_line is on the Executive Dashboard
    and Session Analytics too, and neither asked for this.
    """
    # Same date coercion as trend_line - Snowflake hands back Python date objects, which
    # Altair's JSON encoder rejects even though Streamlit's Arrow path accepts them.
    data = df.copy()
    data[x_col] = pd.to_datetime(data[x_col])

    # Rounded caps only while the columns are wide enough to carry one. Across a year the band
    # is a couple of pixels, and a 3px radius on a 3px column renders as a lozenge - a
    # different mark rather than a rounded one. Squared at the baseline either way.
    corner = 3 if len(data) <= 60 else 0
    base = alt.Chart(data).encode(
        x=alt.X(f"{x_col}:T", title=None),
        y=alt.Y(f"{y_col}:Q", title=y_title),
        tooltip=[
            alt.Tooltip(f"{x_col}:T", title="Date", format="%b %d"),
            alt.Tooltip(f"{y_col}:Q", title=y_title, format=","),
        ],
    )
    # width={"band": 1} fills the whole slot, so consecutive days touch. Altair's default leaves
    # padding either side, which reads as a gap between every day; asked for adjacent columns
    # instead. Days with no activity are still gaps, because those rows are absent from the data
    # rather than zero - which is the reason this axis is temporal.
    # A hairline in the surface colour, matching the PDF: adjacent full-width columns in one flat
    # colour merge into a block, and where two meet this scores a thin division between them.
    # surface() rather than a darkened teal because it is mode-aware - white on the light surface,
    # near-black on the dark one - where a darker edge would vanish into a dark background.
    # Dropped past ~120 columns, where a 1px stroke either side would be most of the column.
    stroke_w = 1 if len(data) <= 120 else 0
    bars = base.mark_bar(color=series_color(), width=alt.RelativeBandSize(1),
                         stroke=surface(), strokeWidth=stroke_w,
                         cornerRadiusTopLeft=corner, cornerRadiusTopRight=corner)
    # The figure above each column, on screen as well as in the PDF. Only while they fit: past
    # roughly forty days the labels are wider than the slot and would overprint each other, and
    # Altair has no collision-avoidance to fall back on the way the PDF's own walk does. The
    # tooltip still answers every column at any range.
    if len(data) <= 40:
        return bars + base.mark_text(dy=-6, fontSize=9, color=readable_on(surface())).encode(
            text=alt.Text(f"{y_col}:Q", format=","),
        )
    return bars


def multi_trend_line(df: pd.DataFrame, x_col: str, y_col: str, series_col: str, y_title: str) -> alt.Chart:
    """Several series over time - one line and one colour per series.

    Legend order follows total volume rather than the alphabet, so the dominant
    series reads first. Colours cycle if there are more series than the palette
    holds; slicing the palette directly would hand Altair a short range and silently
    drop the colour encoding for the tail.
    """
    # Same date coercion as trend_line: Snowflake hands back Python date objects,
    # which Altair's JSON encoder rejects even though Streamlit's Arrow path accepts
    # them - so the app renders while to_json() raises.
    data = df.copy()
    data[x_col] = pd.to_datetime(data[x_col])

    order = data.groupby(series_col)[y_col].sum().sort_values(ascending=False).index.astype(str).tolist()
    hues = categorical()
    palette = [hues[i % len(hues)] for i in range(len(order))]

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
            color=alt.condition(f"datum.{value_col} > 0", alt.value(positive_color()), alt.value(negative_color())),
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
