import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

import streamlit as st

from src.charts import ordered_bar, top_events_bar
from src.components import compact, date_range_filter, kpi, kpi_row, previous_window
from src.db import run_query
from src.queries import (
    audience_kpis_sql,
    geo_region_sql,
    identity_cohort_sql,
    locale_share_sql,
    top_accounts_sql,
    top_timezones_sql,
    top_visitor_by_sessions_sql,
    user_activity_kpis_sql,
    visitor_sessions_bands_sql,
    visitor_sessions_percentiles_sql,
)

# Kept at module level so every branch below stays a single indented line - the
# Snowsight editor re-indents multi-line calls inside indented blocks and breaks them.
BOT_WARNING = (
    "One IP address ({ip}) accounts for {sessions:,} sessions in this range - {ratio:.0f}x the "
    "95th percentile of {p95:,.0f}. This is almost certainly automated traffic (bot, crawler, "
    "or a shared corporate network) rather than a single person. It is counted in the "
    "'over 100 sessions' band below, not hidden."
)
IDENTITY_NOTE = (
    "Identified sessions are **{ratio:.1f}x more engaged** ({eng_a:.1f}% vs {eng_b:.1f}%) and "
    "convert at {conv_a:.2f}% against {conv_b:.2f}%. An address usually means the visitor "
    "arrived from a targeted email, so this measures what that channel is worth."
)
ACCOUNTS_NOTE = (
    "Companies are derived from the email domain. {free_people:,} visitors used a consumer "
    "mailbox ({free_sessions:,} sessions) and are excluded from this ranking - they are people, "
    "not accounts. Individual addresses are never displayed or queryable here."
)
EMAIL_CAVEAT = (
    "Only well-formed addresses count. The tracker also writes 18 non-email placeholder strings "
    "into the same parameter, covering 408,194 events in a typical 30-day window; treating those "
    "as identities would overstate coverage roughly threefold."
)
NO_IDENTITY = (
    "No identified visitors in this date range. Identity comes from the `email` URL parameter, "
    "which only appears on traffic arriving from targeted email campaigns."
)
IP_PROXY_NOTE = (
    "The majority of traffic carries no identity. For those sessions IP address is the closest "
    "available proxy for 'who', acknowledging that it is shared by NAT, VPNs and bots and is "
    "not a verified individual."
)

st.set_page_config(page_title="Audience", page_icon="👥", layout="wide")
st.title("Audience")
st.caption(
    "Who the traffic is, from three angles: identity where visitors are known, company "
    "where an email domain resolves to one, and geography from the browser timezone."
)

start_date, end_date = date_range_filter(default_days=30)
prev_start, prev_end = previous_window(start_date, end_date)
params = [start_date, end_date]

now = run_query(audience_kpis_sql(), params).iloc[0]
was = run_query(audience_kpis_sql(), [prev_start, prev_end]).iloc[0]

now_share = float(now["IDENTIFIED_SESSIONS"]) / float(now["TOTAL_SESSIONS"]) * 100 if float(now["TOTAL_SESSIONS"]) else 0.0
was_share = float(was["IDENTIFIED_SESSIONS"]) / float(was["TOTAL_SESSIONS"]) * 100 if float(was["TOTAL_SESSIONS"]) else 0.0

kpi_row(
    [
        kpi("Identified People", now["IDENTIFIED_PEOPLE"], was["IDENTIFIED_PEOPLE"]),
        kpi("Companies", now["COMPANIES"], was["COMPANIES"]),
        kpi("Identified Sessions", now["IDENTIFIED_SESSIONS"], was["IDENTIFIED_SESSIONS"]),
        kpi("Share Identified", now_share, was_share, decimals=1, suffix="%", delta_as_points=True),
    ]
)
st.caption(EMAIL_CAVEAT)

st.subheader("Identified vs Anonymous")
cohort = run_query(identity_cohort_sql(), params)
if len(cohort) < 2:
    st.info(NO_IDENTITY)
else:
    known = cohort[cohort["COHORT"] == "Identified"].iloc[0]
    unknown = cohort[cohort["COHORT"] == "Anonymous"].iloc[0]
    eng_b = float(unknown["ENGAGEMENT_PCT"]) or 1e-9
    st.caption(IDENTITY_NOTE.format(ratio=float(known["ENGAGEMENT_PCT"]) / eng_b, eng_a=float(known["ENGAGEMENT_PCT"]), eng_b=float(unknown["ENGAGEMENT_PCT"]), conv_a=float(known["LEAD_CONV_PCT"]), conv_b=float(unknown["LEAD_CONV_PCT"])))
    table = cohort.copy()
    table["SESSIONS"] = [compact(v) for v in table["SESSIONS"]]
    table["ENGAGEMENT_PCT"] = [f"{v:.1f}%" for v in table["ENGAGEMENT_PCT"]]
    table["LEAD_CONV_PCT"] = [f"{v:.2f}%" for v in table["LEAD_CONV_PCT"]]
    table["MEDIAN_EVENTS"] = [f"{v:.0f}" for v in table["MEDIAN_EVENTS"]]
    table.columns = ["Cohort", "Sessions", "Engagement", "Lead conversion", "Median events"]
    st.dataframe(table, use_container_width=True, hide_index=True)

st.subheader("Top Companies")
accounts = run_query(top_accounts_sql(), params)
if accounts.empty:
    st.info(NO_IDENTITY)
else:
    free = accounts[accounts["KIND"] == "Free mail"]
    corporate = accounts[accounts["KIND"] == "Corporate"]
    st.caption(ACCOUNTS_NOTE.format(free_people=int(free["PEOPLE"].sum()), free_sessions=int(free["SESSIONS"].sum())))
    if corporate.empty:
        st.info("No corporate domains in this date range.")
    else:
        st.altair_chart(top_events_bar(corporate, "COMPANY", "SESSIONS", x_title="Sessions"), use_container_width=True)
    with st.expander("All domains, including consumer mailboxes"):
        st.dataframe(accounts, use_container_width=True, hide_index=True)

st.subheader("Where They Are")
st.caption("Derived from the browser timezone, which is present on every event. UTC and Etc/* are settings rather than places, so they are grouped as unknown.")
regions = run_query(geo_region_sql(), params)
if regions.empty:
    st.info("No sessions in this date range.")
else:
    st.altair_chart(ordered_bar(regions, "REGION", "SESSIONS", x_title="Sessions"), use_container_width=True)
    with st.expander("Individual timezones"):
        st.dataframe(run_query(top_timezones_sql(), params), use_container_width=True, hide_index=True)

st.subheader("Language")
locales = run_query(locale_share_sql(), params)
if locales.empty:
    st.info("No locale recorded in this date range.")
else:
    st.altair_chart(ordered_bar(locales, "LOCALE", "SESSIONS", x_title="Sessions"), use_container_width=True)

st.subheader("Anonymous Activity (IP proxy)")
st.caption(IP_PROXY_NOTE)
ip_now = run_query(user_activity_kpis_sql(), params).iloc[0]
ip_was = run_query(user_activity_kpis_sql(), [prev_start, prev_end]).iloc[0]

kpi_row(
    [
        kpi("Unique IP Addresses", ip_now["TOTAL_VISITORS"], ip_was["TOTAL_VISITORS"]),
        kpi("Returning (active >1 day)", ip_now["PCT_RETURNING"], ip_was["PCT_RETURNING"], decimals=1, suffix="%", delta_as_points=True),
        kpi("Median Sessions / IP", ip_now["MEDIAN_SESSIONS_PER_VISITOR"], ip_was["MEDIAN_SESSIONS_PER_VISITOR"], decimals=1),
        kpi("Single-session IPs", ip_now["PCT_SINGLE_SESSION"], ip_was["PCT_SINGLE_SESSION"], decimals=1, suffix="%", delta_as_points=True),
    ]
)
st.caption(
    f"Median, not mean - one IP distorts the average badly. The mean is "
    f"{ip_now['MEAN_SESSIONS_PER_VISITOR']:.1f} sessions per IP against a median of "
    f"{ip_now['MEDIAN_SESSIONS_PER_VISITOR']:.0f}."
)

bands = run_query(visitor_sessions_bands_sql(), params)
if bands.empty:
    st.info("No visitor activity in this date range.")
else:
    st.altair_chart(ordered_bar(bands, "BAND", "VISITORS", x_title="IP addresses"), use_container_width=True)
    with st.expander("Percentiles and edge cases"):
        st.dataframe(run_query(visitor_sessions_percentiles_sql(), params).T, use_container_width=True)

stats = run_query(visitor_sessions_percentiles_sql(), params).iloc[0]
top = run_query(top_visitor_by_sessions_sql(), params)
p95 = float(stats["P95_SESSIONS"])
if not top.empty and p95 > 0 and float(top["SESSION_COUNT"].iloc[0]) > p95 * 5:
    st.warning(BOT_WARNING.format(ip=top["REQUEST_IP"].iloc[0], sessions=int(top["SESSION_COUNT"].iloc[0]), ratio=float(top["SESSION_COUNT"].iloc[0]) / p95, p95=p95))

st.caption(f"Showing {start_date} to {end_date}. Change is against {prev_start} to {prev_end}.")
