import calendar
import datetime as dt
from functools import partial

import streamlit as st

from src.db import CACHE_TTL_SECONDS, EXCLUDE_INTERNAL_KEY, clear_cache
from src.prefetch import warm

# Streamlit discards widget state on page navigation, so the selection is
# mirrored into a plain (non-widget) key that survives, then fed back in as the
# widget's value on the next page. Reading the widget's own key instead is what
# reset the range to the default on every page switch.
_STORE = "date_range_value"
_WIDGET = "date_range_widget"

# Results are cached for an hour so navigation doesn't re-query - see src/db.py for
# the measurements behind that. An hour of staleness on a 30-day window is
# immaterial, but it must be visible and overridable rather than silent.
_CACHE_NOTE = "Figures are cached for up to {mins} min. Refresh to re-query Snowflake."
_WARMED = "warmed_range"
_PRELOAD_HELP = "Runs every page's queries once (~30s) so each one opens instantly afterwards."


def _tick(bar, done, total, page):
    bar.progress(done / total, text=f"{page}  ({done}/{total})")


def compact(value) -> str:
    """Magnitude at a glance: 1.67M, 40.5K, 3,074.

    KPI cards are narrow, and a 7-digit comma-formatted number gets truncated
    mid-number ("1,757...") - worse than losing a decimal place. Values under
    10,000 are left exact because they already fit.
    """
    n = float(value)
    if abs(n) >= 1e9:
        return f"{n / 1e9:.2f}B"
    if abs(n) >= 1e6:
        return f"{n / 1e6:.2f}M"
    if abs(n) >= 1e4:
        return f"{n / 1e3:.1f}K"
    return f"{n:,.0f}"


def duration_label(minutes) -> str:
    """Readable duration, scaled to its own magnitude.

    Sessions here have a median of 0.0167 minutes, which reads as "0.02 min" under
    fixed formatting and as "1 s" under this one. Same number, one of them legible.
    """
    m = float(minutes)
    if m < 1:
        return f"{m * 60:.0f} s"
    if m < 60:
        return f"{m:.1f} min"
    return f"{m / 60:.1f} h"


def previous_window(start: dt.date, end: dt.date) -> tuple[dt.date, dt.date]:
    """The equal-length window ending the day before `start`, for comparison."""
    span = (end - start).days
    prev_end = start - dt.timedelta(days=1)
    return prev_end - dt.timedelta(days=span), prev_end


def kpi(label, current, previous=None, decimals=0, suffix="", delta_as_points=False, formatter=None):
    """Build one metric card: compact value, exact figure on hover, change vs previous.

    `delta_as_points` reports a percentage-point difference instead of a percentage
    change - the honest form when the metric is itself a percentage, where "+4%"
    is ambiguous between relative and absolute movement. `formatter` overrides the
    display for values that need their own scale, e.g. duration_label.
    """
    current = float(current)
    if formatter:
        display = exact = formatter(current)
    else:
        display = f"{current:,.{decimals}f}{suffix}" if decimals else f"{compact(current)}{suffix}"
        exact = f"{current:,.{decimals}f}{suffix}"

    delta = None
    detail = f"{exact} this period"
    if previous is not None:
        previous = float(previous)
        prev_text = formatter(previous) if formatter else f"{previous:,.{decimals}f}{suffix}"
        detail = f"{exact} this period vs {prev_text} previous"
        if delta_as_points:
            delta = f"{current - previous:+.1f} pp"
        elif previous != 0:
            delta = f"{(current - previous) / abs(previous) * 100:+.1f}%"
    return (label, display, delta, detail)


def kpi_row(items) -> None:
    """Render KPI cards. Accepts plain (label, value) or the 4-tuples built by kpi()."""
    cols = st.columns(len(items))
    for col, item in zip(cols, items):
        delta = item[2] if len(item) > 2 else None
        help_text = item[3] if len(item) > 3 else None
        col.metric(item[0], item[1], delta=delta, help=help_text)


# Tracking begins 2025-06-14; the table also holds 205 rows stamped before 2020
# (earliest 1978) and three in the future (latest 2058) that are plainly corrupt.
# Without a floor the picker offered every one of those, so a reader could land on
# a range with no data at all - which is how the Session Analytics NaN crash was
# reached. Deliberately a little earlier than the first event, so the true start of
# data is visible rather than clipped.
_DATA_STARTS = dt.date(2025, 6, 1)

# Same two-key pattern as the date range: the widget key is purged on navigation, so
# the durable key is what db.exclude_internal() reads and what survives a page
# change. Named to match EXCLUDE_INTERNAL_KEY exactly - they must not drift.
_EXCLUDE_STORE = EXCLUDE_INTERNAL_KEY
_EXCLUDE_WIDGET = "exclude_internal_widget"
_EXCLUDE_LABEL = "Exclude internal traffic"
_EXCLUDE_HELP = (
    "Removes Demand AI's own traffic on two counts: content under the demand_ai tenant "
    "(the marketing site, localhost and preview builds), and visits from demandai.co "
    "addresses - staff browsing customers' content. Applies to every page at once."
)
# Rendered in the page body, not the sidebar, so a screenshot of any page carries the
# caveat with it. A filtered figure that looks unfiltered is the real risk here.
_EXCLUDE_ACTIVE = (
    ":material/filter_alt: **Internal traffic excluded.** Figures on this page omit Demand AI's "
    "own tenant. Turn this off in the sidebar to see all traffic."
)

# Quick ranges. Whole calendar months, not day counts: six months before 31 August is
# 28 February, and a year is 365 or 366 days depending on which side of the leap day
# it falls.
_PRESETS = (
    ("Last 1 month", 1),
    ("Last 3 months", 3),
    ("Last 6 months", 6),
    ("Last 1 year", 12),
    ("Last 2 years", 24),
)
_MONTHS = dict(_PRESETS)
_PRESET_WIDGET = "date_preset_widget"
_PRESET_LABEL = "Quick range"

# Shown, and selected, only while it is true. A dropdown still reading "Last 3 months"
# after the reader hand-edits the picker to a fortnight is a control that misdescribes
# its own output, which is worse than no control - so the two are synced both ways and
# this appears the moment the range stops matching any preset.
_CUSTOM = "Custom"

# Data runs 2025-06-14 to today - 422 days as of writing - so "Last 2 years" is clipped
# to the floor and is the whole dataset, and "Last 1 year" very nearly is (22.04M of
# 22.13M rows). The picker above always shows what was actually selected, so this is
# visible rather than implied; the tooltip says why.
_PRESET_HELP = (
    "Sets the range above to whole calendar months ending today. Windows are clipped to "
    "the start of tracking, so the longest options can resolve to the same dates."
)

# Measured on this warehouse: one query costs ~1.3s over 30 days, 5.0s over six
# months and 8.2s over a year, and each page issues roughly a dozen. Wide ranges are
# usable but not instant, and that is worth knowing before the click rather than
# during the wait.
_WIDE_DAYS = 120
_WIDE_NOTE = "Wide range ({days} days). Measured at a year, the first load costs 15-90s per page and about a second after that, cached for {mins} min. Preload all pages does them in one pass."

# First real event in the table, measured: MIN(EVENT_TS)::DATE over the rows inside a
# sane date range is 2025-06-14. Distinct from _DATA_STARTS, which is a deliberately
# earlier floor for the picker so the true start of data is visible rather than clipped.
_TRACKING_STARTS = dt.date(2025, 6, 14)

# Every KPI card carries a "vs previous" delta against the equal-length window before
# this one, and a wide window pushes that comparison off the back of the data. At one
# year the previous window holds 58 of 365 days; at two years it holds none at all.
# Neither case is wrong arithmetic - the previous period really was that quiet - but a
# reader seeing "+400%" will read growth, not a shorter measuring stick. The presets
# make both cases one click away, so the caveat is stated where the numbers are rather
# than left for someone to work out. In the page body, like the internal-traffic
# banner, so a screenshot cannot separate the figures from the warning.
_PREV_PARTIAL = ":material/info: **Change vs previous overstates growth here.** The comparison period ({a} to {b}) reaches back before tracking began on {t}, so only {n} of its {d} days carry any data."
_PREV_NONE = ":material/info: **Ignore the change vs previous on this page.** The comparison period ({a} to {b}) ends before tracking began on {t}, so there is no earlier data to compare this window against."


def _months_before(day: dt.date, months: int) -> dt.date:
    """`day` shifted back whole calendar months, clipped to the shorter month."""
    total = day.year * 12 + day.month - 1 - months
    year, month = divmod(total, 12)
    return dt.date(year, month + 1, min(day.day, calendar.monthrange(year, month + 1)[1]))


def _preset_range(today: dt.date, months: int) -> tuple[dt.date, dt.date]:
    """The last `months` calendar months ending today, floored at the start of data.

    Start is one day after the shifted date so the window is exactly that many months
    long rather than a month and a day, and so consecutive windows do not overlap -
    previous_window() compares against the period immediately before this one.
    """
    return (max(_months_before(today, months) + dt.timedelta(days=1), _DATA_STARTS), today)


def _preset_label(today: dt.date, window) -> str:
    """Which preset, if any, the current range is - so the dropdown can describe it."""
    return next((name for name, m in _PRESETS if _preset_range(today, m) == tuple(window)), _CUSTOM)


def date_range_filter(default_days: int = 30, key: str = _WIDGET) -> tuple[dt.date, dt.date]:
    # Snowflake's runtime clock can lag the viewer's local date by up to a day, so
    # max_value is padded - otherwise the viewer can't select their own "today".
    today = dt.date.today()

    if _STORE not in st.session_state:
        # Snapped to a whole-month preset when `default_days` is within a couple of days
        # of one, so the dropdown has a name for the opening window instead of reading
        # Custom on first load. Every page asks for 30, meaning "about a month", and
        # that lands 0-2 days off depending on the month. A request that is nowhere near
        # a preset is honoured exactly - snapping a 7-day default up to a month would be
        # a silent widening, which is a different and worse bug.
        target = (today - dt.timedelta(days=default_days), today)
        gap, months = min((abs((_preset_range(today, m)[0] - target[0]).days), m) for m in _MONTHS.values())
        st.session_state[_STORE] = _preset_range(today, months) if gap <= 2 else target

    # The dropdown and the picker are two views of one range, so they are kept in step
    # both ways. This half runs first: a preset chosen on the previous run has to reach
    # the picker before the picker is created. Streamlit ignores `value=` once a widget
    # key exists in session state, so moving the picker means writing its own key - and
    # that is legal only ahead of the widget, and raises after it.
    chose = st.session_state.get(_PRESET_WIDGET)
    pending = None
    if chose in _MONTHS and chose != _preset_label(today, st.session_state[_STORE]):
        pending = _preset_range(today, _MONTHS[chose])
        st.session_state[_STORE] = pending

    # Clamp before handing the stored range back as `value`. st.date_input raises a
    # StreamlitAPIException - on every page, not just one - if `value` sits outside
    # [min_value, max_value], and the bounds can move under a live session: the
    # upper bound rolls at midnight, and _DATA_STARTS moves whenever this file is
    # redeployed. Adding the floor without this would trade one page's crash for
    # every page's.
    latest = today + dt.timedelta(days=1)
    held = st.session_state[_STORE]
    stored = (min(max(held[0], _DATA_STARTS), latest), min(max(held[1], _DATA_STARTS), latest))
    if stored != tuple(held):
        st.session_state[_STORE] = stored
    # Move the picker itself only when something other than the picker changed the
    # range - a preset chosen in the dropdown, or a clamp that pulled a stale range
    # back into bounds. Writing it on every run would snap a half-finished selection
    # back: between the two clicks of a range the picker holds a 1-tuple while _STORE
    # still holds the previous complete range, so an unconditional write would undo
    # the first click. Routed through the clamp above, so a preset can never hand the
    # picker a value outside its own bounds - two years starts well before the floor.
    if pending is not None or stored != tuple(held):
        st.session_state[key] = stored
    # `value` is only ever the first-render default: Streamlit ignores it once the
    # widget's own key exists in session state, and warns on every run if both are
    # supplied. After navigation the widget key is purged and `value` is what restores
    # the range, so it is passed exactly when it can still be used.
    default = {} if key in st.session_state else {"value": stored}
    selected = st.sidebar.date_input("Date range", min_value=_DATA_STARTS, max_value=latest, key=key, **default)

    # Half-finished selections come back as a 1-tuple; hold the last complete range
    # so the dashboard doesn't snap to the default for a rerun.
    if isinstance(selected, (tuple, list)) and len(selected) == 2:
        st.session_state[_STORE] = (selected[0], selected[1])

    # The other half of the sync, and the reason it sits below the picker: the label is
    # decided from the range the picker just produced, so a hand-edited window that
    # matches no preset shows as Custom instead of leaving the dropdown asserting a
    # window it no longer describes. Custom is offered only while it is the truth -
    # selecting it would mean nothing, and every other option means something.
    span = st.session_state[_STORE]
    st.session_state[_PRESET_WIDGET] = _preset_label(today, span)
    names = [n for n, _ in _PRESETS] + ([_CUSTOM] if st.session_state[_PRESET_WIDGET] == _CUSTOM else [])
    st.sidebar.selectbox(_PRESET_LABEL, names, key=_PRESET_WIDGET, help=_PRESET_HELP)

    if (span[1] - span[0]).days + 1 > _WIDE_DAYS:
        st.sidebar.caption(_WIDE_NOTE.format(days=(span[1] - span[0]).days + 1, mins=CACHE_TTL_SECONDS // 60))

    if _EXCLUDE_STORE not in st.session_state:
        st.session_state[_EXCLUDE_STORE] = False
    excluding = st.sidebar.checkbox(_EXCLUDE_LABEL, value=st.session_state[_EXCLUDE_STORE], key=_EXCLUDE_WIDGET, help=_EXCLUDE_HELP)
    st.session_state[_EXCLUDE_STORE] = excluding
    if excluding:
        st.caption(_EXCLUDE_ACTIVE)

    # Data controls live here rather than on a landing page, because since the
    # Executive Dashboard became the landing page there is no neutral page to put
    # them on - and they are wanted from wherever the reader happens to be.
    chosen = st.session_state[_STORE]
    prev_start, prev_end = previous_window(chosen[0], chosen[1])
    if prev_end < _TRACKING_STARTS:
        st.caption(_PREV_NONE.format(a=prev_start, b=prev_end, t=_TRACKING_STARTS))
    elif prev_start < _TRACKING_STARTS:
        covered = (prev_end - _TRACKING_STARTS).days + 1
        st.caption(_PREV_PARTIAL.format(a=prev_start, b=prev_end, t=_TRACKING_STARTS, n=covered, d=(prev_end - prev_start).days + 1))
    # The warmed marker carries the exclusion flag as well as the range. Toggling the
    # filter rewrites every query's SQL, so the cache for the other state is cold -
    # keying on the range alone would keep claiming "preloaded" while every page went
    # back to querying Snowflake.
    warm_key = (chosen, excluding)
    with st.sidebar.expander("Data", expanded=False):
        st.caption(_CACHE_NOTE.format(mins=CACHE_TTL_SECONDS // 60))
        if st.button("Refresh data", width="stretch"):
            clear_cache()
            st.session_state.pop(_WARMED, None)
            st.rerun()
        if st.session_state.get(_WARMED) == warm_key:
            st.caption("All pages preloaded for this range.")
        elif st.button("Preload all pages", width="stretch", help=_PRELOAD_HELP):
            bar = st.progress(0.0, text="Starting...")
            ok, total = warm(chosen[0], chosen[1], prev_start, prev_end, on_progress=partial(_tick, bar))
            bar.empty()
            st.session_state[_WARMED] = warm_key
            st.rerun()

    return chosen
