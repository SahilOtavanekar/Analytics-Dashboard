from functools import partial

import streamlit as st

from src.components import date_range_filter, previous_window
from src.db import CACHE_TTL_SECONDS
from src.prefetch import warm

# Strings and the progress callback live at module level so every statement inside
# an indented block below stays on one line - the Snowsight editor re-indents
# multi-line calls inside blocks and breaks them on paste.
PRELOAD_OFFER = (
    "Each page queries Snowflake on its first visit, which takes a few seconds. Preloading runs "
    "all of them now so every page opens instantly afterwards. Roughly 30 seconds, and you can "
    "skip it and pay the cost page by page instead."
)
PRELOAD_DONE = (
    "Results stay cached for up to {mins} minutes. Use **Refresh data** in the sidebar to pull "
    "fresh figures, or change the date range to reload."
)


def _tick(bar, done, total, page):
    bar.progress(done / total, text=f"Loading {page}  ({done}/{total})")


st.set_page_config(page_title="Analytics Dashboard", page_icon="📊", layout="wide")

st.title("Analytics Dashboard")
st.write(
    "Interactive analytics over the `CIT_DATA_CORE.TRACKING.INSYTE_TRAKING_EVENTS` "
    "tracking events table. Use the sidebar to navigate between pages."
)

# The date range lives in session state and is shared by every page, so choosing it
# here means the preload warms exactly the queries those pages will go on to ask for.
start_date, end_date = date_range_filter(default_days=30)
prev_start, prev_end = previous_window(start_date, end_date)

st.page_link("pages/1_Executive_Dashboard.py", label="Executive Dashboard", icon="📊")
st.page_link("pages/2_Event_Analytics.py", label="Event Analytics", icon="📈")
st.page_link("pages/3_Session_Analytics.py", label="Session Analytics", icon="🕒")
st.page_link("pages/4_Audience.py", label="Audience", icon="👥")
st.page_link("pages/5_Campaign_Analytics.py", label="Campaign Analytics", icon="🎯")
st.page_link("pages/6_Conversion.py", label="Conversion", icon="🔻")
st.page_link("pages/7_Pages_and_Sources.py", label="Pages & Sources", icon="🧭")
st.page_link("pages/8_Content_Performance.py", label="Content Performance", icon="📄")
st.page_link("pages/9_AI_Usage.py", label="AI Usage", icon="🤖")
st.page_link("pages/10_Tenant_Breakdown.py", label="Tenant Breakdown", icon="🏢")

st.divider()
st.subheader("Preload")

# Keyed on the date range: changing the range invalidates every cached result, so
# the offer returns rather than reporting a success that no longer holds.
_WARMED = "warmed_range"
current_range = (start_date, end_date)

if st.session_state.get(_WARMED) == current_range:
    st.success(f"All 10 pages are loaded for {start_date} to {end_date}. Navigation is instant.")
    st.caption(PRELOAD_DONE.format(mins=CACHE_TTL_SECONDS // 60))
else:
    st.caption(PRELOAD_OFFER)
    if st.button("Preload all pages", type="primary"):
        bar = st.progress(0.0, text="Starting...")
        ok, total = warm(start_date, end_date, prev_start, prev_end, on_progress=partial(_tick, bar))
        bar.empty()
        st.session_state[_WARMED] = current_range
        st.success(f"Loaded {ok} of {total} queries across 10 pages. Navigation is now instant.")
        st.caption(PRELOAD_DONE.format(mins=CACHE_TTL_SECONDS // 60))
