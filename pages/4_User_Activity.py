import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

import streamlit as st

from src.charts import distribution_histogram
from src.components import date_range_filter, kpi_row
from src.db import run_query
from src.queries import (
    sessions_per_visitor_sql,
    top_visitor_by_sessions_sql,
    user_activity_kpis_sql,
)

# Kept at module level so the branch that uses it stays a single indented line -
# multi-line calls inside an indented block get mangled by the Snowsight editor.
BOT_WARNING = (
    "One IP address ({ip}) accounts for {sessions:,} sessions in this range - far above a "
    "typical visitor (95th percentile: {p95:.0f}). This is almost certainly automated "
    "traffic (bot, crawler, or a shared corporate network) rather than a single person. "
    "It's included in the chart below, not filtered out."
)

st.set_page_config(page_title="User Activity", page_icon="👥", layout="wide")
st.title("User Activity")
st.caption(
    "This data has no persistent visitor/user ID. Activity below is a proxy based on "
    "IP address and session, not verified individual identity."
)

start_date, end_date = date_range_filter(default_days=30)
params = [start_date, end_date]

kpis = run_query(user_activity_kpis_sql(), params).iloc[0]
kpi_row(
    [
        ("Unique Visitors (proxy)", f"{int(kpis['TOTAL_VISITORS']):,}"),
        ("Returning (active >1 day)", f"{kpis['PCT_RETURNING']:.1f}%"),
        ("Avg Sessions / Visitor", f"{kpis['AVG_SESSIONS_PER_VISITOR']:.1f}"),
    ]
)

st.subheader("Sessions per Visitor")
per_visitor = run_query(sessions_per_visitor_sql(), params)
if per_visitor.empty:
    st.info("No visitor activity in this date range.")
else:
    p95 = per_visitor["SESSION_COUNT"].quantile(0.95)
    top = run_query(top_visitor_by_sessions_sql(), params).iloc[0]
    if p95 > 0 and top["SESSION_COUNT"] > p95 * 5:
        st.warning(BOT_WARNING.format(ip=top["REQUEST_IP"], sessions=int(top["SESSION_COUNT"]), p95=p95))
    visitor_chart = distribution_histogram(per_visitor, "SESSION_COUNT", "Sessions per Visitor", y_title="Visitors")
    st.altair_chart(visitor_chart, use_container_width=True)
    with st.expander("View as table"):
        st.dataframe(per_visitor.describe(), use_container_width=True)

st.caption(f"Showing data from {start_date} to {end_date}.")
