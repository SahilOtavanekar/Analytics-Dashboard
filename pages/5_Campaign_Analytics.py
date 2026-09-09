import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

import streamlit as st

from src.campaign_detail import SELECTED_KEY
from src.campaign_detail import render as detail_render
from src.charts import CAMPAIGN_PICK, campaign_bar
from src.components import date_range_filter, kpi, kpi_row, previous_window
from src.db import run_query
from src.queries import campaign_kpis_sql, top_campaigns_sql

st.set_page_config(page_title="Campaign Analytics", page_icon="🎯", layout="wide")
st.title("Campaign Analytics")

CAMPAIGN_NOTE = "Ranked by sessions. Hover a bar for that campaign's figures, or click it to open a full breakdown."
PICK_LABEL = "Open a campaign"
PICK_HINT = "Pick a campaign, or click its bar above"
ORPHANED = ":material/info: The open campaign is no longer in the top ten for this date range. Its figures below are recalculated for the new range."

# The tooltip's own fields, minus the ones measured and rejected: identified people and
# companies (13 of the top 25 campaigns have none and two have exactly one, so the column
# would be a blank that reads as a zero, and n=1 names a person) and AI attach (0.0% on
# eight of the top ten, because ml_request events carry no campaign ID).
TABLE_COLUMNS = [
    "CAMPAIGN_LABEL",
    "TENANT",
    "SESSIONS",
    "SESSION_SHARE_PCT",
    "EVENTS",
    "EVENTS_PER_SESSION",
    "ASSET_PCT",
    "LEAD_PCT",
    "FIRST_SEEN",
    "LAST_SEEN",
    "ACTIVE_DAYS",
]
TABLE_HEADERS = {
    "CAMPAIGN_LABEL": "Campaign",
    "TENANT": "Tenant ID",
    "SESSIONS": "Sessions",
    "SESSION_SHARE_PCT": "% of campaign sessions",
    "EVENTS": "Events",
    "EVENTS_PER_SESSION": "Events / session",
    "ASSET_PCT": "% sessions opened content",
    "LEAD_PCT": "% sessions submitted",
    "FIRST_SEEN": "First seen",
    "LAST_SEEN": "Last seen",
    "ACTIVE_DAYS": "Active days",
}

start_date, end_date = date_range_filter(default_days=30)
prev_start, prev_end = previous_window(start_date, end_date)
params = [start_date, end_date]

top_campaigns = run_query(top_campaigns_sql(), params)

# The detail view REPLACES the ranking rather than appending to it, and that includes the
# page-level KPI row - those three figures describe all campaigns, so leaving them above
# one campaign's breakdown would invite reading them as its own. Streamlit also scrolls to
# the top of the page on every rerun, so a detail section added below the chart would
# arrive out of view with nothing signalling it. The selected ID lives in a durable key, so
# the sidebar's date and filter controls can rerun the page without losing it.
selected = st.session_state.get(SELECTED_KEY)
if selected:
    if st.button(":material/arrow_back: Back to all campaigns"):
        st.session_state.pop(SELECTED_KEY, None)
        st.rerun()
    if not top_campaigns.empty and selected not in set(top_campaigns["CAMPAIGN_ID"].astype(str)):
        # The reader narrowed the date range and the open campaign left the top ten. Its
        # detail is still valid for the new range, so it stays open - but say so, because
        # the alternative is a page that silently discards what they were reading.
        st.caption(ORPHANED)
    detail_render(selected, start_date, end_date)
    st.caption(f"Showing {start_date} to {end_date}.")
    st.stop()

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
st.caption(CAMPAIGN_NOTE)
if top_campaigns.empty:
    st.info("No campaign activity in this date range.")
else:
    chart, notes = campaign_bar(top_campaigns, start_date, end_date)
    # on_select reruns the script when a bar is clicked; the selection comes back keyed by
    # the parameter campaign_bar declared. An empty selection is a click on empty space,
    # which must NOT close an open campaign - so it is ignored rather than written through.
    event = st.altair_chart(chart, width="stretch", on_select="rerun", key="campaign_chart")
    picked = (getattr(event, "selection", None) or {}).get(CAMPAIGN_PICK) or []
    if picked and picked[0].get("CAMPAIGN_ID"):
        st.session_state[SELECTED_KEY] = str(picked[0]["CAMPAIGN_ID"])
        st.rerun()
    for note in notes:
        st.caption(note)
    # A selectbox beside the chart, not instead of it. Chart clicks cannot be driven by
    # keyboard, cannot be exercised by the test suite, and cannot be verified inside
    # Snowflake from here - so the same drill-down needs one route that is certain.
    labels = {f"{r.CAMPAIGN_LABEL}": str(r.CAMPAIGN_ID) for r in top_campaigns.itertuples()}
    chosen = st.selectbox(PICK_LABEL, list(labels), index=None, placeholder=PICK_HINT, key="campaign_picker")
    if chosen:
        st.session_state[SELECTED_KEY] = labels[chosen]
        st.rerun()
    # The same figures as the tooltip, because a tooltip cannot be screenshotted, read on
    # a touch device, or copied into a deck - and these are the numbers people quote.
    with st.expander("View as table"):
        st.dataframe(top_campaigns[TABLE_COLUMNS].rename(columns=TABLE_HEADERS), width="stretch", hide_index=True)

st.caption(f"Showing {start_date} to {end_date}. Change is against {prev_start} to {prev_end}.")
