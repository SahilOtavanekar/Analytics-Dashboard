import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

import streamlit as st

from src.charts import ordered_bar
from src.components import date_range_filter, kpi, kpi_row, previous_window
from src.db import run_query
from src.queries import (
    top_visitor_by_sessions_sql,
    user_activity_kpis_sql,
    visitor_sessions_bands_sql,
    visitor_sessions_percentiles_sql,
)

# Kept at module level so the branch that uses it stays a single indented line -
# multi-line calls inside an indented block get mangled by the Snowsight editor.
BOT_WARNING = (
    "One IP address ({ip}) accounts for {sessions:,} sessions in this range - {ratio:.0f}x the "
    "95th percentile of {p95:,.0f}. This is almost certainly automated traffic (bot, crawler, "
    "or a shared corporate network) rather than a single person. It is counted in the "
    "'over 100 sessions' band below, not hidden."
)

st.set_page_config(page_title="User Activity", page_icon="👥", layout="wide")
st.title("User Activity")
st.caption(
    "This data has no persistent visitor/user ID. Activity below is a proxy based on "
    "IP address and session, not verified individual identity."
)

start_date, end_date = date_range_filter(default_days=30)
prev_start, prev_end = previous_window(start_date, end_date)
params = [start_date, end_date]

now = run_query(user_activity_kpis_sql(), params).iloc[0]
was = run_query(user_activity_kpis_sql(), [prev_start, prev_end]).iloc[0]

kpi_row(
    [
        kpi("Unique Visitors (proxy)", now["TOTAL_VISITORS"], was["TOTAL_VISITORS"]),
        kpi("Returning (active >1 day)", now["PCT_RETURNING"], was["PCT_RETURNING"], decimals=1, suffix="%", delta_as_points=True),
        kpi("Median Sessions / Visitor", now["MEDIAN_SESSIONS_PER_VISITOR"], was["MEDIAN_SESSIONS_PER_VISITOR"], decimals=1),
        kpi("Single-session Visitors", now["PCT_SINGLE_SESSION"], was["PCT_SINGLE_SESSION"], decimals=1, suffix="%", delta_as_points=True),
    ]
)
st.caption(
    f"Median, not mean - one IP distorts the average badly. The mean is "
    f"{now['MEAN_SESSIONS_PER_VISITOR']:.1f} sessions per visitor against a median of "
    f"{now['MEDIAN_SESSIONS_PER_VISITOR']:.0f}."
)

st.subheader("Sessions per Visitor")
bands = run_query(visitor_sessions_bands_sql(), params)
if bands.empty:
    st.info("No visitor activity in this date range.")
else:
    st.altair_chart(ordered_bar(bands, "BAND", "VISITORS", x_title="Visitors"), use_container_width=True)
    with st.expander("Percentiles and edge cases"):
        st.dataframe(run_query(visitor_sessions_percentiles_sql(), params).T, use_container_width=True)

stats = run_query(visitor_sessions_percentiles_sql(), params).iloc[0]
top = run_query(top_visitor_by_sessions_sql(), params)
p95 = float(stats["P95_SESSIONS"])
if not top.empty and p95 > 0 and float(top["SESSION_COUNT"].iloc[0]) > p95 * 5:
    st.warning(BOT_WARNING.format(ip=top["REQUEST_IP"].iloc[0], sessions=int(top["SESSION_COUNT"].iloc[0]), ratio=float(top["SESSION_COUNT"].iloc[0]) / p95, p95=p95))

st.caption(f"Showing {start_date} to {end_date}. Change is against {prev_start} to {prev_end}.")
