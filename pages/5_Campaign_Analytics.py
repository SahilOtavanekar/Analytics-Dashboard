import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

import streamlit as st

from src.charts import share_stacked_bar, top_events_bar
from src.components import date_range_filter, kpi, kpi_row, previous_window
from src.db import run_query
from src.queries import campaign_kpis_sql, channel_share_sql, top_campaigns_sql

st.set_page_config(page_title="Campaign Analytics", page_icon="🎯", layout="wide")
st.title("Campaign Analytics")

start_date, end_date = date_range_filter(default_days=30)
prev_start, prev_end = previous_window(start_date, end_date)
params = [start_date, end_date]

now = run_query(campaign_kpis_sql(), params).iloc[0]
was = run_query(campaign_kpis_sql(), [prev_start, prev_end]).iloc[0]

kpi_row(
    [
        kpi("Total Campaigns", now["TOTAL_CAMPAIGNS"], was["TOTAL_CAMPAIGNS"]),
        kpi("Total Sessions", now["TOTAL_SESSIONS"], was["TOTAL_SESSIONS"]),
        kpi("Avg Events / Campaign", now["AVG_EVENTS_PER_CAMPAIGN"], was["AVG_EVENTS_PER_CAMPAIGN"]),
    ]
)

st.subheader("Top Campaigns")
top_campaigns = run_query(top_campaigns_sql(), params)
if top_campaigns.empty:
    st.info("No campaign activity in this date range.")
else:
    st.altair_chart(top_events_bar(top_campaigns, "CAMPAIGN_LABEL", "EVENT_COUNT"), use_container_width=True)
    with st.expander("View as table"):
        st.dataframe(top_campaigns, use_container_width=True, hide_index=True)

st.subheader("Traffic by Device")
channel_share = run_query(channel_share_sql(), params)
if channel_share.empty:
    st.info("No device activity in this date range.")
else:
    st.altair_chart(share_stacked_bar(channel_share, "CHANNEL", "EVENT_COUNT"), use_container_width=True)
    with st.expander("View as table"):
        st.dataframe(channel_share, use_container_width=True, hide_index=True)

st.caption(f"Showing {start_date} to {end_date}. Change is against {prev_start} to {prev_end}.")
