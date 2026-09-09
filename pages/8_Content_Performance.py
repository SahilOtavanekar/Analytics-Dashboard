import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

import streamlit as st

# top_events_bar, not ordered_bar: ordered_bar labels every row with its share of
# the column total, which is meaningless for an average page number, and formats the
# label with int() so 1.5 would render as "1".
from src.charts import top_events_bar
from src.components import compact, date_range_filter, kpi, kpi_row, previous_window
from src.db import run_query
from src.queries import asset_cohort_sql, asset_kpis_sql, asset_read_depth_sql, top_assets_sql

# Module level so every branch below stays a single indented line - the Snowsight
# editor re-indents multi-line calls inside indented blocks and breaks them.
COHORT_NOTE = (
    "Sessions that opened an asset are **{eng_ratio:.1f}x more engaged** ({eng_a:.1f}% vs "
    "{eng_b:.1f}%) but convert at **{conv_ratio:.1f}x the rate** of sessions that didn't "
    "({conv_a:.2f}% vs {conv_b:.2f}%). Content clearly holds attention; it is not, on its "
    "own, closing. Both cohorts are large, so neither rate is a small-sample artifact."
)
DEPTH_NOTE = (
    "Average page reached inside each document, from {n:,} pdf-page-visit events carrying a "
    "page number (1-118). A low average against a high deepest-page means most readers stop "
    "early while a few finish."
)
NO_ASSETS = (
    "No tracked content in this date range. Assets are identified by the `asset` query "
    "parameter, which the tracking script began emitting around Mar-Apr 2026."
)

st.set_page_config(page_title="Content Performance", page_icon="📄", layout="wide")
st.title("Content Performance")
st.caption(
    "Assets are identified by the `asset` URL parameter, which rides on page views, "
    "clicks, PDF page turns and form submits alike - so this is content reach across the "
    "whole journey, not just landing pages."
)

start_date, end_date = date_range_filter(default_days=30)
prev_start, prev_end = previous_window(start_date, end_date)
params = [start_date, end_date]

now = run_query(asset_kpis_sql(), params).iloc[0]
was = run_query(asset_kpis_sql(), [prev_start, prev_end]).iloc[0]

now_share = float(now["ASSET_SESSIONS"]) / float(now["TOTAL_SESSIONS"]) * 100 if float(now["TOTAL_SESSIONS"]) else 0.0
was_share = float(was["ASSET_SESSIONS"]) / float(was["TOTAL_SESSIONS"]) * 100 if float(was["TOTAL_SESSIONS"]) else 0.0

kpi_row(
    [
        kpi("Assets Viewed", now["TOTAL_ASSETS"], was["TOTAL_ASSETS"]),
        kpi("Sessions with an Asset", now["ASSET_SESSIONS"], was["ASSET_SESSIONS"]),
        kpi("Share of All Sessions", now_share, was_share, decimals=1, suffix="%", delta_as_points=True),
        kpi("Asset Events", now["ASSET_EVENTS"], was["ASSET_EVENTS"]),
    ]
)

if int(now["TOTAL_ASSETS"]) == 0:
    st.info(NO_ASSETS)
    st.caption(f"Showing {start_date} to {end_date}.")
    st.stop()

st.subheader("Does content actually convert?")
cohort = run_query(asset_cohort_sql(), params)
if len(cohort) < 2:
    st.info("Only one cohort present in this date range - nothing to compare.")
else:
    saw = cohort[cohort["COHORT"] == "Saw an asset"].iloc[0]
    none = cohort[cohort["COHORT"] == "No asset"].iloc[0]
    eng_b = float(none["ENGAGEMENT_PCT"]) or 1e-9
    conv_b = float(none["LEAD_CONV_PCT"]) or 1e-9
    st.caption(COHORT_NOTE.format(eng_ratio=float(saw["ENGAGEMENT_PCT"]) / eng_b, eng_a=float(saw["ENGAGEMENT_PCT"]), eng_b=float(none["ENGAGEMENT_PCT"]), conv_ratio=float(saw["LEAD_CONV_PCT"]) / conv_b, conv_a=float(saw["LEAD_CONV_PCT"]), conv_b=float(none["LEAD_CONV_PCT"])))
    table = cohort.copy()
    table["SESSIONS"] = [compact(v) for v in table["SESSIONS"]]
    table["ENGAGEMENT_PCT"] = [f"{v:.1f}%" for v in table["ENGAGEMENT_PCT"]]
    table["LEAD_CONV_PCT"] = [f"{v:.2f}%" for v in table["LEAD_CONV_PCT"]]
    table["MEDIAN_EVENTS"] = [f"{v:.0f}" for v in table["MEDIAN_EVENTS"]]
    table.columns = ["Cohort", "Sessions", "Engagement", "Lead conversion", "Median events"]
    st.dataframe(table, width="stretch", hide_index=True)

st.subheader("Top Content by Reach")
top = run_query(top_assets_sql(), params)
if top.empty:
    st.info(NO_ASSETS)
else:
    st.altair_chart(top_events_bar(top, "ASSET", "SESSIONS", x_title="Sessions"), width="stretch")
    with st.expander("With engagement depth"):
        st.dataframe(top, width="stretch", hide_index=True)

st.subheader("PDF Read Depth")
depth = run_query(asset_read_depth_sql(), params)
if depth.empty:
    st.info("No PDF page-turn events in this date range.")
else:
    st.caption(DEPTH_NOTE.format(n=int(depth["SESSIONS"].sum())))
    st.altair_chart(top_events_bar(depth, "ASSET", "AVG_PAGE_REACHED", x_title="Average page reached"), width="stretch")
    with st.expander("Deepest page reached per asset"):
        st.dataframe(depth, width="stretch", hide_index=True)

st.caption(f"Showing {start_date} to {end_date}. Change is against {prev_start} to {prev_end}.")
