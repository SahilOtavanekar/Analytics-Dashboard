import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

import streamlit as st

from src.components import date_range_filter, kpi_row
from src.db import run_query
from src.queries import executive_kpis_sql

st.set_page_config(page_title="Executive Dashboard", page_icon="📊", layout="wide")
st.title("Executive Dashboard")

start_date, end_date = date_range_filter(default_days=30)

kpis = run_query(
    executive_kpis_sql(),
    {"start_date": start_date, "end_date": end_date},
).iloc[0]

kpi_row(
    [
        ("Total Events", f"{kpis['TOTAL_EVENTS']:,}"),
        ("Total Sessions", f"{kpis['TOTAL_SESSIONS']:,}"),
        ("Total Campaigns", f"{kpis['TOTAL_CAMPAIGNS']:,}"),
    ]
)

st.caption(f"Showing data from {start_date} to {end_date}.")
