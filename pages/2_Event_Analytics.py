import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

import streamlit as st

from src.charts import share_stacked_bar, top_events_bar
from src.components import date_range_filter
from src.db import run_query
from src.queries import event_type_share_sql, top_events_sql

st.set_page_config(page_title="Event Analytics", page_icon="📈", layout="wide")
st.title("Event Analytics")

start_date, end_date = date_range_filter(default_days=30)
params = {"start_date": start_date, "end_date": end_date}

st.subheader("Top Events")
top_events = run_query(top_events_sql(), params)
if top_events.empty:
    st.info("No events in this date range.")
else:
    st.plotly_chart(top_events_bar(top_events, "EVENT_NAME", "EVENT_COUNT"), use_container_width=True)
    with st.expander("View as table"):
        st.dataframe(top_events, use_container_width=True, hide_index=True)

st.subheader("Event Type Share")
event_share = run_query(event_type_share_sql(), params)
if event_share.empty:
    st.info("No events in this date range.")
else:
    st.plotly_chart(share_stacked_bar(event_share, "EVENT_TYPE", "EVENT_COUNT"), use_container_width=True)
    with st.expander("View as table"):
        st.dataframe(event_share, use_container_width=True, hide_index=True)

st.caption(f"Showing data from {start_date} to {end_date}.")
