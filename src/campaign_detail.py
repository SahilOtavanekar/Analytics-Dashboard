"""One campaign, in as much detail as its data supports.

Rendered inline on Campaign Analytics, replacing the ranking rather than sitting beneath
it. Streamlit scrolls to the top of the page on every rerun, so content appended below a
chart arrives out of view with nothing to indicate it is there; swapping the view puts the
detail where the reader is already looking, and a Back control returns them.

This module is now presentation only. Every query and every gate lives in
src/campaign_report.py, which imports no Streamlit, so the download button here and the
scheduled email planned next render from the same collect() call rather than each fetching
its own version of the truth.

Which sections exist was measured across the 336 campaigns with >=50 sessions in a 3-month
window, not assumed - see the drill-down block in src/queries.py. Four sections have data
for every campaign and always render. Six are conditional, and when one is absent it says
why in a single line rather than drawing an empty chart: an empty axis reads as "zero",
which is a different and stronger claim than "not measured here".
"""

import datetime as dt

import streamlit as st

from src.campaign_report import (
    IDENTITY_FLOOR,
    STATUS_MISSING,
    STATUS_NO_SESSIONS,
    classify_lookup,
    collect,
    pct,
)
from src.charts import (
    funnel_bar,
    ordered_bar,
    share_stacked_bar,
    top_events_bar,
    trend_bar,
)
from src.components import duration_label, kpi, kpi_row
from src.db import run_query

SELECTED_KEY = "campaign_detail_id"

# Re-exported so the page and the suites keep one import site for both. classify_lookup and
# the floor are defined in campaign_report because they are decisions about data, not about
# rendering, and the emailed report has to apply them identically.
_IDENTITY_FLOOR = IDENTITY_FLOOR

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


def _absent(message: str) -> None:
    st.caption(f":material/info: {message}")


def render(campaign_id: str, start: dt.date, end: dt.date, data: dict | None = None) -> dict:
    """Draw the whole detail view. Returns the collected report, for the caller and tests.

    `data` lets the caller pass a report it has already collected - the page needs one before
    this runs, to decide whether a share link and download buttons make sense at all. Omitted,
    this collects its own; run_query caches on (sql, params) either way, so passing it is an
    explicitness rather than an optimisation.
    """
    if data is None:
        data = collect(run_query, campaign_id, start, end)
    k = data["kpis"]

    # Nothing for this id in this window - answered before anything is drawn, so the answer
    # is one message and not six. Rendered further down, this produced a heading presenting
    # the id as a campaign, a note explaining that its name was not recorded, a tenant line
    # reading "None to None - 0 of 31 days active", and an offer to share the link. Every one
    # of those is a statement about a campaign that is not there, and the reader had to get
    # past all of them to reach the sentence that mattered.
    if data["status"] == STATUS_MISSING:
        if data["reason"] == "wrong_window":
            f = data["lookup"]
            named = f" (`{f['CAMPAIGN_NAME']}`)" if f["CAMPAIGN_NAME"] else ""
            st.warning(f"Campaign `{campaign_id}`{named} has no activity between {start} and {end}. It ran **{f['FIRST_EVER']} to {f['LAST_EVER']}** with {int(f['EVENTS_EVER']):,} events — widen the date range in the sidebar to see it.")
        else:
            st.warning(f"No campaign `{campaign_id}` exists in the tracked data. If you followed a link, the ID may be wrong — IDs are case-sensitive.")
        return data

    sessions = int(k["SESSIONS"])

    # ---------------------------------------------------------------- header
    st.subheader(data["label"])
    if not data["named"]:
        st.caption("No campaign name recorded — this is the tracking ID. Name capture began March 2026.")
    tenant = str(k["TENANT"]) if k["TENANT"] else _TENANT_UNKNOWN
    span_days = (end - start).days + 1
    bits = [
        f"Tenant `{tenant}`" if tenant != _TENANT_UNKNOWN else "No tenant recorded",
        f"{k['FIRST_SEEN']} to {k['LAST_SEEN']}",
        f"{int(k['ACTIVE_DAYS']):,} of {span_days:,} days active",
    ]
    st.caption(" · ".join(bits))

    # A real campaign whose sessions predate session capture. Distinct from the branch above,
    # and it keeps the header - there genuinely is a campaign to describe.
    if data["status"] == STATUS_NO_SESSIONS:
        st.info(f"Campaign `{campaign_id}` recorded {int(k['EVENTS']):,} events in this range but no identifiable sessions. Session IDs were not recorded on campaign rows before November 2025, so only event counts exist this far back.")
        return data

    kpi_row(
        [
            kpi("Sessions", sessions),
            kpi("Events", k["EVENTS"]),
            kpi("Events / Session", float(k["EVENTS"]) / sessions, decimals=1),
            kpi("Median Duration", k["MEDIAN_DURATION_MINUTES"], formatter=duration_label),
            kpi("Engaged", pct(k["ACTED_SESSIONS"], sessions), decimals=1, suffix="%"),
            kpi("Converted", pct(k["LEAD_SESSIONS"], sessions), decimals=2, suffix="%"),
        ]
    )

    # ------------------------------------------------------- activity (always)
    st.markdown("##### Activity over time")
    daily = data.get("daily")
    if daily is None or daily.empty:
        _absent("No dated activity in this range.")
    else:
        st.altair_chart(trend_bar(daily, "EVENT_DATE", "SESSION_COUNT", "Sessions"), width="stretch")

    # ------------------------------------------------------- duration (always)
    st.markdown("##### Session quality")
    st.caption(_DURATION_NOTE.format(median=duration_label(k["MEDIAN_DURATION_MINUTES"]), mean=duration_label(k["MEAN_DURATION_MINUTES"])))
    bands = data.get("duration")
    if bands is None or bands.empty:
        _absent("No sessions to measure.")
    else:
        st.altair_chart(ordered_bar(bands, "BAND", "SESSIONS"), width="stretch")
        st.caption(f"{int(k['INSTANT_SESSIONS']):,} of {sessions:,} sessions ({pct(k['INSTANT_SESSIONS'], sessions):.0f}%) contain a single event. Median {k['MEDIAN_EVENTS']:.0f} events per session.")

    # ---------------------------------------------------- engagement (always)
    st.markdown("##### Engagement and conversion")
    funnel = data.get("funnel")
    if funnel is not None and len(funnel) and float(funnel["SESSIONS"].iloc[0]) > 0:
        st.altair_chart(funnel_bar(funnel, "STAGE", "SESSIONS"), width="stretch")
    # The action-reach chart was removed from this page: a second bar chart under the funnel,
    # whose bars deliberately do NOT sum to the funnel stage above them, cost more to explain
    # than it paid back. The figures are not lost - collect() still returns them and the
    # downloaded PDF still tabulates them, where a table carries the overlap without a reader
    # having to hold "these bars overlap" in their head while comparing bar lengths.
    #
    # Consent is stated, not charted. It is a decision about a cookie banner rather than interest
    # in the campaign, and it used to occupy two places at once: accepting was its own bar, while
    # rejecting arrived as an ordinary click and so read as engagement. Both are excluded from
    # Engaged and from conversion, and reported here as the fact they are.
    said_yes, said_no = int(k["CONSENT_YES_SESSIONS"]), int(k["CONSENT_NO_SESSIONS"])
    if said_yes or said_no:
        _absent(f"Cookie banner: {said_yes:,} accepted, {said_no:,} rejected "
                f"({pct(said_yes + said_no, sessions):.0f}% of sessions answered it). Counted as "
                f"neither engagement nor conversion.")
    if int(k["LEAD_SESSIONS"]) == 0:
        _absent(_ABSENT["leads"])

    # --------------------------------------------------------- geography (always)
    st.markdown("##### Where they are")
    regions = data.get("regions")
    if regions is None or regions.empty:
        _absent("No timezone recorded on any event.")
    else:
        st.caption("From the browser timezone, the only location signal in the data. UTC and Etc/* are settings rather than places, so they group as unknown.")
        st.altair_chart(ordered_bar(regions, "REGION", "SESSIONS", x_title="Sessions"), width="stretch")
        with st.expander(f"Individual timezones ({int(k['TIMEZONES'])} seen)"):
            st.dataframe(data["timezones"], width="stretch", hide_index=True)

    # ------------------------------------------------------ content (conditional)
    st.markdown("##### Content")
    assets = data.get("assets")
    if assets is None or assets.empty:
        _absent(_ABSENT["content"])
    else:
        st.caption(f"{int(k['CONTENT_SESSIONS']):,} of {sessions:,} sessions ({pct(k['CONTENT_SESSIONS'], sessions):.0f}%) opened one of {int(k['ASSETS']):,} assets.")
        st.altair_chart(top_events_bar(assets, "ASSET", "SESSIONS", x_title="Sessions"), width="stretch")
        with st.expander("With engagement depth"):
            st.dataframe(assets, width="stretch", hide_index=True)
        depth = data.get("depth")
        if depth is None or depth.empty:
            _absent(_ABSENT["depth"])
        else:
            st.caption(f"Average page reached inside each document, from {int(k['PDF_EVENTS']):,} page-turn events.")
            st.altair_chart(top_events_bar(depth, "ASSET", "AVG_PAGE_REACHED", x_title="Average page reached"), width="stretch")
            with st.expander("Deepest page reached per asset"):
                st.dataframe(depth, width="stretch", hide_index=True)

    # ----------------------------------------------------- audience (conditional)
    st.markdown("##### Accounts reached")
    people = int(k["PEOPLE"])
    companies = data.get("companies")
    if people == 0:
        _absent(_ABSENT["identity"])
    elif people < _IDENTITY_FLOOR:
        _absent(_ABSENT["identity_floor"].format(n=people, noun="person" if people == 1 else "people"))
    elif companies is None or companies.empty:
        _absent("Identified visitors all used consumer mailboxes, so no company can be named.")
    else:
        st.caption(f"{people:,} identified people across {int(k['COMPANIES']):,} companies — {pct(k['IDENTIFIED_SESSIONS'], sessions):.0f}% of sessions. Consumer mailboxes are excluded; individual addresses are never shown.")
        st.altair_chart(top_events_bar(companies, "COMPANY", "SESSIONS", x_title="Sessions"), width="stretch")
        with st.expander("With headcount per company"):
            st.dataframe(companies, width="stretch", hide_index=True)

    # ------------------------------------------------------ sources (conditional)
    st.markdown("##### How they arrived")
    groups = data.get("sources")
    referrers = data.get("referrers")
    if groups is None or groups.empty:
        _absent(_ABSENT["sources"])
    else:
        st.altair_chart(share_stacked_bar(groups, "SOURCE_GROUP", "EVENTS"), width="stretch")
        if referrers is None or referrers.empty:
            st.caption("No external referrers — traffic arrived directly, which is normal for a personalised link opened from an email client.")
        else:
            st.altair_chart(top_events_bar(referrers, "REFERRER", "EVENTS", x_title="Events"), width="stretch")

    # ----------------------------------------------------------- AI (conditional)
    st.markdown("##### AI usage")
    ai = data.get("ai")
    if ai is None or ai.empty:
        _absent(_ABSENT["ai"])
    else:
        attach = pct(k["AI_SESSIONS"], k["CONTENT_SESSIONS"]) if int(k["CONTENT_SESSIONS"]) else None
        line = f"{int(k['AI_EVENTS']):,} AI-assisted events across {int(k['AI_SESSIONS']):,} sessions ({pct(k['AI_SESSIONS'], sessions):.0f}% of all sessions)"
        st.caption(line + (f", attaching to {attach:.0f}% of content sessions." if attach is not None else ". No content sessions, so an attach rate is not defined."))
        st.altair_chart(share_stacked_bar(ai, "MODEL", "REQUESTS"), width="stretch")
        with st.expander("Requests and sessions per model"):
            st.dataframe(ai, width="stretch", hide_index=True)

    return data
