import datetime as dt

import streamlit as st

from src.db import CACHE_TTL_SECONDS, clear_cache

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


def date_range_filter(default_days: int = 30, key: str = _WIDGET) -> tuple[dt.date, dt.date]:
    # Snowflake's runtime clock can lag the viewer's local date by up to a day, so
    # max_value is padded - otherwise the viewer can't select their own "today".
    today = dt.date.today()

    if _STORE not in st.session_state:
        st.session_state[_STORE] = (today - dt.timedelta(days=default_days), today)

    stored = st.session_state[_STORE]
    selected = st.sidebar.date_input("Date range", value=stored, max_value=today + dt.timedelta(days=1), key=key)

    # Half-finished selections come back as a 1-tuple; hold the last complete range
    # so the dashboard doesn't snap to the default for a rerun.
    if isinstance(selected, (tuple, list)) and len(selected) == 2:
        st.session_state[_STORE] = (selected[0], selected[1])

    # Rendered on every page, because the cache is what makes navigation fast and
    # the reader needs a way out of it without knowing where the setting lives.
    if st.sidebar.button("Refresh data", use_container_width=True):
        clear_cache()
        st.session_state.pop("warmed_range", None)
        st.rerun()
    st.sidebar.caption(_CACHE_NOTE.format(mins=CACHE_TTL_SECONDS // 60))

    return st.session_state[_STORE]
