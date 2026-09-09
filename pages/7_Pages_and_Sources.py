import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

import streamlit as st

from src.charts import share_stacked_bar, top_events_bar
from src.components import date_range_filter, kpi, kpi_row, previous_window
from src.db import run_query
from src.queries import (
    page_coverage_sql,
    top_external_referrers_sql,
    top_pages_sql,
    traffic_sources_sql,
)

SOURCE_NOTE = (
    "Sources are bucketed before ranking. Most referrers are our own estate - the S3 "
    "microsites bucket, Atlassian, Amplify preview URLs, localhost - and listing those "
    "alongside real referrers would present internal traffic as acquisition. Only the "
    "External bucket is ranked below."
)

MIGRATION_NOTE = (
    "Pages are identified from SEARCH_URL, which holds the page URL and is populated "
    "across the whole history. PATH and TAB_URL were retired in a tracking change "
    "around March-April 2026 and are empty for any recent range."
)

st.set_page_config(page_title="Pages & Sources", page_icon="🧭", layout="wide")
st.title("Pages & Sources")

start_date, end_date = date_range_filter(default_days=30)
prev_start, prev_end = previous_window(start_date, end_date)
params = [start_date, end_date]

cover_now = run_query(page_coverage_sql(), params).iloc[0]
cover_was = run_query(page_coverage_sql(), [prev_start, prev_end]).iloc[0]
sources = run_query(traffic_sources_sql(), params)

total_events = float(sources["EVENT_COUNT"].sum()) if not sources.empty else 0.0
by_group = dict(zip(sources["SOURCE_GROUP"], sources["EVENT_COUNT"])) if not sources.empty else {}
direct_pct = float(by_group.get("Direct / none", 0)) / total_events * 100 if total_events else 0.0
external_pct = float(by_group.get("External", 0)) / total_events * 100 if total_events else 0.0

kpi_row(
    [
        kpi("Page Views", cover_now["PAGE_VIEWS"], cover_was["PAGE_VIEWS"]),
        kpi("Distinct Pages", cover_now["DISTINCT_PAGES"], cover_was["DISTINCT_PAGES"]),
        kpi("Direct Traffic", direct_pct, decimals=1, suffix="%"),
        kpi("External Referrals", external_pct, decimals=1, suffix="%"),
    ]
)

st.subheader("Top Pages")
st.caption(MIGRATION_NOTE)
pages = run_query(top_pages_sql(), params)
if pages.empty:
    st.info("No page views with a resolvable URL in this date range.")
else:
    st.altair_chart(top_events_bar(pages, "PAGE", "EVENT_COUNT", x_title="Page views"), width="stretch")
    with st.expander("View as table"):
        st.dataframe(pages, width="stretch", hide_index=True)

st.subheader("Traffic Sources")
if sources.empty:
    st.info("No events in this date range.")
else:
    st.altair_chart(share_stacked_bar(sources, "SOURCE_GROUP", "EVENT_COUNT"), width="stretch")
    with st.expander("View as table"):
        st.dataframe(sources, width="stretch", hide_index=True)

st.subheader("Top External Referrers")
st.caption(SOURCE_NOTE)
referrers = run_query(top_external_referrers_sql(), params)
if referrers.empty:
    st.info("No external referrals in this date range.")
else:
    st.altair_chart(top_events_bar(referrers, "REFERRER", "EVENT_COUNT", x_title="Events"), width="stretch")
    with st.expander("View as table"):
        st.dataframe(referrers, width="stretch", hide_index=True)

st.caption(f"Showing {start_date} to {end_date}. Change is against {prev_start} to {prev_end}.")
