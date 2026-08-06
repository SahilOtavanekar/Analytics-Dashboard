import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

import streamlit as st

from src.components import date_range_filter, kpi, kpi_row, previous_window
from src.db import run_query
from src.queries import executive_kpis_sql

st.set_page_config(page_title="Executive Dashboard", page_icon="📊", layout="wide")
st.title("Executive Dashboard")

start_date, end_date = date_range_filter(default_days=30)
prev_start, prev_end = previous_window(start_date, end_date)

now = run_query(executive_kpis_sql(), [start_date, end_date]).iloc[0]
was = run_query(executive_kpis_sql(), [prev_start, prev_end]).iloc[0]

kpi_row(
    [
        kpi("Total Events", now["TOTAL_EVENTS"], was["TOTAL_EVENTS"]),
        kpi("Total Sessions", now["TOTAL_SESSIONS"], was["TOTAL_SESSIONS"]),
        kpi("Total Campaigns", now["TOTAL_CAMPAIGNS"], was["TOTAL_CAMPAIGNS"]),
    ]
)

st.caption(f"Showing {start_date} to {end_date}. Change is against {prev_start} to {prev_end}.")
