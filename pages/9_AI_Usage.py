import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

import streamlit as st

from src.charts import multi_trend_line, share_stacked_bar
from src.components import compact, date_range_filter, kpi, kpi_row, previous_window
from src.db import run_query
from src.queries import ai_cohort_sql, ai_kpis_sql, model_mix_sql, model_trend_sql

# Module level so every branch below stays a single indented line - the Snowsight
# editor re-indents multi-line calls inside indented blocks and breaks them.
USAGE_BASIS = (
    "Usage is counted from `ml_request = true`, not from the presence of a model name. "
    "`PROPERTIES:llm` also sits on ~863k events where no request was made - it records the "
    "model a surface is *configured* with, so counting it would overstate AI usage roughly "
    "fourfold."
)
COHORT_NOTE = (
    "Sessions making an AI request convert at **{conv_a:.2f}%** against {conv_b:.2f}% for those "
    "that don't. They also run **shorter** ({med_a:.0f} vs {med_b:.0f} median events), so the "
    "feature appears to shorten the path rather than extend the visit."
)
TYPO_NOTE = (
    "{n:,} events carry `ml_request = 'flase'` - a misspelling in the tracking script. They are "
    "counted as non-AI here. Worth fixing upstream before the volume grows."
)
NO_AI = (
    "No AI requests in this date range. The `ml_request` flag began appearing in the tracking "
    "data during 2026."
)

st.set_page_config(page_title="AI Usage", page_icon="🤖", layout="wide")
st.title("AI Usage")
st.caption(USAGE_BASIS)

start_date, end_date = date_range_filter(default_days=30)
prev_start, prev_end = previous_window(start_date, end_date)
params = [start_date, end_date]

now = run_query(ai_kpis_sql(), params).iloc[0]
was = run_query(ai_kpis_sql(), [prev_start, prev_end]).iloc[0]

now_share = float(now["AI_SESSIONS"]) / float(now["TOTAL_SESSIONS"]) * 100 if float(now["TOTAL_SESSIONS"]) else 0.0
was_share = float(was["AI_SESSIONS"]) / float(was["TOTAL_SESSIONS"]) * 100 if float(was["TOTAL_SESSIONS"]) else 0.0

kpi_row(
    [
        kpi("AI Requests", now["AI_REQUESTS"], was["AI_REQUESTS"]),
        kpi("Sessions Using AI", now["AI_SESSIONS"], was["AI_SESSIONS"]),
        kpi("Share of Sessions", now_share, was_share, decimals=1, suffix="%", delta_as_points=True),
        kpi("Models in Use", now["MODELS_USED"], was["MODELS_USED"]),
    ]
)

if int(now["AI_REQUESTS"]) == 0:
    st.info(NO_AI)
    st.caption(f"Showing {start_date} to {end_date}.")
    st.stop()

if int(now["MALFORMED_FLAG_EVENTS"]) > 0:
    st.warning(TYPO_NOTE.format(n=int(now["MALFORMED_FLAG_EVENTS"])))

st.subheader("Model Mix")
mix = run_query(model_mix_sql(), params)
if mix.empty:
    st.info(NO_AI)
else:
    st.altair_chart(share_stacked_bar(mix, "MODEL", "REQUESTS"), use_container_width=True)
    with st.expander("View as table"):
        st.dataframe(mix, use_container_width=True, hide_index=True)

st.subheader("Model Migration")
trend = run_query(model_trend_sql(), params)
if trend.empty or trend["WEEK_START"].nunique() < 2:
    st.info("Not enough weeks in this range to show a trend. Widen the date range.")
else:
    st.caption("Weekly requests per model. Widen the range to see the full migration curve.")
    st.altair_chart(multi_trend_line(trend, "WEEK_START", "REQUESTS", "MODEL", "Requests"), use_container_width=True)

st.subheader("Does AI Change Behaviour?")
cohort = run_query(ai_cohort_sql(), params)
if len(cohort) < 2:
    st.info("Only one cohort present in this date range - nothing to compare.")
else:
    used = cohort[cohort["COHORT"] == "Used AI"].iloc[0]
    notused = cohort[cohort["COHORT"] == "No AI"].iloc[0]
    st.caption(COHORT_NOTE.format(conv_a=float(used["LEAD_CONV_PCT"]), conv_b=float(notused["LEAD_CONV_PCT"]), med_a=float(used["MEDIAN_EVENTS"]), med_b=float(notused["MEDIAN_EVENTS"])))
    table = cohort.copy()
    table["SESSIONS"] = [compact(v) for v in table["SESSIONS"]]
    table["ENGAGEMENT_PCT"] = [f"{v:.1f}%" for v in table["ENGAGEMENT_PCT"]]
    table["LEAD_CONV_PCT"] = [f"{v:.2f}%" for v in table["LEAD_CONV_PCT"]]
    table["MEDIAN_EVENTS"] = [f"{v:.0f}" for v in table["MEDIAN_EVENTS"]]
    table.columns = ["Cohort", "Sessions", "Engagement", "Lead conversion", "Median events"]
    st.dataframe(table, use_container_width=True, hide_index=True)

st.caption(f"Showing {start_date} to {end_date}. Change is against {prev_start} to {prev_end}.")
