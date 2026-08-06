import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

import streamlit as st

from src.charts import distribution_histogram, trend_line
from src.components import date_range_filter, kpi, kpi_row, previous_window
from src.db import run_query
from src.queries import session_durations_sql, session_kpis_sql, sessions_over_time_sql

st.set_page_config(page_title="Session Analytics", page_icon="🕒", layout="wide")
st.title("Session Analytics")

start_date, end_date = date_range_filter(default_days=30)
prev_start, prev_end = previous_window(start_date, end_date)
params = [start_date, end_date]

now = run_query(session_kpis_sql(), params + params).iloc[0]
was = run_query(session_kpis_sql(), [prev_start, prev_end, prev_start, prev_end]).iloc[0]

kpi_row(
    [
        kpi("Total Sessions", now["TOTAL_SESSIONS"], was["TOTAL_SESSIONS"]),
        kpi("Avg Events / Session", now["AVG_EVENTS_PER_SESSION"], was["AVG_EVENTS_PER_SESSION"], decimals=1),
        kpi("Avg Session Duration", now["AVG_DURATION_MINUTES"], was["AVG_DURATION_MINUTES"], decimals=1, suffix=" min"),
    ]
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

st.subheader("Session Duration Distribution")
durations = run_query(session_durations_sql(), params)
if durations.empty:
    st.info("No sessions in this date range.")
else:
    duration_chart = distribution_histogram(durations, "DURATION_MINUTES", "Duration (minutes)")
    st.altair_chart(duration_chart, use_container_width=True)
    with st.expander("View as table"):
        st.dataframe(durations.describe(), use_container_width=True)

st.caption(f"Showing {start_date} to {end_date}. Change is against {prev_start} to {prev_end}.")
