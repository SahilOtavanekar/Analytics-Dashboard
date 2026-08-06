import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

import streamlit as st

from src.charts import funnel_bar, top_events_bar
from src.components import date_range_filter, kpi, kpi_row, previous_window
from src.db import run_query
from src.queries import action_reach_sql, funnel_sql

# Explained on the page because the obvious four-stage funnel is wrong, and anyone
# reading these numbers deserves to know why it isn't shown.
NOT_A_FUNNEL = (
    "These actions overlap rather than follow one another, so they are shown as reach, "
    "not as funnel stages. In a recent 30-day window 9,001 sessions clicked while only "
    "2,269 opened a PDF, 7,184 clicking sessions never opened one, and 413 form "
    "submissions involved no click at all. Sequencing them would invent a journey."
)

st.set_page_config(page_title="Conversion", page_icon="🔻", layout="wide")
st.title("Conversion")

start_date, end_date = date_range_filter(default_days=30)
prev_start, prev_end = previous_window(start_date, end_date)
params = [start_date, end_date]

now = run_query(funnel_sql(), params)
was = run_query(funnel_sql(), [prev_start, prev_end])

if now.empty or float(now["SESSIONS"].iloc[0]) == 0:
    st.info("No sessions in this date range.")
    st.caption(f"Showing {start_date} to {end_date}.")
    st.stop()

now_sessions, now_acted, now_converted = (float(v) for v in now["SESSIONS"])
was_sessions, was_acted, was_converted = (float(v) for v in was["SESSIONS"])

now_engagement = now_acted / now_sessions * 100 if now_sessions else 0.0
was_engagement = was_acted / was_sessions * 100 if was_sessions else 0.0
now_conversion = now_converted / now_sessions * 100 if now_sessions else 0.0
was_conversion = was_converted / was_sessions * 100 if was_sessions else 0.0

kpi_row(
    [
        kpi("Sessions", now_sessions, was_sessions),
        kpi("Engagement Rate", now_engagement, was_engagement, decimals=1, suffix="%", delta_as_points=True),
        kpi("Conversion Rate", now_conversion, was_conversion, decimals=1, suffix="%", delta_as_points=True),
    ]
)
st.caption("Engagement = session took any tracked action. Conversion = session submitted a form.")

st.subheader("Session Funnel")
st.altair_chart(funnel_bar(now, "STAGE", "SESSIONS"), use_container_width=True)
with st.expander("View as table"):
    st.dataframe(now[["STAGE", "SESSIONS"]], use_container_width=True, hide_index=True)

st.subheader("Action Reach")
st.caption(NOT_A_FUNNEL)
reach = run_query(action_reach_sql(), params + params)
if reach.empty:
    st.info("No tracked actions in this date range.")
else:
    st.altair_chart(top_events_bar(reach, "ACTION", "SESSIONS", x_title="Sessions"), use_container_width=True)
    with st.expander("View as table"):
        st.dataframe(reach, use_container_width=True, hide_index=True)

st.caption(f"Showing {start_date} to {end_date}. Change is against {prev_start} to {prev_end}.")
