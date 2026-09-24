"""One campaign's report, assembled without calling Streamlit.

Nothing here touches st.*, so every function runs identically inside a Streamlit rerun, a
Snowpark stored procedure or a plain script. That is the point: the scheduled email planned
next has no Streamlit runtime to call into, and if the report were assembled inside a page
callback the automation would have to reimplement it and the two would drift - the failure
this project has already had to correct twice, when a documented figure and a computed one
disagreed.

ONE CAVEAT, measured rather than assumed. This module calls no Streamlit, but importing it
still pulls Streamlit in transitively - 323 modules - because src.queries takes table_fqn
from src.db, and src.db imports Streamlit for @st.cache_data and st.session_state. So a
stored procedure importing this would need streamlit in its package list (it is in the
Snowflake Anaconda channel, so that works) or src.db's pure parts split out first. The
functions themselves are runtime-independent; the import graph is not, yet.

The unit of reuse is collect(): it returns every section as a DataFrame, already gated, and
three consumers render from the same dict.

    run          a callable (sql, params) -> DataFrame. Streamlit passes its cached
                 run_query; a stored procedure passes a plain Snowpark call. collect() never
                 imports either, so it does not care which runtime it is in.

Gating lives here rather than in the renderer, and that is a deliberate choice about
disclosure: a section that is withheld is never QUERIED, so it cannot reach a CSV, an email
body or a browser payload by accident. The identified-people floor is the clearest case -
below it the company breakdown is not fetched at all, so no downstream consumer can leak it
however it is written.
"""

from __future__ import annotations

import datetime as dt
import html
import math

from src.queries import (
    EVENT_BUCKET_ORDER,
    campaign_ai_sql,
    campaign_assets_sql,
    campaign_companies_sql,
    campaign_daily_sql,
    campaign_detail_kpis_sql,
    campaign_duration_bands_sql,
    campaign_event_mix_sql,
    campaign_funnel_sql,
    campaign_geo_sql,
    campaign_lookup_sql,
    campaign_pages_sql,
    campaign_read_depth_sql,
    campaign_sources_sql,
)

# An identified-people floor, not a courtesy. Across the top 25 campaigns two have exactly
# one identified person, and "1 person at acme.com" is an individual described by a report
# that promises domain-level aggregation only. Below the floor the breakdown is not queried.
IDENTITY_FLOOR = 5

# When one network address accounts for this much of a campaign's sessions, the session
# count stops describing reach and starts describing one place. Both numbers are measured,
# not picked: across 169 campaigns with 50+ sessions the top address takes a median 20%,
# and the distribution is bimodal - 65 campaigns under 20%, 21 above 90%, little between -
# so a threshold separates two kinds of campaign rather than cutting a gradient.
#
# The session floor is what keeps it meaningful. With no floor the rule fires on 165 of 594
# campaigns, because four sessions out of five is 80% and says nothing; at 20 it fires on
# 36 of 276. A caption that appears on a quarter of campaigns is furniture.
CONCENTRATION_PCT = 80.0
CONCENTRATION_MIN_SESSIONS = 20


def concentration(kpis) -> float | None:
    """Top address's share of this campaign's sessions, or None when it is not worth saying.

    Returns None rather than a number below the threshold so every caller asks the same
    question once - three renderers would otherwise each re-implement the gate, and the
    PDF and the page could then disagree about whether a campaign is concentrated.
    """
    sessions = int(kpis["SESSIONS"] or 0)
    top = int(kpis["TOP_IP_SESSIONS"] or 0)
    if sessions < CONCENTRATION_MIN_SESSIONS or not sessions:
        return None
    share = top / sessions * 100
    return share if share >= CONCENTRATION_PCT else None

STATUS_OK = "ok"
STATUS_NO_SESSIONS = "no_sessions"
STATUS_MISSING = "missing"


def classify_lookup(found) -> str:
    """Turn a campaign_lookup_sql row into one of three outcomes.

    "open"          activity inside the selected window
    "wrong_window"  a real campaign that ran on other dates - name them
    "unknown"       no such id anywhere in the tracked data - a typo

    Pure, and tested directly, because the path that reaches it cannot be driven through
    Streamlit's test harness: AppTest resolves a selectbox value via options.index(), so a
    deliberately-absent value - the whole point of accepting a pasted id - raises before the
    script runs.
    """
    if int(found["EVENTS_IN_WINDOW"]) > 0:
        return "open"
    if int(found["EVENTS_EVER"]) > 0:
        return "wrong_window"
    return "unknown"


def collect(run, campaign_id: str, start: dt.date, end: dt.date) -> dict:
    """Every section of one campaign's report, gated, from a single entry point.

    Returns a dict carrying `status`, the KPI row, and a DataFrame per section that has
    data. Sections absent from the dict were not queried - see the module note on why that
    matters. Ordering of the queries is the same order the page renders them, so a reader
    following a slow load sees them appear top to bottom.
    """
    params = [start, end, campaign_id]
    kpis = run(campaign_detail_kpis_sql(), params).iloc[0]
    out: dict = {
        "campaign_id": campaign_id,
        "start": start,
        "end": end,
        "kpis": kpis,
        "label": kpis["CAMPAIGN_NAME"] or campaign_id,
        "named": bool(kpis["CAMPAIGN_NAME"]),
    }

    # Nothing for this id in this window. The likeliest cause of a shared link failing is a
    # real campaign that ran on other dates, so the reason is resolved here - once - and
    # every consumer gets the same explanation rather than inventing its own.
    if int(kpis["EVENTS"]) == 0:
        found = run(campaign_lookup_sql(), params).iloc[0]
        out["status"] = STATUS_MISSING
        out["reason"] = classify_lookup(found)
        out["lookup"] = found
        return out

    # Real campaign, but SESSION_ID was not recorded on campaign rows before 2025-11, so
    # every session-derived section would be an empty claim rather than a measurement.
    if int(kpis["SESSIONS"]) == 0:
        out["status"] = STATUS_NO_SESSIONS
        return out

    out["status"] = STATUS_OK
    out["daily"] = run(campaign_daily_sql(), params)
    out["duration"] = run(campaign_duration_bands_sql(), params)
    out["funnel"] = run(campaign_funnel_sql(), params)
    out["event_mix"] = run(campaign_event_mix_sql(), params)
    # No "actions" key any more: the action-reach breakdown was removed from the page, the PDF
    # and the emailed body, so fetching it would be a round trip nothing reads. That takes this
    # from eleven queries per drill-down to ten. campaign_actions_sql() is kept in queries.py -
    # it is correct and the suites still exercise it - so restoring the section is one line here.

    geo = run(campaign_geo_sql(), params)
    out["regions"] = geo[geo["KIND"] == "region"][["LABEL", "SESSIONS"]].rename(columns={"LABEL": "REGION"})
    out["timezones"] = geo[geo["KIND"] == "timezone"][["LABEL", "SESSIONS"]].rename(columns={"LABEL": "TIMEZONE"})

    # Pages before assets, matching the order all three surfaces render them in: the page is the
    # container, the asset is the content opened on it. Gated on the count the KPI query already
    # returned, so a campaign whose page views carry no resolvable SEARCH_URL costs no round trip.
    if int(kpis["PAGES"]) > 0:
        out["pages"] = run(campaign_pages_sql(), params)

    if int(kpis["ASSETS"]) > 0:
        out["assets"] = run(campaign_assets_sql(), params)
        if int(kpis["PDF_EVENTS"]) > 0:
            out["depth"] = run(campaign_read_depth_sql(), params)

    if int(kpis["PEOPLE"]) >= IDENTITY_FLOOR:
        out["companies"] = run(campaign_companies_sql(), params)

    sources = run(campaign_sources_sql(), params)
    out["sources"] = sources[sources["KIND"] == "group"][["LABEL", "EVENTS"]].rename(columns={"LABEL": "SOURCE_GROUP"})
    out["referrers"] = sources[sources["KIND"] == "referrer"][["LABEL", "EVENTS"]].rename(columns={"LABEL": "REFERRER"})

    if int(kpis["AI_EVENTS"]) > 0:
        out["ai"] = run(campaign_ai_sql(), params)

    return out


def pct(part, whole) -> float:
    """Percentage, with the zero denominator handled once instead of at every call site."""
    part, whole = float(part or 0), float(whole or 0)
    return part / whole * 100 if whole else 0.0


def duration_label(minutes) -> str:
    """Readable duration, scaled to its own magnitude. Mirrors components.duration_label,
    duplicated rather than imported because that module imports Streamlit."""
    m = float(minutes or 0)
    if m < 1:
        return f"{m * 60:.0f} s"
    if m < 60:
        return f"{m:.1f} min"
    return f"{m / 60:.1f} h"


def filename(campaign_id: str, start: dt.date, end: dt.date, ext: str) -> str:
    """A filename that survives every OS. Campaign ids are usually clean slugs or UUIDs, but
    some are neither, and a report is no place to discover that."""
    safe = "".join(c if (c.isalnum() or c in "-_") else "_" for c in str(campaign_id))[:60]
    return f"campaign_{safe}_{start}_{end}.{ext}"


# ------------------------------------------------------------------------------- PDF
# Written through src/minipdf.py, which has no third-party dependency. This was fpdf2, and the
# switch was forced by a measurement rather than a preference: the deployed app runs the
# CONTAINER runtime, whose package set is closed without an External Access Integration it does
# not have. fpdf2 is published in the Snowflake Anaconda channel, but only the WAREHOUSE runtime
# reads environment.yml - so listing it there, which is what we had done, left the PDF button
# permanently disabled on the runtime we actually deploy to. reportlab and weasyprint fail the
# same test, and weasyprint additionally needs cairo and pango system libraries.
#
# minipdf deliberately keeps fpdf2's method names and cell geometry, so everything below is the
# layout as it was tuned, not a rewrite of it.
#
# The layout mirrors the HTML deliberately - same sections, same order, same gating - so the
# two downloads and the page cannot tell different stories about one campaign.
_PDF_INK = (11, 26, 26)
_PDF_MUTED = (84, 106, 106)
_PDF_TEAL = (5, 110, 110)
_PDF_RULE = (230, 236, 236)
_PDF_BAND = (242, 247, 247)
_PAGE_W = 180.0  # A4 width less both margins

# The first three slots of theme.CATEGORICAL_LIGHT, as RGB, and deliberately a COPY rather
# than an import: src/theme.py resolves its palette through st.context.theme, which is a
# Streamlit call, and this module is verified Streamlit-free so a scheduled job can render a
# report with no script run behind it. The light slots are the right ones regardless - a PDF
# has one surface and it is white, so there is no dark variant to resolve to.
#
# Indexed by position in EVENT_BUCKET_ORDER, never by rank, so a campaign whose clicks
# outnumber its page visits does not repaint both slices. Same rule as donut_chart's `order`.
_PDF_MIX_HUES = (
    (29, 148, 133),   # #1d9485 teal   - Page visit
    (145, 55, 151),   # #913797 magenta - Clicks
    (169, 136, 27),   # #a9881b olive   - Form submit
)

# fpdf2's core fonts are latin-1 only and raise outright on anything else, so text is folded
# rather than left to blow up mid-report. The typographic characters this codebase uses in
# captions - em dash, curly quotes, ellipsis - have plain equivalents; anything genuinely
# outside latin-1, such as a CJK campaign name, becomes "?" and is legible as a gap rather
# than a crash. The HTML download carries the same text in full UTF-8 for those cases.
_FOLD = {"—": "-", "–": "-", "‘": "'", "’": "'", "“": '"',
         "”": '"', "…": "...", "·": "-", "→": "->", "✓": "y"}


def _safe(value) -> str:
    text = "" if value is None else str(value)
    for bad, good in _FOLD.items():
        text = text.replace(bad, good)
    return text.encode("latin-1", "replace").decode("latin-1")


_MONTH = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _as_date(value):
    """Snowflake dates arrive as date objects, Timestamps or ISO strings depending on the
    path. Anything with .year is used directly; anything else is parsed as ISO."""
    if hasattr(value, "year") and hasattr(value, "month"):
        return value
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _fmt_date(value, year: bool = False) -> str:
    """"12 Feb" rather than "02-12". ISO is unambiguous to a machine and to nobody else: an
    axis reading 02-12 is either 12 February or 2 December depending on the reader's
    nationality, and a report crossing timezones and teams should not need a convention note.
    The year is added only when the range spans one, where its absence would be the ambiguity.
    """
    d = _as_date(value)
    if d is None:
        return _safe(value)
    return f"{d.day} {_MONTH[d.month - 1]}" + (f" {d.year}" if year else "")


def _fmt_range(start, end) -> str:
    """"12 Jul - 11 Aug 2026", with the year stated once when both ends share it."""
    a, b = _as_date(start), _as_date(end)
    if a is None or b is None:
        return f"{start} to {end}"
    if a.year != b.year:
        return f"{_fmt_date(a, year=True)} - {_fmt_date(b, year=True)}"
    return f"{_fmt_date(a)} - {_fmt_date(b)} {b.year}"


def _pdf_heading(pdf, text, keep: float = 24.0) -> None:
    """A section heading with a hairline under it.

    `keep` is the space the section needs below the heading. Without it fpdf2 breaks the page
    wherever the cursor happens to land, and a heading stranded at the foot of a page with its
    table overleaf is the commonest way a generated PDF looks careless.
    """
    if pdf.get_y() + keep > pdf.h - pdf.b_margin:
        pdf.add_page()
    pdf.ln(4.5)
    pdf.set_font("Helvetica", "B", 10.5)
    pdf.set_text_color(*_PDF_TEAL)
    pdf.cell(0, 5.5, _safe(text), new_x="LMARGIN", new_y="NEXT")
    pdf.set_draw_color(*_PDF_RULE)
    pdf.set_line_width(0.3)
    pdf.line(pdf.l_margin, pdf.get_y() + 0.4, pdf.l_margin + _PAGE_W, pdf.get_y() + 0.4)
    pdf.set_line_width(0.2)
    pdf.ln(1.6)
    pdf.set_text_color(*_PDF_INK)


def _pdf_rule(pdf, keep: float = 30.0) -> None:
    """A full-width divider between two sections that would otherwise run together.

    Every heading already carries a hairline beneath it, which separates a heading from its
    own table - not one section from the next. Pages and Content sit at the end of the report
    as a pair and are the one place that distinction matters: both are "what they looked at",
    both are tables of names and session counts, and read back to back the second looks like
    a continuation of the first. This is heavier than the heading rule and sits in its own
    air, so it reads as a break rather than as another underline.

    `keep` reserves the space a section needs after it, so the divider cannot be the last
    mark on a page with its section overleaf - which would separate nothing at all.
    """
    if pdf.get_y() + keep > pdf.h - pdf.b_margin:
        pdf.add_page()
        return
    pdf.ln(5.0)
    pdf.set_draw_color(*_PDF_RULE)
    pdf.set_line_width(0.6)
    y = pdf.get_y()
    pdf.line(pdf.l_margin, y, pdf.l_margin + _PAGE_W, y)
    pdf.set_line_width(0.2)
    pdf.ln(1.5)


def _pdf_note(pdf, text) -> None:
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(*_PDF_MUTED)
    pdf.multi_cell(_PAGE_W, 4, _safe(text), new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(*_PDF_INK)


def _nice_axis(vmax: float, target: int = 4) -> tuple[float, int]:
    """Axis ceiling and interval count, chosen so every tick is a whole number.

    Picking the ceiling first and dividing by a fixed four gridlines is what produced ticks
    of 37.5 and 112.5 on a chart counting sessions. Choosing the STEP first, from 1/2/5 times
    a power of ten and never below 1, means the labels are always integers and the ceiling
    only exceeds the peak by less than one step.
    """
    import math

    if vmax <= 0:
        return 1.0, 1
    raw = vmax / max(target, 1)
    magnitude = 10 ** math.floor(math.log10(raw)) if raw > 0 else 1
    step = next((m * magnitude for m in (1, 2, 5, 10) if m * magnitude >= raw), 10 * magnitude)
    step = max(step, 1.0)
    intervals = max(int(math.ceil(vmax / step)), 1)
    return step * intervals, intervals


def _pdf_bar_values(pdf, points, values, emphasis, y0, bar_w) -> None:
    """The figure above every column, thinned only where two would actually collide.

    A printed report has no tooltip. A value the reader can only get by measuring a column
    against a gridline is a value they will not get, so the number goes on the chart.

    Two passes, not one. The peak and the trough are reserved FIRST and the rest are then walked
    left to right, so the two figures anyone actually looks for can never be the ones dropped by
    a collision. Measured widths rather than "every Nth": "1" and "1,234" are not the same width,
    which is the same reason the date labels below are walked instead of thinned by index.
    """
    spans: list[tuple[float, float]] = []

    def fits(a: float, b: float) -> bool:
        return all(b + 0.8 < lo or a > hi + 0.8 for lo, hi in spans)

    order = sorted(emphasis) + [i for i in range(len(values)) if i not in emphasis]
    for i in order:
        px, py = points[i]
        strong = i in emphasis
        pdf.set_font("Helvetica", "B" if strong else "", 6)
        tag = f"{values[i]:,.10g}"
        w = pdf.get_string_width(tag)
        left, right, baseline = px - w / 2, px + w / 2, py - 1.4
        if not fits(left, right):
            continue
        # Once the columns are narrower than the figures - a wide date range - a label is wider
        # than its own slot and reaches over its neighbours. Where that neighbour is TALLER the
        # label lands on teal, and muted grey on teal is unreadable. Checked against the actual
        # geometry rather than inferred from the point count, because it depends on the values.
        on_column = any(
            j != i and points[j][1] < baseline
            and points[j][0] + bar_w / 2 > left and points[j][0] - bar_w / 2 < right
            for j in range(len(values))
        )
        if on_column and not strong:
            continue  # a middling day is not worth a number sitting on another day's column
        spans.append((left, right))
        if baseline < y0 + 1:
            # A column reaching the axis ceiling leaves no room above it, so its figure goes
            # inside its own cap rather than climbing over the section heading.
            pdf.set_text_color(255, 255, 255)
            pdf.text(left, py + 3.0, tag)
        elif on_column:
            # Kept, because this is the peak or the trough, and reversed out so it stays legible.
            pdf.set_text_color(255, 255, 255)
            pdf.text(left, baseline, tag)
        else:
            pdf.set_text_color(*(_PDF_TEAL if strong else _PDF_MUTED))
            pdf.text(left, baseline, tag)
    pdf.set_text_color(*_PDF_INK)


def _pdf_series_chart(pdf, title, frame, x_col, y_col, y_title, kind="bar", note=None, height=46.0) -> None:
    """A time series as an actual chart, drawn as vector rather than rasterised.

    Thirty dates in a table is a list of numbers nobody reads; the shape - which days spiked,
    which went quiet - is the whole content, and a table hides it. Drawn with the writer's own
    primitives instead of rendering a PNG through matplotlib: the output stays vector so it
    is crisp at any zoom and adds no kilobytes of bitmap, and it avoids putting a second heavy
    package into a Snowflake environment where install failures are the fragile part.

    `kind` is "bar" or "line", and everything except the mark itself is shared: the axis
    ceiling, the gridlines, the collision-avoiding x labels, the peak/trough annotation and the
    summary line. Written as one function with a branch rather than two functions, because the
    two would drift - the axis logic here has already been corrected twice (a ceiling of 200 for
    a peak of 140, then fractional ticks) and neither fix would have reached a copy.

    The screen chart in src/charts.py is trend_bar for the same section, so the download and the
    page make the same claim about the same numbers.
    """
    if frame is None or len(frame) < 2:
        return
    _pdf_heading(pdf, title)
    if note:
        _pdf_note(pdf, note)

    values = [float(getattr(r, y_col) or 0) for r in frame.itertuples()]
    labels = [str(getattr(r, x_col)) for r in frame.itertuples()]
    top, gridlines = _nice_axis(max(values))

    # Reserve the whole block up front so auto page-break cannot split the axes from the line.
    if pdf.get_y() + height + 10 > pdf.h - pdf.b_margin:
        pdf.add_page()

    gutter = 13.0  # room for y tick labels
    x0 = pdf.l_margin + gutter
    plot_w = _PAGE_W - gutter
    y0 = pdf.get_y() + 2
    plot_h = height - 12  # leave room for x labels beneath

    # Gridlines and y labels first, so the series draws over them.
    pdf.set_font("Helvetica", "", 6.5)
    pdf.set_draw_color(*_PDF_RULE)
    pdf.set_line_width(0.2)
    for i in range(gridlines + 1):
        frac = i / gridlines
        gy = y0 + plot_h - (plot_h * frac)
        pdf.line(x0, gy, x0 + plot_w, gy)
        pdf.set_text_color(*_PDF_MUTED)
        tick = f"{top * frac:,.10g}"
        pdf.text(x0 - 1.5 - pdf.get_string_width(tick), gy + 1.1, tick)

    # Where each day sits. Columns are centred in their own slot so the first and last are
    # fully inside the axes; a line runs edge to edge, because its points ARE the ends.
    if kind == "bar":
        slot = plot_w / len(values)
        # Columns fill their slot, so consecutive days touch. There was a 0.6mm gap here, which
        # is what the house chart guidance prescribes for separating neighbouring bars - adjacent
        # columns were asked for instead, and the figure above each one carries the separating
        # that the air was doing. Days with NO activity are still gaps: those rows are absent
        # from the data rather than zero, which is why this axis is drawn from dates.
        bar_w = slot
        points = [(x0 + i * slot + slot / 2, y0 + plot_h - (v / top) * plot_h) for i, v in enumerate(values)]
        pdf.set_fill_color(*_PDF_TEAL)
        # Full-width columns in one flat colour merge into a single block, which is what the chart
        # looked like. Each now carries a hairline in the PAGE colour: where two columns meet the
        # two strokes overlap into one thin division, and along the outside it is white on white,
        # so it separates without adding any ink that competes with the data. A darker teal
        # outline would do the same on paper but could not be reused on screen, where dark mode
        # puts the series on a near-black surface and a darkened edge disappears into it.
        pdf.set_draw_color(255, 255, 255)
        edge = min(0.25, bar_w * 0.18)
        pdf.set_line_width(edge)
        for px, py in points:
            # A zero-height rect draws nothing, so a day with no sessions would silently vanish
            # rather than reading as a day that was measured and was quiet.
            h = max(y0 + plot_h - py, 0.25)
            # A stroke centred on the edges of a sub-millimetre column would erase the column, so
            # the very quietest days are filled only - their height is the honest signal.
            pdf.rect(px - bar_w / 2, py, bar_w, h, style="DF" if h > 3 * edge else "F")
        pdf.set_line_width(0.2)
    else:
        # One polyline rather than N line() calls so the joins are mitred and the whole path is
        # a single object in the content stream.
        step = plot_w / (len(values) - 1)
        points = [(x0 + i * step, y0 + plot_h - (v / top) * plot_h) for i, v in enumerate(values)]
        pdf.set_fill_color(224, 239, 238)
        pdf.polyline(points + [(points[-1][0], y0 + plot_h), (points[0][0], y0 + plot_h)],
                     fill=True, polygon=True, style="F")
        pdf.set_draw_color(*_PDF_TEAL)
        pdf.set_line_width(0.5)
        pdf.polyline(points)
        pdf.set_line_width(0.2)

    # Peak and trough are the two points anyone asks about, so they are emphasised either way -
    # but a bar chart labels EVERY column and a line chart labels only those two. The difference
    # is the mark: 27 dots each carrying a number is a cloud of text with nothing to sit on,
    # while a column has a cap that holds one.
    hi, lo = values.index(max(values)), values.index(min(values))
    if kind == "bar":
        _pdf_bar_values(pdf, points, values, {hi, lo}, y0, bar_w)
    else:
        pdf.set_fill_color(*_PDF_TEAL)
        pdf.set_font("Helvetica", "B", 6.5)
        pdf.set_text_color(*_PDF_TEAL)
        for idx in {hi, lo}:
            px, py = points[idx]
            pdf.ellipse(px - 0.7, py - 0.7, 1.4, 1.4, style="F")
            tag = f"{values[idx]:,.10g}"
            tx = min(max(px - pdf.get_string_width(tag) / 2, x0), x0 + plot_w - pdf.get_string_width(tag))
            # Above the point by default. Below only when the point is close enough to the top
            # that the label would leave the plot - putting the trough label underneath instead
            # pushed it into the row of date labels, where it read as part of the axis.
            ty = py + 3.6 if (py - 3.0) < y0 else py - 2.4
            pdf.text(tx, ty, tag)

    # X labels as "12 Feb", spaced by measured width rather than by index. Thinning every Nth
    # label still collided on wide ranges, because "3 Sep" and "28 Sep" are not the same width;
    # walking left to right and skipping anything that would overlap the last one drawn is what
    # actually guarantees no collision at any range length.
    dates = [_as_date(v) for v in labels]
    spans_years = bool(dates[0] and dates[-1] and dates[0].year != dates[-1].year)
    pdf.set_font("Helvetica", "", 6.5)
    pdf.set_text_color(*_PDF_MUTED)
    right_edge = -1e9
    for i, lab in enumerate(labels):
        text = _safe(_fmt_date(lab, year=spans_years))
        w = pdf.get_string_width(text)
        # Centred on the mark's own x, taken from points rather than recomputed. Columns are
        # centred in their slot and line vertices sit on the edges, so a shared "i * step" was
        # only ever right for the line - and for bars it also raised a NameError, because the
        # bar branch never defines step.
        lx = points[i][0] - w / 2
        if lx < right_edge + 3 and i not in (0, len(labels) - 1):
            continue
        lx = min(max(lx, pdf.l_margin), pdf.l_margin + _PAGE_W - w)
        if lx < right_edge + 3 and i:
            continue
        pdf.text(lx, y0 + plot_h + 4.4, text)
        right_edge = lx + w
    pdf.set_text_color(*_PDF_INK)

    pdf.set_y(y0 + height - 3)
    total = sum(values)
    pdf.set_font("Helvetica", "", 7.5)
    pdf.set_text_color(*_PDF_MUTED)
    pdf.cell(0, 4, _safe(f"{y_title}: {total:,.10g} over {len(values)} days      "
                         f"Peak {max(values):,.10g} on {_fmt_date(labels[hi], year=spans_years)}      "
                         f"Quietest {min(values):,.10g} on {_fmt_date(labels[lo], year=spans_years)}"),
             new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(*_PDF_INK)


def _pdf_table(pdf, title, frame, label_col, value_col, value_head, extra=None, note=None) -> None:
    if frame is None or not len(frame):
        return
    _pdf_heading(pdf, title)
    if note:
        _pdf_note(pdf, note)

    extra = extra or []
    w_label, w_value = 74.0, 24.0

    def _fit(text: str, width: float) -> str:
        """`text` trimmed to `width`, walking back through the ORIGINAL string.

        Written as a helper because the label and every extra cell need it: this writer's
        cell() neither wraps nor clips, so anything too wide silently overprints its
        neighbour. Trimming the running result instead - label[:-2] + "..." - removes two
        characters and appends three, so the string grows by one per pass and the loop never
        terminates. That form was here, and any label past roughly 49 lowercase characters
        hung PDF generation outright.
        """
        if pdf.get_string_width(text) <= width:
            return text
        keep = len(text)
        while keep > 1 and pdf.get_string_width(text[:keep] + "...") > width:
            keep -= 1
        return text[:keep].rstrip() + "..."

    # Extra columns are sized to their own content, not to a fixed 20mm - both the HEADING,
    # which is derived from the column name ("VIEWS_PER_SESSION" -> "Views Per Session", which
    # ran straight back over the "Views" column beside it), and the widest VALUE, since a host
    # or any other string extra overflows 20mm just as readily. Both were seen in the rendered
    # PDF and are invisible in any assertion about bytes.
    pdf.set_font("Helvetica", "B", 7)
    heads = [pdf.get_string_width(_safe(col.replace("_", " ").title())) for col, _ in extra]
    pdf.set_font("Helvetica", "", 8)
    vals = [max((pdf.get_string_width(_safe(fmt(getattr(r, col)))) for r in frame.itertuples()),
                default=0.0) for col, fmt in extra]
    w_extras = [min(46.0, max(20.0, max(h, v) + 4.0)) for h, v in zip(heads, vals)]
    w_bar = _PAGE_W - w_label - w_value - sum(w_extras)
    # Wide extras eat the bar rather than the label, but only down to a floor - past that the
    # label column gives up the rest, because a 2mm bar is decoration and a truncated label is
    # still a name. Several string extras is the case that reaches this.
    if w_bar < 14.0:
        w_label = max(40.0, w_label - (14.0 - w_bar))
        w_bar = _PAGE_W - w_label - w_value - sum(w_extras)

    def _head(continued: bool = False) -> None:
        """The column strip. Redrawn after a page break, because a table that spills over
        opened the next page on a bare row: "Europe  447" with nothing naming either column.
        Rendering the report and looking at page two is what surfaced that - it is invisible in
        any assertion about bytes."""
        if continued:
            pdf.set_font("Helvetica", "", 7)
            pdf.set_text_color(*_PDF_MUTED)
            pdf.cell(0, 4, _safe(f"{title} (continued)"), new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "B", 7)
        pdf.set_text_color(*_PDF_MUTED)
        pdf.set_fill_color(*_PDF_BAND)
        pdf.cell(w_label, 5.4, "  " + _safe(label_col.replace("_", " ").title()), fill=True)
        pdf.cell(w_value, 5.4, _safe(value_head) + "  ", align="R", fill=True)
        pdf.cell(w_bar, 5.4, "", fill=True)
        for (col, _), w in zip(extra, w_extras):
            pdf.cell(w, 5.4, _safe(col.replace("_", " ").title()) + "  ", align="R", fill=True)
        pdf.ln()
        pdf.set_text_color(*_PDF_INK)
        pdf.set_font("Helvetica", "", 8)

    _head()

    top = float(max(float(getattr(r, value_col) or 0) for r in frame.itertuples())) or 1.0
    for n, row in enumerate(frame.itertuples()):
        # Rows are struck one at a time so a long table can break across pages mid-body, and
        # the header follows it over so the continuation is readable on its own.
        if pdf.get_y() + 6 > pdf.h - pdf.b_margin:
            pdf.add_page()
            _head(continued=True)
        value = float(getattr(row, value_col) or 0)
        label = _fmt_date(getattr(row, label_col)) if _as_date(getattr(row, label_col)) else _safe(getattr(row, label_col))
        # Trimmed to what the column can hold. Campaign and asset names reach 140 characters,
        # page paths run longer still, and the writer overprints the next column rather than
        # wrapping. See _fit for the loop this replaced and what it did.
        label = _fit(label, w_label - 5)
        y = pdf.get_y()
        # Alternating tint instead of a rule under every row: at eight point, a hairline every
        # 5mm turns a short table into a grid, while a band lets the eye track across a wide one.
        if n % 2:
            pdf.set_fill_color(250, 252, 252)
            pdf.rect(pdf.l_margin, y, _PAGE_W, 5.4, style="F")
        pdf.cell(w_label, 5.4, "  " + label)
        pdf.cell(w_value, 5.4, f"{value:,.10g}" + "  ", align="R")
        pdf.set_fill_color(*_PDF_TEAL)
        pdf.rect(pdf.get_x() + 1, y + 1.6, max((value / top) * (w_bar - 3), 0.4), 2.2, style="F")
        pdf.cell(w_bar, 5.4, "")
        for (col, fmt), w in zip(extra, w_extras):
            pdf.cell(w, 5.4, _fit(_safe(fmt(getattr(row, col))), w - 3) + "  ", align="R")
        pdf.ln()


def _pdf_card_at(pdf, x, y, w, h, label, value) -> None:
    """One bordered KPI card at an absolute position, leaving the cursor where it found it.

    Split out of _pdf_cards so the mix row can place cards in a column beside the ring
    instead of in _pdf_cards' full-width grid. Both call this, so a card cannot come to look
    different depending on which layout drew it.
    """
    pdf.set_fill_color(*_PDF_BAND)
    pdf.set_draw_color(*_PDF_RULE)
    pdf.rect(x, y, w, h, style="DF")
    pdf.set_xy(x + 2.6, y + 1.8)
    pdf.set_font("Helvetica", "", 6.5)
    pdf.set_text_color(*_PDF_MUTED)
    pdf.cell(w - 5, 3.4, _safe(str(label).upper()))
    pdf.set_xy(x + 2.6, y + 5.6)
    pdf.set_font("Helvetica", "B", 12.5)
    pdf.set_text_color(*_PDF_INK)
    pdf.cell(w - 5, 6.4, _safe(value))
    pdf.set_text_color(*_PDF_INK)


def _pdf_donut(pdf, cx, cy, r_out, r_in, slices) -> None:
    """A part-to-whole ring, drawn as filled annular sectors.

    minipdf has no arc primitive, so each sector is one polygon: the outer edge swept
    clockwise from the slice's start angle, then the inner edge swept back. Tracing the two
    edges in opposite directions is what makes the hole a hole - the `f` operator fills by
    the nonzero winding rule, so the inner loop cancels the outer one rather than filling
    over it. Drawing a disc and then covering its middle with a white circle would look
    identical on screen and wrong on any non-white paper or background.

    Angles start at 12 o'clock and increase clockwise, matching Vega's default theta, so the
    ring in the PDF and the ring on the page put the same slice in the same place.

    `slices` is (label, value, rgb). One object per sector keeps the seams mitred.
    """
    total = float(sum(v for _, v, _ in slices))
    if total <= 0:
        return
    angle = 0.0
    for _label, value, rgb in slices:
        sweep = float(value) / total * 2.0 * math.pi
        if sweep <= 0:
            continue
        # ~2 degrees per segment: below that the facets show on a 34mm ring at print size.
        steps = max(2, int(math.ceil(sweep / 0.035)))
        outer = [(cx + r_out * math.sin(angle + sweep * j / steps), cy - r_out * math.cos(angle + sweep * j / steps)) for j in range(steps + 1)]
        inner = [(cx + r_in * math.sin(angle + sweep * j / steps), cy - r_in * math.cos(angle + sweep * j / steps)) for j in range(steps, -1, -1)]
        pdf.set_fill_color(*rgb)
        pdf.polyline(outer + inner, polygon=True, style="F")
        angle += sweep


def _pdf_mix_row(pdf, cards, mix, total_events) -> None:
    """The page's top row: stat cards stacked at the left, the event-mix ring at the right.

    This is the one section of the report that mirrors a page layout rather than restating a
    chart as a table, and it is here because the page moved its Events total INTO the ring's
    hole. Printing a table instead would have left the PDF with no total at all in the place
    the reader now looks for one.

    Falls back to the plain card grid when the mix is empty or sums to zero - a campaign with
    no bucketed events would otherwise get a blank box where the ring should be.
    """
    rows = []
    if mix is not None and len(mix) and float(mix["EVENTS"].sum()) > 0:
        by_bucket = {str(r.BUCKET): float(r.EVENTS or 0) for r in mix.itertuples()}
        rows = [(name, by_bucket.get(name, 0.0), _PDF_MIX_HUES[i % len(_PDF_MIX_HUES)]) for i, name in enumerate(EVENT_BUCKET_ORDER) if by_bucket.get(name, 0.0) > 0]
    if not rows:
        _pdf_cards(pdf, cards, per_row=max(1, len(cards)))
        return

    gap = 4.0
    left_w = (_PAGE_W - gap) / 3.0
    right_w = _PAGE_W - left_w - gap
    card_h = 13.5
    box_h = 46.0
    if pdf.get_y() + box_h + 4.0 > pdf.h - pdf.b_margin:
        pdf.add_page()
    top = pdf.get_y()
    x0 = pdf.l_margin
    for i, (label, value) in enumerate(cards):
        _pdf_card_at(pdf, x0, top + i * (card_h + 3.0), left_w, card_h, label, value)

    bx = x0 + left_w + gap
    pdf.set_fill_color(255, 255, 255)
    pdf.set_draw_color(*_PDF_RULE)
    pdf.rect(bx, top, right_w, box_h, style="D")

    r_out = 16.0
    r_in = r_out * 0.6
    cx = bx + 9.0 + r_out
    cy = top + box_h / 2.0
    _pdf_donut(pdf, cx, cy, r_out, r_in, rows)

    # The total goes in the hole, which is the whole reason the ring replaced a KPI card.
    #
    # Sized to fit rather than set at a fixed point size: the hole is 19.2mm across and a
    # seven-figure total at 11pt is wider than that. Overflow here does not clip, it draws
    # the number straight over its own ring, so the size is stepped down until minipdf
    # measures it as fitting. Campaign totals in this data reach seven figures.
    shown = float(sum(v for _, v, _ in rows))
    centre = _safe(f"{int(round(shown)):,}")
    hole_w = r_in * 2.0 - 2.0
    size = 11.0
    pdf.set_font("Helvetica", "B", size)
    while size > 5.5 and pdf.get_string_width(centre) > hole_w:
        size -= 0.5
        pdf.set_font("Helvetica", "B", size)
    pdf.set_text_color(*_PDF_INK)
    pdf.set_xy(cx - r_out, cy - 4.6)
    pdf.cell(r_out * 2.0, 4.6, centre, align="C")
    pdf.set_font("Helvetica", "", 5.0)
    pdf.set_text_color(*_PDF_MUTED)
    pdf.set_xy(cx - r_out, cy - 0.2)
    pdf.cell(r_out * 2.0, 3.2, _safe("TOTAL EVENTS"), align="C")

    # The legend carries the count and the share. Same reasoning as the page's: an arc thin
    # enough to be unlabellable still has a swatch here, and the PDF has no tooltip to fall
    # back on, so anything not in this list is simply not readable anywhere.
    lx = bx + r_out * 2.0 + 15.0
    lw = bx + right_w - 5.0 - lx
    ly = cy - (len(rows) * 7.0) / 2.0
    for label, value, rgb in rows:
        pdf.set_fill_color(*rgb)
        pdf.rect(lx, ly + 1.6, 3.0, 3.0, style="F")
        pdf.set_font("Helvetica", "", 7.5)
        pdf.set_text_color(*_PDF_INK)
        pdf.set_xy(lx + 4.6, ly)
        pdf.cell(lw - 4.6, 6.2, _safe(label))
        share = value / shown * 100.0 if shown else 0.0
        # A real count must never print as "0%" - see donut_chart's _legend, same rule.
        share_txt = f"{share:.1f}%" if share >= 0.1 else "<0.1%"
        pdf.set_text_color(*_PDF_MUTED)
        pdf.set_xy(lx + 4.6, ly)
        pdf.cell(lw - 4.6, 6.2, _safe(f"{int(round(value)):,}  ({share_txt})"), align="R")
        ly += 7.0

    # Only fires if the buckets stop being exhaustive. They are today - see
    # campaign_event_mix_sql - so this is a guard against the two totals silently drifting,
    # not a caption the reader is expected to meet.
    missing = int(total_events) - int(round(shown))
    pdf.set_y(top + box_h)
    pdf.set_text_color(*_PDF_INK)
    pdf.ln(1.0)
    if missing > 0:
        _pdf_note(pdf, f"{int(round(shown)):,} of this campaign's {int(total_events):,} events fall into these three buckets; {missing:,} do not.")


def _pdf_cards(pdf, pairs, per_row: int = 3) -> None:
    """KPIs as bordered cards rather than bare text in a grid.

    The previous version printed a row of labels, then a row of values, juggling ln() by hand;
    the two rows drifted apart and it read as four loose lines rather than six figures. A card
    binds each label to its number, and the gutter between cards does the separating that
    whitespace alone was failing to do.
    """
    gap = 3.0
    w = (_PAGE_W - gap * (per_row - 1)) / per_row
    h = 13.5
    for i, (label, value) in enumerate(pairs):
        col = i % per_row
        if col == 0 and i:
            pdf.ln(h + gap)
        x = pdf.l_margin + col * (w + gap)
        y = pdf.get_y()
        _pdf_card_at(pdf, x, y, w, h, label, value)
        pdf.set_xy(pdf.l_margin, y)
    pdf.ln(h + 1)
    pdf.set_text_color(*_PDF_INK)


class _Doc:
    """Builds the canvas with a running footer.

    A multi-page report with no page numbers and no campaign name on page two is a stack of
    loose sheets once printed, so every page carries both.

    The footer is a plain callable rather than an overridden method, which is the one place
    this differs from the fpdf2 version it replaced. src/minipdf.py calls it once per page at
    output time, when the page total is already known - so "Page 3 of 7" needs no `{nb}`
    placeholder patched in afterwards, and the right-aligned half is measured against the real
    string rather than against a two-character stand-in.
    """

    @staticmethod
    def build(title: str, window: str):
        from src.minipdf import Canvas

        def footer(pdf):
            pdf.set_y(-11)
            pdf.set_draw_color(*_PDF_RULE)
            pdf.line(pdf.l_margin, pdf.get_y() - 1.5, pdf.l_margin + _PAGE_W, pdf.get_y() - 1.5)
            pdf.set_font("Helvetica", "", 6.5)
            pdf.set_text_color(*_PDF_MUTED)
            pdf.cell(_PAGE_W * 0.7, 4, _safe(f"{title}   ·   {window}"))
            pdf.cell(_PAGE_W * 0.3, 4, f"Page {pdf.page_no()} of {pdf.total_pages}", align="R")

        pdf = Canvas(footer=footer, title=_safe(f"{title} - campaign report"))
        pdf.set_auto_page_break(auto=True, margin=18)
        pdf.set_margins(15, 14, 15)
        return pdf


def _pdf_title_block(pdf, data) -> None:
    """Campaign name against a teal rule, then its identity as labelled facts.

    The identity line used to be one run-on string padded with double spaces, which is how it
    read: an undifferentiated sentence. Separating the facts with a bullet and setting the
    labels in the muted grey lets the eye pick out the one it wants.
    """
    y = pdf.get_y()
    pdf.set_fill_color(*_PDF_TEAL)
    pdf.rect(pdf.l_margin, y + 0.8, 1.6, 8.4, style="F")
    pdf.set_xy(pdf.l_margin + 4.2, y)
    pdf.set_font("Helvetica", "B", 16)
    pdf.set_text_color(*_PDF_INK)
    pdf.multi_cell(_PAGE_W - 4.2, 7.4, _safe(data["label"]), new_x="LMARGIN", new_y="NEXT")

    k = data["kpis"]
    facts = [f"ID {data['campaign_id']}"]
    if k["TENANT"]:
        facts.append(f"Tenant {k['TENANT']}")
    facts.append(f"Reporting on {_fmt_range(data['start'], data['end'])}")
    if data["status"] != STATUS_MISSING and _as_date(k["FIRST_SEEN"]):
        window_days = (_as_date(data["end"]) - _as_date(data["start"])).days + 1
        active = f"Active on {int(k['ACTIVE_DAYS']):,} of {window_days:,} days"
        # The campaign's own span is only worth printing when it differs from the reporting
        # range. A campaign running the full window printed the identical span twice, one
        # labelled "Reporting on" and one "Active", which reads as two facts and is one.
        if _as_date(k["FIRST_SEEN"]) != _as_date(data["start"]) or _as_date(k["LAST_SEEN"]) != _as_date(data["end"]):
            active = f"Ran {_fmt_range(k['FIRST_SEEN'], k['LAST_SEEN'])}, {active.lower()}"
        facts.append(active)
    pdf.set_x(pdf.l_margin + 4.2)
    pdf.set_font("Helvetica", "", 7.5)
    pdf.set_text_color(*_PDF_MUTED)
    pdf.multi_cell(_PAGE_W - 4.2, 4, _safe("   ·   ".join(facts)), new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(*_PDF_INK)
    pdf.ln(2.5)


def to_pdf(data: dict) -> bytes:
    """The same report as to_html, as a PDF, with no third-party dependency.

    Takes no `link`, where to_html does: the PDF used to print the live dashboard URL in a
    closing note and no longer prints anything there, so the argument was accepted and ignored -
    a worse outcome than not accepting it, because a caller passing one would believe it landed.

    The writer is imported lazily so that a fault in it disables one download button rather
    than taking the campaign page with it - the same containment the fpdf2 import had, kept
    now that the reason has changed from "the package may be absent" to "this is our own code".
    """
    k = data["kpis"]
    window = _fmt_range(data["start"], data["end"])
    pdf = _Doc.build(data["label"], window)
    pdf.add_page()
    pdf.set_text_color(*_PDF_INK)
    _pdf_title_block(pdf, data)

    if data["status"] == STATUS_MISSING:
        if data.get("reason") == "wrong_window":
            f = data["lookup"]
            msg = (f"This campaign recorded nothing between {window}. It ran "
                   f"{_fmt_range(f['FIRST_EVER'], f['LAST_EVER'])} with {int(f['EVENTS_EVER']):,} events - "
                   f"widen the date range to see it.")
        else:
            msg = f"No campaign with the ID {data['campaign_id']} exists in the tracked data."
        _pdf_note(pdf, msg)
        return bytes(pdf.output())

    if data["status"] == STATUS_NO_SESSIONS:
        _pdf_note(pdf, f"{int(k['EVENTS']):,} events recorded between {window}, but no identifiable "
                       f"sessions: session IDs were not captured on campaign rows before November 2025.")
        return bytes(pdf.output())

    s = int(k["SESSIONS"])
    # Two cards and the ring, matching the page exactly. The row used to hold six cards, and
    # four of them have since come off the page: Events moved into the ring's hole, and
    # Median duration, Engaged and Converted were dropped because each restated a figure the
    # report already carries with its workings - Median duration in Session duration below,
    # Engaged and Converted as the second and third stages of the Engagement funnel, where
    # they appear as counts nested under the sessions they are a share of rather than as two
    # bare percentages. Nothing is lost from the PDF; it stops being said twice.
    _pdf_mix_row(pdf, [
        ("Sessions", f"{s:,}"),
        ("Events / session", f"{float(k['EVENTS']) / s:,.1f}"),
    ], data.get("event_mix"), int(k["EVENTS"]))

    _conc = concentration(k)
    if _conc is not None:
        _pdf_note(pdf, f"{_conc:.0f}% of these {s:,} sessions came from a single network address "
                       f"({int(k['DISTINCT_IPS']):,} addresses in total). Likely one organisation or an "
                       f"automated client rather than {s:,} separate visitors - an address is not a person.")

    # A chart, not a table. A single-day range degenerates to one point with nothing to
    # connect, so it falls back to the tabular form rather than drawing an empty axis.
    daily = data.get("daily")
    if daily is not None and len(daily) >= 2:
        _pdf_series_chart(pdf, "Activity over time", daily, "EVENT_DATE", "SESSION_COUNT", "Sessions", kind="bar")
    else:
        _pdf_table(pdf, "Activity by day", daily, "EVENT_DATE", "SESSION_COUNT", "Sessions")
    _pdf_table(pdf, "Session duration", data.get("duration"), "BAND", "SESSIONS", "Sessions",
               note=(f"Span between a session's first and last event, not time spent reading. "
                     f"{int(k['INSTANT_SESSIONS']):,} of {s:,} sessions hold a single event."))
    # Action reach is gone from here as well as from the page. It was a second breakdown whose
    # rows deliberately did NOT sum to the funnel above them, and explaining that cost more than
    # the rows paid back. The consent figures move onto the funnel, where they belong: they are
    # the reason its second stage is lower than a reader might expect.
    _consent = (f" Cookie banner: {int(k['CONSENT_YES_SESSIONS']):,} accepted, "
                f"{int(k['CONSENT_NO_SESSIONS']):,} rejected - counted as neither engagement nor "
                f"conversion.") if (int(k["CONSENT_YES_SESSIONS"]) or int(k["CONSENT_NO_SESSIONS"])) else ""
    # Stated, not charted: the funnel counts SESSIONS and every stage must nest inside the
    # one above it, so a session+page figure cannot become a fourth row without the bottom
    # row being able to exceed the row it is drawn from. Shown only when the two differ.
    _leadpages = (f" Those {int(k['LEAD_SESSIONS']):,} converting sessions submitted across {int(k['LEAD_PAGE_SUBMITS']):,} distinct session-and-URL combinations.") if int(k["LEAD_PAGE_SUBMITS"]) > int(k["LEAD_SESSIONS"]) else ""
    _funnel_note = ("Each stage is a subset of the one above it." + _consent + _leadpages) if (_consent or _leadpages) else None
    _pdf_table(pdf, "Engagement funnel", data.get("funnel"), "STAGE", "SESSIONS", "Sessions",
               note=_funnel_note)
    _pdf_table(pdf, "Where they are", data.get("regions"), "REGION", "SESSIONS", "Sessions",
               note="From the browser timezone, the only location signal in the data.")
    _pdf_table(pdf, "Accounts reached", data.get("companies"), "COMPANY", "SESSIONS", "Sessions",
               extra=[("PEOPLE", lambda v: f"{int(v or 0):,}")],
               note="Company domains only; individual addresses are never included.")
    # The withholding is stated in the section it applies to, not in a closing note. It used to
    # ride along in the footer text that has since been dropped, which made it the one privacy
    # disclosure in the report whose placement depended on an unrelated decoration - and
    # _pdf_table draws nothing at all for an empty frame, so without this the section simply
    # vanished and the reader had no way to tell "withheld" from "no identified visitors".
    people = int(k["PEOPLE"])
    companies = data.get("companies")
    if (companies is None or not len(companies)) and 0 < people < IDENTITY_FLOOR:
        _pdf_heading(pdf, "Accounts reached")
        _pdf_note(pdf, f"Company breakdown withheld: only {people} identified "
                       f"{'person' if people == 1 else 'people'} in this range, too few to name a "
                       f"company without describing an individual.")
    _pdf_table(pdf, "How they arrived", data.get("sources"), "SOURCE_GROUP", "EVENTS", "Events")
    _pdf_table(pdf, "External referrers", data.get("referrers"), "REFERRER", "EVENTS", "Events")
    _pdf_table(pdf, "AI model mix", data.get("ai"), "MODEL", "REQUESTS", "Requests",
               extra=[("SESSIONS", lambda v: f"{int(v or 0):,}")])

    # What they looked at, kept together at the end. Pages and Content are one question asked at
    # two levels - the page is the container, the asset is what was opened on it - and they are
    # also the two longest tables here, up to twelve rows each. Sitting mid-report they pushed
    # the short, comparable sections apart; at the end the reader gets the whole shape of the
    # campaign first and the inventory afterwards. PDF read depth follows Content because it is
    # per-asset: it names assets, so it cannot precede the table that lists them.
    _pdf_table(pdf, "Pages viewed", data.get("pages"), "PAGE", "SESSIONS", "Sessions",
               extra=[("HOST", lambda v: _safe(v or "")),
                      ("VIEWS", lambda v: f"{int(v or 0):,}"),
                      ("VIEWS_PER_SESSION", lambda v: f"{float(v or 0):,.1f}")],
               note=("Labelled by path, with the host alongside. Query strings are stripped; "
                     "localhost and iframe pages are left out. One session can visit several "
                     "pages, so the shares do not sum to 100%."))
    # Drawn only when BOTH sides exist. A divider above an absent Content section would be a
    # rule with nothing under it, and one below an absent Pages section would open the block
    # with a line - _pdf_table renders nothing at all for an empty frame, so neither is visible
    # from here without asking.
    if data.get("pages") is not None and len(data.get("pages", [])) \
            and data.get("assets") is not None and len(data.get("assets", [])):
        _pdf_rule(pdf)
    _pdf_table(pdf, "Content by reach", data.get("assets"), "ASSET", "SESSIONS", "Sessions",
               extra=[("EVENTS_PER_SESSION", lambda v: f"{float(v or 0):,.1f}")])
    _pdf_table(pdf, "PDF read depth", data.get("depth"), "ASSET", "AVG_PAGE_REACHED", "Avg page",
               extra=[("DEEPEST_PAGE", lambda v: f"{int(v or 0):,}")])

    # No closing block. The date window and the campaign name are on every page already, in the
    # running footer, so a "Covers <window>" line at the end restated what the page it sat on was
    # already saying - and a live URL printed in a document meant to be filed or forwarded is
    # noise on paper and unclickable in most readers.
    return bytes(pdf.output())


# ------------------------------------------------------------------------------ HTML
# NOT reachable from the UI. The HTML download button was removed at the point the PDF landed,
# so the only callers today are the test suite and the scheduled email planned next. It is
# kept rather than deleted because SYSTEM$SEND_EMAIL sends a text/html BODY and supports no
# attachments, so an emailed report has to be HTML no matter what the download offers - and
# rebuilding this later, separately from collect(), is exactly how the page and the email
# would come to disagree.
#
# Tables with CSS-width bars, not charts, for the same reason: Outlook strips inline SVG and
# ignores much of the CSS an Altair or vector chart would need, so bars built from a div width
# are what actually arrives, and they degrade to a readable table when CSS is dropped
# entirely. The PDF is free to draw real vector charts because nothing strips a PDF.
_CSS = """
body{margin:0;padding:24px;background:#fff;color:#0b1a1a;
 font:15px/1.6 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif}
.wrap{max-width:860px;margin:0 auto}
h1{font-size:26px;margin:0 0 4px;letter-spacing:-.01em}
h2{font-size:17px;margin:34px 0 10px;padding-bottom:6px;border-bottom:1px solid #e6ecec}
.sub{color:#546a6a;margin:0 0 22px;font-size:14px}
.kpis{display:flex;flex-wrap:wrap;gap:10px;margin:0 0 8px}
.kpi{border:1px solid #e6ecec;border-radius:6px;padding:10px 14px;min-width:132px;background:#f7fafa}
.kpi .l{font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:#546a6a}
.kpi .v{font-size:21px;font-weight:600;font-variant-numeric:tabular-nums}
table{border-collapse:collapse;width:100%;font-size:14px}
th{text-align:left;font-size:11px;letter-spacing:.08em;text-transform:uppercase;
 color:#546a6a;font-weight:500;padding:6px 10px;background:#f2f7f7}
td{padding:6px 10px;border-bottom:1px solid #eef3f3;vertical-align:middle}
td.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.bar{background:#056e6e;height:11px;border-radius:2px;min-width:2px}
.barcell{width:42%}
.mix{display:flex;flex-wrap:wrap;align-items:center;gap:22px;border:1px solid #e6ecec;
 border-radius:6px;padding:16px 18px;margin:10px 0 8px}
.mix .ring{flex:0 0 auto}
.mix .leg{flex:1 1 260px;min-width:240px}
.mix .leg table{font-size:13px}
.mix .leg td{border-bottom:none;padding:4px 0}
.mix .sw{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:8px;
 vertical-align:-1px}
.note{color:#546a6a;font-size:13px;margin:8px 0 0}
.warn{background:#fdf6e3;border:1px solid #e8d9a8;border-radius:6px;padding:14px 16px;margin:18px 0}
footer{margin-top:34px;padding-top:12px;border-top:1px solid #e6ecec;color:#546a6a;font-size:12px}
"""


def _esc(v) -> str:
    return html.escape("" if v is None else str(v), quote=True)


def _bar_rows(frame, label_col, value_col, extra=None, link_col=None) -> str:
    """`link_col` turns the label into an anchor pointing at that column's URL.

    Only the Pages section passes it, and only because a path on its own - "/logitech/" -
    is a label rather than an address: it reads fine and pastes nowhere. campaign_pages_sql
    returns the absolute URL beside it for exactly this. The href is escaped with quote=True
    like every other value here; it arrives from PARSE_URL on our own tracking data, but an
    unescaped attribute is a habit worth not having.
    """
    if frame is None or not len(frame):
        return ""
    top = float(max(float(v or 0) for v in frame[value_col])) or 1.0
    out = []
    for row in frame.itertuples():
        label = _esc(getattr(row, label_col))
        if link_col:
            href = _esc(getattr(row, link_col, "") or "")
            if href:
                label = f"<a href='{href}'>{label}</a>"
        value = float(getattr(row, value_col) or 0)
        width = max(value / top * 100.0, 0.0)
        cells = f"<td>{label}</td><td class='n'>{value:,.10g}</td>"
        cells += f"<td class='barcell'><div class='bar' style='width:{width:.1f}%'></div></td>"
        if extra:
            for col, fmt in extra:
                cells += f"<td class='n'>{fmt(getattr(row, col))}</td>"
        out.append(f"<tr>{cells}</tr>")
    return "".join(out)


def _table(title, frame, label_col, value_col, value_head, extra=None, note=None, link_col=None) -> str:
    if frame is None or not len(frame):
        return ""
    head = f"<th>{_esc(label_col.replace('_', ' ').title())}</th><th class='n'>{_esc(value_head)}</th><th></th>"
    if extra:
        for col, _ in extra:
            head += f"<th class='n'>{_esc(col.replace('_', ' ').title())}</th>"
    body = _bar_rows(frame, label_col, value_col, extra, link_col)
    note_html = f"<p class='note'>{_esc(note)}</p>" if note else ""
    return f"<h2>{_esc(title)}</h2>{note_html}<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


_HTML_MIX_HUES = ("#1d9485", "#913797", "#a9881b")


def _html_donut(mix, total_events) -> str:
    """The event-mix ring, as inline SVG with the split repeated as a legend beside it.

    The legend is not decoration and not a fallback afterthought: Gmail strips inline SVG
    from a message body, and this function's output has to work as an email as well as a
    download. A client that drops the ring leaves a reader with the complete split in text;
    a client that keeps it shows the same chart the page does. Neither one loses a number.

    Slices are stroked arcs on one circle rather than filled paths - stroke-dasharray gives
    the ring its hole for free, and the -90 degree rotation starts the first slice at 12
    o'clock so it matches both donut_chart and _pdf_donut.
    """
    if mix is None or not len(mix) or float(mix["EVENTS"].sum()) <= 0:
        return ""
    by_bucket = {str(r.BUCKET): float(r.EVENTS or 0) for r in mix.itertuples()}
    rows = [(name, by_bucket.get(name, 0.0), _HTML_MIX_HUES[i % len(_HTML_MIX_HUES)]) for i, name in enumerate(EVENT_BUCKET_ORDER) if by_bucket.get(name, 0.0) > 0]
    if not rows:
        return ""
    shown = sum(v for _, v, _ in rows)
    r = 54.0
    circ = 2.0 * math.pi * r
    arcs, legend, offset = [], [], 0.0
    for label, value, hue in rows:
        seg = value / shown * circ
        arcs.append(f"<circle cx='80' cy='80' r='{r:g}' fill='none' stroke='{hue}' stroke-width='28' stroke-dasharray='{seg:.3f} {circ - seg:.3f}' stroke-dashoffset='{-offset:.3f}'></circle>")
        offset += seg
        share = value / shown * 100.0
        share_txt = f"{share:.1f}%" if share >= 0.1 else "&lt;0.1%"
        legend.append(f"<tr><td><span class='sw' style='background:{hue}'></span>{_esc(label)}</td><td class='n'>{int(round(value)):,}</td><td class='n'>{share_txt}</td></tr>")
    # Same fit-to-hole rule as the PDF. The hole is 81 user units across; Helvetica digits
    # run .556em and a comma .278em, so a seven-figure total at 24 would overrun the ring.
    # SVG does not clip either, so this is sized down rather than left to overlap.
    centre_txt = f"{int(round(shown)):,}"
    em = sum(0.278 if ch == "," else 0.556 for ch in centre_txt) or 1.0
    centre_size = min(24.0, round(74.0 / em, 1))
    svg = ("<svg class='ring' width='160' height='160' viewBox='0 0 160 160' role='img' "
           f"aria-label='Event mix: {_esc(', '.join(n for n, _, _ in rows))}'>"
           f"<g transform='rotate(-90 80 80)'>{''.join(arcs)}</g>"
           f"<text x='80' y='80' text-anchor='middle' font-size='{centre_size:g}' font-weight='600' fill='#0b1a1a'>{int(round(shown)):,}</text>"
           "<text x='80' y='96' text-anchor='middle' font-size='8' letter-spacing='.6' "
           "fill='#546a6a'>TOTAL EVENTS</text></svg>")
    # See _pdf_mix_row: the buckets are exhaustive today, so this guards a drift rather than
    # describing a known exclusion.
    missing = int(total_events) - int(round(shown))
    gap = f"<p class='note'>{int(round(shown)):,} of this campaign's {int(total_events):,} events fall into these three buckets; {missing:,} do not.</p>" if missing > 0 else ""
    return (f"<div class='mix'>{svg}<div class='leg'><table><tbody>"
            f"{''.join(legend)}</tbody></table></div></div>{gap}")


def _kpi(label, value) -> str:
    return f"<div class='kpi'><div class='l'>{_esc(label)}</div><div class='v'>{_esc(value)}</div></div>"


def to_html(data: dict, link: str = "") -> str:
    """A self-contained HTML report. No JavaScript, no external assets, no attachments -
    so the same bytes serve as a download and as the body of a scheduled email."""
    k = data["kpis"]
    label, cid = _esc(data["label"]), _esc(data["campaign_id"])
    window = f"{data['start']} to {data['end']}"

    if data["status"] == STATUS_MISSING:
        if data.get("reason") == "wrong_window":
            f = data["lookup"]
            msg = (f"Campaign <code>{cid}</code> has no activity between {window}. "
                   f"It ran <b>{_esc(f['FIRST_EVER'])} to {_esc(f['LAST_EVER'])}</b> "
                   f"with {int(f['EVENTS_EVER']):,} events.")
        else:
            msg = f"No campaign <code>{cid}</code> exists in the tracked data."
        body = f"<h1>{label}</h1><div class='warn'>{msg}</div>"
        return _document(label, body, window, link)

    if data["status"] == STATUS_NO_SESSIONS:
        body = (f"<h1>{label}</h1><p class='sub'>Campaign ID {cid}</p><div class='warn'>"
                f"{int(k['EVENTS']):,} events recorded between {window}, but no identifiable "
                f"sessions: session IDs were not captured on campaign rows before November 2025, "
                f"so only event counts exist this far back.</div>")
        return _document(label, body, window, link)

    s = int(k["SESSIONS"])
    parts = [f"<h1>{label}</h1>"]
    ident = f"Campaign ID <code>{cid}</code>"
    if k["TENANT"]:
        ident += f" &middot; Tenant <code>{_esc(k['TENANT'])}</code>"
    ident += (f" &middot; ran {_esc(k['FIRST_SEEN'])} to {_esc(k['LAST_SEEN'])}"
              f" &middot; active on {int(k['ACTIVE_DAYS']):,} days")
    parts.append(f"<p class='sub'>{ident}</p>")

    # Same two cards and the same ring as the page and the PDF - see the note on _pdf_mix_row
    # for why the other four cards came off rather than being kept here alone.
    parts.append("<div class='kpis'>"
                 + _kpi("Sessions", f"{s:,}")
                 + _kpi("Events / session", f"{float(k['EVENTS']) / s:,.1f}")
                 + "</div>")
    parts.append(_html_donut(data.get("event_mix"), int(k["EVENTS"])))

    _conc = concentration(k)
    if _conc is not None:
        _msg = (f"{_conc:.0f}% of these {s:,} sessions came from a single network address "
                f"({int(k['DISTINCT_IPS']):,} addresses in total). Likely one organisation or an "
                f"automated client rather than {s:,} separate visitors - an address is not a person.")
        parts.append(f"<p class='note'>{_esc(_msg)}</p>")
    parts.append(_table("Activity by day", data.get("daily"), "EVENT_DATE", "SESSION_COUNT", "Sessions"))
    parts.append(_table(
        "Session duration", data.get("duration"), "BAND", "SESSIONS", "Sessions",
        note=(f"Span between a session's first and last event, not time spent reading. "
              f"{int(k['INSTANT_SESSIONS']):,} of {s:,} sessions hold a single event.")))
    # Action reach dropped here too, so the page, the PDF and the emailed body describe the same
    # sections. Leaving it in the email alone would recreate exactly the drift this module exists
    # to prevent - and collect() no longer fetches it, so there would be nothing to render.
    _leadpages = (f" Those {int(k['LEAD_SESSIONS']):,} converting sessions submitted across {int(k['LEAD_PAGE_SUBMITS']):,} distinct session-and-URL combinations.") if int(k["LEAD_PAGE_SUBMITS"]) > int(k["LEAD_SESSIONS"]) else ""
    parts.append(_table("Engagement funnel", data.get("funnel"), "STAGE", "SESSIONS", "Sessions",
                        note="Each stage is a subset of the one above it." + _leadpages))
    parts.append(_table("Where they are", data.get("regions"), "REGION", "SESSIONS", "Sessions",
                        note="From the browser timezone, the only location signal in the data."))
    # HOST goes through _esc, unlike every other extra here: _bar_rows interpolates a
    # formatter's output straight into the row, unescaped, and this is the first extra
    # carrying text rather than a number.
    parts.append(_table("Pages viewed", data.get("pages"), "PAGE", "SESSIONS", "Sessions",
                        extra=[("HOST", lambda v: _esc(v or "")),
                               ("VIEWS", lambda v: f"{int(v or 0):,}"),
                               ("VIEWS_PER_SESSION", lambda v: f"{float(v or 0):,.1f}")],
                        link_col="URL",
                        note=("Each page links to its own address. Query strings are stripped; "
                              "localhost and iframe pages are left out. One session can visit several "
                              "pages, so the shares do not sum to 100%.")))
    parts.append(_table("Content by reach", data.get("assets"), "ASSET", "SESSIONS", "Sessions",
                        extra=[("EVENTS_PER_SESSION", lambda v: f"{float(v or 0):,.1f}")]))
    parts.append(_table("PDF read depth", data.get("depth"), "ASSET", "AVG_PAGE_REACHED", "Avg page",
                        extra=[("DEEPEST_PAGE", lambda v: f"{int(v or 0):,}")]))
    parts.append(_table("Accounts reached", data.get("companies"), "COMPANY", "SESSIONS", "Sessions",
                        extra=[("PEOPLE", lambda v: f"{int(v or 0):,}")],
                        note="Company domains only; individual addresses are never included."))
    parts.append(_table("How they arrived", data.get("sources"), "SOURCE_GROUP", "EVENTS", "Events"))
    parts.append(_table("External referrers", data.get("referrers"), "REFERRER", "EVENTS", "Events"))
    parts.append(_table("AI model mix", data.get("ai"), "MODEL", "REQUESTS", "Requests",
                        extra=[("SESSIONS", lambda v: f"{int(v or 0):,}")]))

    withheld = []
    if int(k["PAGES"]) == 0:
        withheld.append("no pages with a resolvable URL")
    if int(k["ASSETS"]) == 0:
        withheld.append("no tracked content")
    if int(k["PEOPLE"]) == 0:
        withheld.append("no identified visitors")
    elif int(k["PEOPLE"]) < IDENTITY_FLOOR:
        withheld.append(f"company breakdown withheld — only {int(k['PEOPLE'])} identified "
                        f"{'person' if int(k['PEOPLE']) == 1 else 'people'}, too few to break down")
    if int(k["AI_EVENTS"]) == 0:
        withheld.append("no AI activity")
    if withheld:
        parts.append(f"<p class='note'>Sections not shown: {_esc('; '.join(withheld))}.</p>")

    return _document(label, "".join(parts), window, link)


def _document(title, body, window, link) -> str:
    linked = f"<br>Open the live dashboard: <a href='{_esc(link)}'>{_esc(link)}</a>" if link else ""
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{_esc(title)} — campaign report</title><style>{_CSS}</style></head>"
        f"<body><div class='wrap'>{body}"
        f"<footer>Covers {_esc(window)}. Every figure is limited to that range.{linked}</footer>"
        "</div></body></html>"
    )
