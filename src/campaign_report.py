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

from src.queries import (
    campaign_ai_sql,
    campaign_assets_sql,
    campaign_companies_sql,
    campaign_daily_sql,
    campaign_detail_kpis_sql,
    campaign_duration_bands_sql,
    campaign_funnel_sql,
    campaign_geo_sql,
    campaign_lookup_sql,
    campaign_read_depth_sql,
    campaign_sources_sql,
)

# An identified-people floor, not a courtesy. Across the top 25 campaigns two have exactly
# one identified person, and "1 person at acme.com" is an individual described by a report
# that promises domain-level aggregation only. Below the floor the breakdown is not queried.
IDENTITY_FLOOR = 5

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
    # No "actions" key any more: the action-reach breakdown was removed from the page, the PDF
    # and the emailed body, so fetching it would be a round trip nothing reads. That takes this
    # from eleven queries per drill-down to ten. campaign_actions_sql() is kept in queries.py -
    # it is correct and the suites still exercise it - so restoring the section is one line here.

    geo = run(campaign_geo_sql(), params)
    out["regions"] = geo[geo["KIND"] == "region"][["LABEL", "SESSIONS"]].rename(columns={"LABEL": "REGION"})
    out["timezones"] = geo[geo["KIND"] == "timezone"][["LABEL", "SESSIONS"]].rename(columns={"LABEL": "TIMEZONE"})

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
    w_label, w_value, w_extra = 74.0, 24.0, 20.0
    w_bar = _PAGE_W - w_label - w_value - (w_extra * len(extra))

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
        for col, _ in extra:
            pdf.cell(w_extra, 5.4, _safe(col.replace("_", " ").title()) + "  ", align="R", fill=True)
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
        # and fpdf2 does not wrap inside a fixed cell - it overprints the next column.
        while pdf.get_string_width(label) > w_label - 5 and len(label) > 4:
            label = label[:-2] + "..."
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
        for col, fmt in extra:
            pdf.cell(w_extra, 5.4, _safe(fmt(getattr(row, col))) + "  ", align="R")
        pdf.ln()


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
        pdf.set_fill_color(*_PDF_BAND)
        pdf.set_draw_color(*_PDF_RULE)
        pdf.rect(x, y, w, h, style="DF")
        pdf.set_xy(x + 2.6, y + 1.8)
        pdf.set_font("Helvetica", "", 6.5)
        pdf.set_text_color(*_PDF_MUTED)
        pdf.cell(w - 5, 3.4, _safe(label.upper()))
        pdf.set_xy(x + 2.6, y + 5.6)
        pdf.set_font("Helvetica", "B", 12.5)
        pdf.set_text_color(*_PDF_INK)
        pdf.cell(w - 5, 6.4, _safe(value))
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
    _pdf_cards(pdf, [
        ("Sessions", f"{s:,}"),
        ("Events", f"{int(k['EVENTS']):,}"),
        ("Events / session", f"{float(k['EVENTS']) / s:,.1f}"),
        ("Median duration", duration_label(k["MEDIAN_DURATION_MINUTES"])),
        ("Engaged", f"{pct(k['ACTED_SESSIONS'], s):.1f}%"),
        ("Converted", f"{pct(k['LEAD_SESSIONS'], s):.2f}%"),
    ])

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
    _pdf_table(pdf, "Engagement funnel", data.get("funnel"), "STAGE", "SESSIONS", "Sessions",
               note=("Each stage is a subset of the one above it." + _consent) if _consent else None)
    _pdf_table(pdf, "Where they are", data.get("regions"), "REGION", "SESSIONS", "Sessions",
               note="From the browser timezone, the only location signal in the data.")
    _pdf_table(pdf, "Content by reach", data.get("assets"), "ASSET", "SESSIONS", "Sessions",
               extra=[("EVENTS_PER_SESSION", lambda v: f"{float(v or 0):,.1f}")])
    _pdf_table(pdf, "PDF read depth", data.get("depth"), "ASSET", "AVG_PAGE_REACHED", "Avg page",
               extra=[("DEEPEST_PAGE", lambda v: f"{int(v or 0):,}")])
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
.note{color:#546a6a;font-size:13px;margin:8px 0 0}
.warn{background:#fdf6e3;border:1px solid #e8d9a8;border-radius:6px;padding:14px 16px;margin:18px 0}
footer{margin-top:34px;padding-top:12px;border-top:1px solid #e6ecec;color:#546a6a;font-size:12px}
"""


def _esc(v) -> str:
    return html.escape("" if v is None else str(v), quote=True)


def _bar_rows(frame, label_col, value_col, extra=None) -> str:
    if frame is None or not len(frame):
        return ""
    top = float(max(float(v or 0) for v in frame[value_col])) or 1.0
    out = []
    for row in frame.itertuples():
        label = _esc(getattr(row, label_col))
        value = float(getattr(row, value_col) or 0)
        width = max(value / top * 100.0, 0.0)
        cells = f"<td>{label}</td><td class='n'>{value:,.10g}</td>"
        cells += f"<td class='barcell'><div class='bar' style='width:{width:.1f}%'></div></td>"
        if extra:
            for col, fmt in extra:
                cells += f"<td class='n'>{fmt(getattr(row, col))}</td>"
        out.append(f"<tr>{cells}</tr>")
    return "".join(out)


def _table(title, frame, label_col, value_col, value_head, extra=None, note=None) -> str:
    if frame is None or not len(frame):
        return ""
    head = f"<th>{_esc(label_col.replace('_', ' ').title())}</th><th class='n'>{_esc(value_head)}</th><th></th>"
    if extra:
        for col, _ in extra:
            head += f"<th class='n'>{_esc(col.replace('_', ' ').title())}</th>"
    body = _bar_rows(frame, label_col, value_col, extra)
    note_html = f"<p class='note'>{_esc(note)}</p>" if note else ""
    return f"<h2>{_esc(title)}</h2>{note_html}<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


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

    parts.append("<div class='kpis'>"
                 + _kpi("Sessions", f"{s:,}")
                 + _kpi("Events", f"{int(k['EVENTS']):,}")
                 + _kpi("Events / session", f"{float(k['EVENTS']) / s:,.1f}")
                 + _kpi("Median duration", duration_label(k["MEDIAN_DURATION_MINUTES"]))
                 + _kpi("Engaged", f"{pct(k['ACTED_SESSIONS'], s):.1f}%")
                 + _kpi("Converted", f"{pct(k['LEAD_SESSIONS'], s):.2f}%")
                 + "</div>")

    parts.append(_table("Activity by day", data.get("daily"), "EVENT_DATE", "SESSION_COUNT", "Sessions"))
    parts.append(_table(
        "Session duration", data.get("duration"), "BAND", "SESSIONS", "Sessions",
        note=(f"Span between a session's first and last event, not time spent reading. "
              f"{int(k['INSTANT_SESSIONS']):,} of {s:,} sessions hold a single event.")))
    # Action reach dropped here too, so the page, the PDF and the emailed body describe the same
    # sections. Leaving it in the email alone would recreate exactly the drift this module exists
    # to prevent - and collect() no longer fetches it, so there would be nothing to render.
    parts.append(_table("Engagement funnel", data.get("funnel"), "STAGE", "SESSIONS", "Sessions",
                        note="Each stage is a subset of the one above it."))
    parts.append(_table("Where they are", data.get("regions"), "REGION", "SESSIONS", "Sessions",
                        note="From the browser timezone, the only location signal in the data."))
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
