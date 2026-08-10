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
    "Removes Demand AI's own traffic (tenant demand_ai) - the marketing site, localhost "
    "and preview builds used for testing. Applies to every page at once."
)
# Rendered in the page body, not the sidebar, so a screenshot of any page carries the
# caveat with it. A filtered figure that looks unfiltered is the real risk here.
_EXCLUDE_ACTIVE = (
    ":material/filter_alt: **Internal traffic excluded.** Figures on this page omit Demand AI's "
    "own tenant. Turn this off in the sidebar to see all traffic."
)


def date_range_filter(default_days: int = 30, key: str = _WIDGET) -> tuple[dt.date, dt.date]:
    # Snowflake's runtime clock can lag the viewer's local date by up to a day, so
    # max_value is padded - otherwise the viewer can't select their own "today".
    today = dt.date.today()

    if _STORE not in st.session_state:
        st.session_state[_STORE] = (today - dt.timedelta(days=default_days), today)

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
    selected = st.sidebar.date_input("Date range", value=stored, min_value=_DATA_STARTS, max_value=latest, key=key)

    # Half-finished selections come back as a 1-tuple; hold the last complete range
    # so the dashboard doesn't snap to the default for a rerun.
    if isinstance(selected, (tuple, list)) and len(selected) == 2:
        st.session_state[_STORE] = (selected[0], selected[1])

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
