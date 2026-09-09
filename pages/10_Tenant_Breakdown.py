import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

import streamlit as st

from src.charts import diverging_bar
from src.components import compact, date_range_filter, kpi, kpi_row, previous_window
from src.db import run_query
from src.queries import (
    tenant_comparison_sql,
    tenant_kpis_sql,
    tenant_movement_sql,
    tenant_spread_sql,
)

# Module level so every branch below stays a single indented line - the Snowsight
# editor re-indents multi-line calls inside indented blocks and breaks them.
PREMISE = (
    "Every other page in this dashboard reports a platform total across all tenants. That is "
    "correct as a total, but it is not any one client's experience - this page shows how far "
    "apart they actually are."
)
SPREAD_NOTE = (
    "Across **{n:,} tenants** with at least 50 sessions, **{zero} generated no leads at all** in "
    "this window. The median tenant converts at {median:.2f}%, the top decile at {p90:.2f}%, and "
    "the best at **{hi:.2f}%** - against a blended {blended:.2f}%. Engagement splits the same way: "
    "{eng_lo:.1f}% to {eng_hi:.1f}%, median {eng_med:.1f}%. The platform average is a real number "
    "about the platform; it is not a forecast for any one account."
)
MOVEMENT_NOTE = (
    "Change in events against the previous equal-length window. This is what a platform-wide "
    "'traffic is down' cannot tell you: whether the fall is broad, or a handful of accounts "
    "going quiet."
)
OPAQUE_IDS = (
    "`tenant_id` is an opaque key - the table carries no tenant name. The campaign column is the "
    "tenant's most frequent campaign name, offered as a hint to identify the account, not as its "
    "name. Mapping IDs to client names upstream would make this page substantially more usable."
)
COVERAGE_WARNING = (
    "Only {pct:.1f}% of events in this range carry a tenant ID. Tenant tagging began in Q4 2025, "
    "so a range reaching further back will under-report tenant activity and group the remainder "
    "as '(untagged)'."
)

st.set_page_config(page_title="Tenant Breakdown", page_icon="🏢", layout="wide")
st.title("Tenant Breakdown")
st.caption(PREMISE)

start_date, end_date = date_range_filter(default_days=30)
prev_start, prev_end = previous_window(start_date, end_date)
params = [start_date, end_date]

now = run_query(tenant_kpis_sql(), params).iloc[0]
was = run_query(tenant_kpis_sql(), [prev_start, prev_end]).iloc[0]
spread = run_query(tenant_spread_sql(), params).iloc[0]

kpi_row(
    [
        kpi("Active Tenants", now["ACTIVE_TENANTS"], was["ACTIVE_TENANTS"]),
        kpi("Events Tagged", now["PCT_TAGGED"], was["PCT_TAGGED"], decimals=1, suffix="%", delta_as_points=True),
        kpi("Blended Conversion", spread["BLENDED_CONV_PCT"], decimals=2, suffix="%"),
        kpi("Median Tenant", spread["CONV_MEDIAN"], decimals=2, suffix="%"),
    ]
)

if float(now["PCT_TAGGED"]) < 95:
    st.warning(COVERAGE_WARNING.format(pct=float(now["PCT_TAGGED"])))

st.subheader("Why the Blended Numbers Mislead")
if int(spread["TENANTS_COMPARED"]) == 0:
    st.info("No tenant in this range has enough sessions to compare. Widen the date range.")
else:
    st.caption(SPREAD_NOTE.format(n=int(spread["TENANTS_COMPARED"]), zero=int(spread["ZERO_CONV_TENANTS"]), median=float(spread["CONV_MEDIAN"]), p90=float(spread["CONV_P90"]), hi=float(spread["CONV_MAX"]), blended=float(spread["BLENDED_CONV_PCT"]), eng_lo=float(spread["ENG_MIN"]), eng_hi=float(spread["ENG_MAX"]), eng_med=float(spread["ENG_MEDIAN"])))

st.subheader("Tenant Comparison")
st.caption(OPAQUE_IDS)
comparison = run_query(tenant_comparison_sql(), params + params)
if comparison.empty:
    st.info("No tenant in this range has at least 50 sessions.")
else:
    table = comparison.copy()
    table["SESSIONS"] = [compact(v) for v in table["SESSIONS"]]
    table["ENGAGEMENT_PCT"] = [f"{v:.1f}%" for v in table["ENGAGEMENT_PCT"]]
    table["LEAD_CONV_PCT"] = [f"{v:.2f}%" for v in table["LEAD_CONV_PCT"]]
    table["MEDIAN_EVENTS"] = [f"{v:.0f}" for v in table["MEDIAN_EVENTS"]]
    table.columns = ["Tenant", "Top campaign", "Sessions", "Engagement", "Lead conversion", "Median events"]
    st.dataframe(table, width="stretch", hide_index=True)

st.subheader("Biggest Movers")
st.caption(MOVEMENT_NOTE)
movement = run_query(tenant_movement_sql(), params + [prev_start, prev_end])
if movement.empty:
    st.info("No tenant activity to compare across these two windows.")
else:
    st.altair_chart(diverging_bar(movement, "TENANT", "CHANGE", x_title="Change in events"), width="stretch")
    gone = movement[(movement["PRIOR_EVENTS"] > 0) & (movement["CURRENT_EVENTS"] == 0)]
    if not gone.empty:
        st.warning(f"{len(gone)} tenant(s) recorded activity in the previous window and none in this one: {', '.join(gone['TENANT'].astype(str))}.")
    with st.expander("View as table"):
        st.dataframe(movement, width="stretch", hide_index=True)

st.caption(f"Showing {start_date} to {end_date}. Change is against {prev_start} to {prev_end}.")
