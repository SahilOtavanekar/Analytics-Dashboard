import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

import streamlit as st

from src.charts import trend_line
from src.components import date_range_filter, kpi, kpi_row, previous_window
from src.db import run_query
from src.queries import executive_kpis_sql, sessions_over_time_sql

# Module level so every branch below stays a single indented line - the Snowsight
# editor re-indents multi-line calls inside indented blocks and breaks them.
CHAIN_NOTE = (
    "InSyte's value chain, in order: content reached, AI engaged inside that content, "
    "account identified, lead captured. Attach rate is AI-on-content specifically - "
    "sessions that used AI without opening an asset are excluded, because "
    "\"content into conversations\" is a claim about what happens inside a document."
)
EMPTY = "No sessions in this date range."

st.set_page_config(page_title="Executive Dashboard", page_icon="📊", layout="wide")
st.title("Executive Dashboard")
st.caption(CHAIN_NOTE)

start_date, end_date = date_range_filter(default_days=30)
prev_start, prev_end = previous_window(start_date, end_date)
params = [start_date, end_date]

now = run_query(executive_kpis_sql(), params).iloc[0]
was = run_query(executive_kpis_sql(), [prev_start, prev_end]).iloc[0]

kpi_row(
    [
        kpi("Sessions", now["TOTAL_SESSIONS"], was["TOTAL_SESSIONS"]),
        kpi("Content Sessions", now["CONTENT_SESSIONS"], was["CONTENT_SESSIONS"]),
        kpi("AI Attach Rate", now["AI_ATTACH_PCT"], was["AI_ATTACH_PCT"], decimals=1, suffix="%", delta_as_points=True),
        kpi("Companies Identified", now["COMPANIES"], was["COMPANIES"]),
        kpi("Lead Conversion", now["LEAD_CONV_PCT"], was["LEAD_CONV_PCT"], decimals=2, suffix="%", delta_as_points=True),
    ]
)

st.subheader("Sessions Over Time")
over_time = run_query(sessions_over_time_sql(), params)
if over_time.empty:
    st.info(EMPTY)
else:
    st.altair_chart(trend_line(over_time, "EVENT_DATE", "SESSION_COUNT", "Sessions"), width="stretch")

st.caption(f"Showing {start_date} to {end_date}. Change is against {prev_start} to {prev_end}.")
