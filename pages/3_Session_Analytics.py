import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

import streamlit as st

from src.charts import ordered_bar, trend_line
from src.components import date_range_filter, duration_label, kpi, kpi_row, previous_window
from src.db import run_query
from src.queries import (
    session_duration_bands_sql,
    session_duration_percentiles_sql,
    session_integrity_sql,
    session_kpis_sql,
    sessions_over_time_sql,
)

# Module level so the branch using it stays a single indented line.
REUSE_WARNING = (
    "{multi:,} sessions in this range were seen from more than one IP address, and one "
    "carried {worst:,}. A session ID is therefore not reliably one visit - it is reused "
    "across unrelated visitors - so per-session figures blend those together. {long:,} "
    "sessions also span more than 24 hours, which no real visit does."
)

DURATION_NOTE = (
    "Duration is the gap between a session's first and last event. Half of all sessions "
    "register zero because they contain a single event, and 44% of consecutive events "
    "fire in the same second, so this measures the span of activity rather than time "
    "spent reading."
)

st.set_page_config(page_title="Session Analytics", page_icon="🕒", layout="wide")
st.title("Session Analytics")

start_date, end_date = date_range_filter(default_days=30)
prev_start, prev_end = previous_window(start_date, end_date)
params = [start_date, end_date]

# session_kpis_sql has two placeholders, not four. This passed the range twice for
# a shape the query no longer has; Snowflake ignored the extras, so the numbers were
# right, but the next placeholder added to that query would have bound wrongly.
now = run_query(session_kpis_sql(), params).iloc[0]
was = run_query(session_kpis_sql(), [prev_start, prev_end]).iloc[0]

kpi_row(
    [
        kpi("Total Sessions", now["TOTAL_SESSIONS"], was["TOTAL_SESSIONS"]),
        kpi("Median Events / Session", now["MEDIAN_EVENTS"], was["MEDIAN_EVENTS"], decimals=1),
        kpi("Median Duration", now["MEDIAN_DURATION_MINUTES"], was["MEDIAN_DURATION_MINUTES"], formatter=duration_label),
        kpi("Instant Sessions", now["PCT_INSTANT"], was["PCT_INSTANT"], decimals=1, suffix="%", delta_as_points=True),
    ]
)
st.caption(
    f"Medians, not means - this distribution is too skewed for an average to describe it. "
    f"Mean duration is {duration_label(now['MEAN_DURATION_MINUTES'])} against a median of "
    f"{duration_label(now['MEDIAN_DURATION_MINUTES'])}. Instant sessions contain a single event."
)

st.subheader("Sessions Over Time")
over_time = run_query(sessions_over_time_sql(), params)
if over_time.empty:
    st.info("No sessions in this date range.")
else:
    trend = trend_line(over_time, "EVENT_DATE", "SESSION_COUNT", "Sessions")
    st.altair_chart(trend, use_container_width=True)
    with st.expander("View as table"):
        st.dataframe(over_time, use_container_width=True, hide_index=True)

st.subheader("Session Duration")
st.caption(DURATION_NOTE)
bands = run_query(session_duration_bands_sql(), params)
if bands.empty:
    st.info("No sessions in this date range.")
else:
    st.altair_chart(ordered_bar(bands, "BAND", "SESSIONS"), use_container_width=True)
    with st.expander("Percentiles and edge cases"):
        st.dataframe(run_query(session_duration_percentiles_sql(), params).T, use_container_width=True)

integrity = run_query(session_integrity_sql(), params).iloc[0]
if int(integrity["MULTI_IP_SESSIONS"]) > 0:
    st.warning(REUSE_WARNING.format(multi=int(integrity["MULTI_IP_SESSIONS"]), worst=int(integrity["MAX_IPS_ON_ONE_SESSION"]), long=int(integrity["OVER_24_HOURS"])))

st.caption(f"Showing {start_date} to {end_date}. Change is against {prev_start} to {prev_end}.")
