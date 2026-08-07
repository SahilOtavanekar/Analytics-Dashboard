import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

import streamlit as st

from src.charts import share_stacked_bar, top_events_bar
from src.components import date_range_filter, kpi, kpi_row, previous_window
from src.db import run_query
from src.queries import event_mix_sql, event_split_kpis_sql, top_events_sql, tracked_actions_sql

st.set_page_config(page_title="Event Analytics", page_icon="📈", layout="wide")
st.title("Event Analytics")
st.caption(
    "Page views outnumber tracked actions roughly 20:1, so ranking them together "
    "hides every intent signal. They are separated below."
)

start_date, end_date = date_range_filter(default_days=30)
prev_start, prev_end = previous_window(start_date, end_date)
params = [start_date, end_date]

now = run_query(event_split_kpis_sql(), params).iloc[0]
was = run_query(event_split_kpis_sql(), [prev_start, prev_end]).iloc[0]

# Actions per 1,000 page views normalises intent against traffic, so a drop in
# visitors doesn't read as a drop in engagement.
now_rate = float(now["TRACKED_ACTIONS"]) / float(now["PAGE_VIEWS"]) * 1000 if float(now["PAGE_VIEWS"]) else 0.0
was_rate = float(was["TRACKED_ACTIONS"]) / float(was["PAGE_VIEWS"]) * 1000 if float(was["PAGE_VIEWS"]) else 0.0

kpi_row(
    [
        kpi("Page Views", now["PAGE_VIEWS"], was["PAGE_VIEWS"]),
        kpi("Tracked Actions", now["TRACKED_ACTIONS"], was["TRACKED_ACTIONS"]),
        kpi("Actions per 1,000 Views", now_rate, was_rate, decimals=1),
    ]
)

st.subheader("Tracked Actions")
actions = run_query(tracked_actions_sql(), params)
if actions.empty:
    st.info("No tracked actions in this date range.")
else:
    st.altair_chart(top_events_bar(actions, "EVENT_NAME", "EVENT_COUNT"), width="stretch")
    with st.expander("View as table"):
        st.dataframe(actions, width="stretch", hide_index=True)

st.subheader("Event Mix")
mix = run_query(event_mix_sql(), params)
if mix.empty:
    st.info("No events in this date range.")
else:
    st.altair_chart(share_stacked_bar(mix, "EVENT_GROUP", "EVENT_COUNT"), width="stretch")
    with st.expander("View as table"):
        st.dataframe(mix, width="stretch", hide_index=True)

with st.expander("All events, page views included"):
    st.dataframe(run_query(top_events_sql(limit=25), params), width="stretch", hide_index=True)

st.caption(f"Showing {start_date} to {end_date}. Change is against {prev_start} to {prev_end}.")
