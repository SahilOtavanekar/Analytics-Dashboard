"""One campaign, in as much detail as its data supports.

Rendered inline on Campaign Analytics, replacing the ranking rather than sitting beneath
it. Streamlit scrolls to the top of the page on every rerun, so content appended below a
chart arrives out of view with nothing to indicate it is there; swapping the view puts the
detail where the reader is already looking, and a Back control returns them.

Which sections exist was measured across the 336 campaigns with >=50 sessions in a
3-month window, not assumed - see the drill-down block in src/queries.py. Four sections
have data for every campaign and always render. Six are conditional, and when one is
absent it says why in a single line rather than drawing an empty chart: an empty axis
reads as "zero", which is a different and stronger claim than "not measured here".

Everything is gated from ONE query. campaign_detail_kpis_sql returns every count the
gates need, so deciding not to render a section never costs a round trip.
"""

import datetime as dt

import streamlit as st

from src.charts import (
    funnel_bar,
    ordered_bar,
    share_stacked_bar,
    top_events_bar,
    trend_line,
)
from src.components import compact, duration_label, kpi, kpi_row
from src.db import run_query
from src.queries import (
    campaign_actions_sql,
    campaign_ai_sql,
    campaign_assets_sql,
    campaign_companies_sql,
    campaign_daily_sql,
    campaign_detail_kpis_sql,
    campaign_duration_bands_sql,
    campaign_funnel_sql,
    campaign_geo_sql,
    campaign_read_depth_sql,
    campaign_sources_sql,
)

SELECTED_KEY = "campaign_detail_id"

# An identified-people floor, not a courtesy. Across the top 25 campaigns two have exactly
# one identified person, and "1 person at acme.com, last seen 12 June" is an individual
# described by a dashboard that promises domain-level aggregation only. Below the floor the
# section is withheld and the count is still reported, so nothing is hidden - only nobody
# is singled out.
_IDENTITY_FLOOR = 5

_ABSENT = {
    "content": "No tracked content. This campaign's traffic carries no `asset` parameter, so there is nothing to attribute reach to. Asset tagging began around March 2026.",
    "depth": "No PDF page-turn events, so read depth cannot be measured for this campaign.",
    "leads": "No form submissions. Half of all campaigns are in this position, so a zero here is common rather than alarming - but it is a true zero, not a gap in tracking.",
    "identity": "No identified visitors. Identity comes from the `email` URL parameter, which only rides on personalised links - and campaign links largely do not carry it.",
    "identity_floor": "{n} identified {noun} — too few to break down by company without describing an individual, so only the total is shown.",
    "ai": "No AI activity. Only 19% of campaigns have any: AI events do carry a campaign ID, they are simply concentrated in a minority of campaigns.",
    "sources": "No referrer recorded on any event, so traffic cannot be attributed to a source.",
}

_DURATION_NOTE = "Duration is the span between a session's first and last event, not time spent reading. Median {median} against a mean of {mean} — the same skew the platform shows."
_TENANT_UNKNOWN = "(untagged)"


def _pct(part, whole) -> float:
    part, whole = float(part), float(whole)
    return part / whole * 100 if whole else 0.0


def _absent(message: str) -> None:
    st.caption(f":material/info: {message}")


def render(campaign_id: str, start: dt.date, end: dt.date) -> dict:
    """Draw the whole detail view. Returns the gate row, for tests and for the caller."""
    params = [start, end, campaign_id]
    k = run_query(campaign_detail_kpis_sql(), params).iloc[0]

    sessions = int(k["SESSIONS"])
    label = k["CAMPAIGN_NAME"] or campaign_id
    named = bool(k["CAMPAIGN_NAME"])

    # ---------------------------------------------------------------- header
    st.subheader(label)
    if not named:
        st.caption("No campaign name recorded — this is the tracking ID. Name capture began March 2026.")
    tenant = str(k["TENANT"]) if k["TENANT"] else _TENANT_UNKNOWN
    span_days = (end - start).days + 1
    bits = [
        f"Tenant `{tenant}`" if tenant != _TENANT_UNKNOWN else "No tenant recorded",
        f"{k['FIRST_SEEN']} to {k['LAST_SEEN']}",
        f"{int(k['ACTIVE_DAYS']):,} of {span_days:,} days active",
    ]
    st.caption(" · ".join(bits))

    if sessions == 0:
        st.info("This campaign has events but no identifiable sessions in this range. Session IDs were not recorded on campaign rows before November 2025.")
        return dict(k)

    kpi_row(
        [
            kpi("Sessions", sessions),
            kpi("Events", k["EVENTS"]),
            kpi("Events / Session", float(k["EVENTS"]) / sessions, decimals=1),
            kpi("Median Duration", k["MEDIAN_DURATION_MINUTES"], formatter=duration_label),
            kpi("Engaged", _pct(k["ACTED_SESSIONS"], sessions), decimals=1, suffix="%"),
            kpi("Converted", _pct(k["LEAD_SESSIONS"], sessions), decimals=2, suffix="%"),
        ]
    )

    # ------------------------------------------------------- activity (always)
    st.markdown("##### Activity over time")
    daily = run_query(campaign_daily_sql(), params)
    if daily.empty:
        _absent("No dated activity in this range.")
    else:
        st.altair_chart(trend_line(daily, "EVENT_DATE", "SESSION_COUNT", "Sessions"), width="stretch")

    # ------------------------------------------------------- duration (always)
    st.markdown("##### Session quality")
    st.caption(_DURATION_NOTE.format(median=duration_label(k["MEDIAN_DURATION_MINUTES"]), mean=duration_label(k["MEAN_DURATION_MINUTES"])))
    bands = run_query(campaign_duration_bands_sql(), params)
    if bands.empty:
        _absent("No sessions to measure.")
    else:
        st.altair_chart(ordered_bar(bands, "BAND", "SESSIONS"), width="stretch")
        st.caption(f"{int(k['INSTANT_SESSIONS']):,} of {sessions:,} sessions ({_pct(k['INSTANT_SESSIONS'], sessions):.0f}%) contain a single event. Median {k['MEDIAN_EVENTS']:.0f} events per session.")

    # ---------------------------------------------------- engagement (always)
    st.markdown("##### Engagement and conversion")
    funnel = run_query(campaign_funnel_sql(), params)
    if not funnel.empty and float(funnel["SESSIONS"].iloc[0]) > 0:
        st.altair_chart(funnel_bar(funnel, "STAGE", "SESSIONS"), width="stretch")
    actions = run_query(campaign_actions_sql(), params)
    if actions.empty:
        _absent("No tracked actions beyond page views.")
    else:
        st.caption("Actions overlap rather than follow one another, so they are shown as reach.")
        st.altair_chart(top_events_bar(actions, "ACTION", "SESSIONS", x_title="Sessions"), width="stretch")
    if int(k["LEAD_SESSIONS"]) == 0:
        _absent(_ABSENT["leads"])

    # --------------------------------------------------------- geography (always)
    st.markdown("##### Where they are")
    geo = run_query(campaign_geo_sql(), params)
    regions = geo[geo["KIND"] == "region"][["LABEL", "SESSIONS"]].rename(columns={"LABEL": "REGION"})
    zones = geo[geo["KIND"] == "timezone"][["LABEL", "SESSIONS"]].rename(columns={"LABEL": "TIMEZONE"})
    if regions.empty:
        _absent("No timezone recorded on any event.")
    else:
        st.caption("From the browser timezone, the only location signal in the data. UTC and Etc/* are settings rather than places, so they group as unknown.")
        st.altair_chart(ordered_bar(regions, "REGION", "SESSIONS", x_title="Sessions"), width="stretch")
        with st.expander(f"Individual timezones ({int(k['TIMEZONES'])} seen)"):
            st.dataframe(zones, width="stretch", hide_index=True)

    # ------------------------------------------------------ content (conditional)
    st.markdown("##### Content")
    if int(k["ASSETS"]) == 0:
        _absent(_ABSENT["content"])
    else:
        st.caption(f"{int(k['CONTENT_SESSIONS']):,} of {sessions:,} sessions ({_pct(k['CONTENT_SESSIONS'], sessions):.0f}%) opened one of {int(k['ASSETS']):,} assets.")
        assets = run_query(campaign_assets_sql(), params)
        if assets.empty:
            _absent(_ABSENT["content"])
        else:
            st.altair_chart(top_events_bar(assets, "ASSET", "SESSIONS", x_title="Sessions"), width="stretch")
            with st.expander("With engagement depth"):
                st.dataframe(assets, width="stretch", hide_index=True)
        if int(k["PDF_EVENTS"]) == 0:
            _absent(_ABSENT["depth"])
        else:
            depth = run_query(campaign_read_depth_sql(), params)
            if depth.empty:
                _absent(_ABSENT["depth"])
            else:
                st.caption(f"Average page reached inside each document, from {int(k['PDF_EVENTS']):,} page-turn events.")
                st.altair_chart(top_events_bar(depth, "ASSET", "AVG_PAGE_REACHED", x_title="Average page reached"), width="stretch")
                with st.expander("Deepest page reached per asset"):
                    st.dataframe(depth, width="stretch", hide_index=True)

    # ----------------------------------------------------- audience (conditional)
    st.markdown("##### Accounts reached")
    people = int(k["PEOPLE"])
    if people == 0:
        _absent(_ABSENT["identity"])
    elif people < _IDENTITY_FLOOR:
        _absent(_ABSENT["identity_floor"].format(n=people, noun="person" if people == 1 else "people"))
    else:
        st.caption(f"{people:,} identified people across {int(k['COMPANIES']):,} companies — {_pct(k['IDENTIFIED_SESSIONS'], sessions):.0f}% of sessions. Consumer mailboxes are excluded; individual addresses are never shown.")
        companies = run_query(campaign_companies_sql(), params)
        if companies.empty:
            _absent("Identified visitors all used consumer mailboxes, so no company can be named.")
        else:
            st.altair_chart(top_events_bar(companies, "COMPANY", "SESSIONS", x_title="Sessions"), width="stretch")
            with st.expander("With headcount per company"):
                st.dataframe(companies, width="stretch", hide_index=True)

    # ------------------------------------------------------ sources (conditional)
    st.markdown("##### How they arrived")
    sources = run_query(campaign_sources_sql(), params)
    groups = sources[sources["KIND"] == "group"][["LABEL", "EVENTS"]].rename(columns={"LABEL": "SOURCE_GROUP"})
    referrers = sources[sources["KIND"] == "referrer"][["LABEL", "EVENTS"]].rename(columns={"LABEL": "REFERRER"})
    if groups.empty:
        _absent(_ABSENT["sources"])
    else:
        st.altair_chart(share_stacked_bar(groups, "SOURCE_GROUP", "EVENTS"), width="stretch")
        if referrers.empty:
            st.caption("No external referrers — traffic arrived directly, which is normal for a personalised link opened from an email client.")
        else:
            st.altair_chart(top_events_bar(referrers, "REFERRER", "EVENTS", x_title="Events"), width="stretch")

    # ----------------------------------------------------------- AI (conditional)
    st.markdown("##### AI usage")
    if int(k["AI_EVENTS"]) == 0:
        _absent(_ABSENT["ai"])
    else:
        attach = _pct(k["AI_SESSIONS"], k["CONTENT_SESSIONS"]) if int(k["CONTENT_SESSIONS"]) else None
        line = f"{int(k['AI_EVENTS']):,} AI-assisted events across {int(k['AI_SESSIONS']):,} sessions ({_pct(k['AI_SESSIONS'], sessions):.0f}% of all sessions)"
        st.caption(line + (f", attaching to {attach:.0f}% of content sessions." if attach is not None else ". No content sessions, so an attach rate is not defined."))
        ai = run_query(campaign_ai_sql(), params)
        if ai.empty:
            _absent(_ABSENT["ai"])
        else:
            st.altair_chart(share_stacked_bar(ai, "MODEL", "REQUESTS"), width="stretch")
            with st.expander("Requests and sessions per model"):
                st.dataframe(ai, width="stretch", hide_index=True)

    return dict(k)
