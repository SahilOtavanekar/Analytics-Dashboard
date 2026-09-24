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
    concentration,
    STATUS_MISSING,
    STATUS_NO_SESSIONS,
    classify_lookup,
    collect,
    pct,
)
from src.charts import (
    donut_chart,
    ordered_bar,
    page_bar,
    share_stacked_bar,
    top_events_bar,
    trend_bar,
)
from src.components import duration_label, kpi
from src.db import run_query
from src.queries import EVENT_BUCKET_ORDER

SELECTED_KEY = "campaign_detail_id"

# Re-exported so the page and the suites keep one import site for both. classify_lookup and
# the floor are defined in campaign_report because they are decisions about data, not about
# rendering, and the emailed report has to apply them identically.
_IDENTITY_FLOOR = IDENTITY_FLOOR

# Kept at module level so the branch using it stays a single indented line - the Snowsight
# editor re-indents multi-line calls inside indented blocks and breaks them.
# The three buckets are exclusive and exhaustive - see campaign_event_mix_sql - so `skipped`
# is zero and this never renders. It is kept as a drift guard: the ring's hole and the
# Events KPI are two counts of the same thing from two queries, and if a future predicate
# lands on one and not the other, the reader is told rather than left to notice. The old
# wording named AI requests as the cause, which stopped being true when the AND NOT
# _AI_REQUEST clause came out of the query.
_MIX_NOTE = (
    "{shown:,} of this campaign's {total:,} events fall into these three buckets; "
    "{skipped:,} do not."
)

_ABSENT = {
    "pages": "No page views carry a resolvable URL. Pages are identified from `SEARCH_URL`, the one URL column populated across the whole history — `PATH` and `TAB_URL` were retired in the March–April 2026 tracking change.",
    "content": "No tracked content. This campaign's traffic carries no `asset` parameter, so there is nothing to attribute reach to. Asset tagging began around March 2026.",
    "depth": "No PDF page-turn events, so read depth cannot be measured for this campaign.",
    "identity": "No identified visitors. Identity comes from the `email` URL parameter, which only rides on personalised links - and campaign links largely do not carry it.",
    "identity_floor": "{n} identified {noun} — too few to break down by company without describing an individual, so only the total is shown.",
    "ai": "No AI activity. Only 19% of campaigns have any: AI events do carry a campaign ID, they are simply concentrated in a minority of campaigns.",
    "sources": "No referrer recorded on any event, so traffic cannot be attributed to a source.",
}

_DURATION_NOTE = "Duration is the span between a session's first and last event, not time spent reading. Median {median} against a mean of {mean} — the same skew the platform shows."
# Module level, like every other caption here, so the branch below stays a single indented line -
# the Snowsight editor re-indents multi-line calls inside indented blocks and breaks them.
_PAGES_NOTE = "{views:,} page views across {pages:,} pages, labelled by path — the host is on hover, and the full address is in the table. Query strings are stripped, so `?asset=` variants of one page rank together, and localhost and iframe pages are left out. One session can visit several pages, so the shares sum past 100%."
# 437 of 592 campaigns in a 30-day window have exactly ONE page (median 1, p90 2), so a bar
# chart is the exception rather than the rule here. A single bar is a rectangle whose length
# is its own maximum - it carries no comparison, which is the only thing a bar chart is for -
# so one page is stated instead, the way the consent figures are.
_ONE_PAGE = "Every session landed on a single page: [{page}]({url}) — {sessions:,} sessions, {views:,} views, {per:.1f} per session."
_PAGE_COLUMNS = {
    # The address is spelled out rather than hidden behind an "Open" label. A LinkColumn with
    # display_text renders the word and keeps the URL underneath, which is fine for clicking and
    # useless for the thing actually wanted here - reading the address off the screen and pasting
    # it somewhere. Shown in full, it is selectable text AND a link.
    "URL": st.column_config.LinkColumn("Address", width="large"),
    "SESSIONS": st.column_config.NumberColumn("Sessions", format="%d"),
    "VIEWS": st.column_config.NumberColumn("Views", format="%d"),
    "VIEWS_PER_SESSION": st.column_config.NumberColumn("Views / session", format="%.1f"),
    "PCT_OF_SESSIONS": st.column_config.NumberColumn("% of sessions", format="%.1f"),
}
# Address first, and neither PAGE nor HOST shown: the URL ends with the path the bars are
# labelled by and begins with the host, so both would be the same string twice. They stay in the
# frame because the chart needs them - the axis label is the path alone, and the tooltip names
# the host that the path omits. Ordering matters as much as inclusion: left in the frame's own
# order the address landed last, off the right edge of the container, so the one column added
# for copying was the one column not on screen.
_PAGE_ORDER = ("URL", "SESSIONS", "VIEWS", "VIEWS_PER_SESSION", "PCT_OF_SESSIONS")
# Module level so the branch below stays one indented line - the Snowsight editor
# re-indents multi-line calls inside indented blocks and breaks them.
_CONCENTRATION_NOTE = "{share:.0f}% of these {sessions:,} sessions came from a single network address, out of {ips:,} addresses in total. Likely one organisation or an automated client rather than {sessions:,} separate visitors — an address is not a person."

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

    # Sessions, Events / Session and the mix donut as three cards on one row. st.metric and
    # st.column both take border=True, so the cards are native Streamlit rather than CSS -
    # which matters here because a hand-rolled card would need its own light and dark
    # surfaces, and this file deliberately owns no colours.
    #
    # Engaged and Converted are deliberately NOT cards. Both were a percentage of SESSIONS,
    # and the Engagement and conversion funnel below states the same two figures as stages,
    # with the counts they are drawn from and nested so the relationship is visible. The
    # figures are not lost - the funnel carries them, on this page and in both downloads.
    #
    # The Events card used to sit beside Sessions. It is the donut now, so the total it
    # carried moves into the hole rather than being lost, labelled so it cannot be mistaken
    # for one of the page's other totals. The three buckets are exhaustive, so the ring sums
    # to the same figure the card showed - see _MIX_NOTE for the guard that says so if it
    # ever stops being true.
    mix = data.get("event_mix")
    has_mix = mix is not None and not mix.empty and float(mix["EVENTS"].sum()) > 0
    # Two cards STACKED in the left column, ring on the right. Writing both metrics into the
    # same column is what stacks them - Streamlit lays a column out vertically - so this needs
    # no nested columns and stays clear of the one-level nesting limit.
    #
    # vertical_alignment="top", not "center": the ring column is the taller of the two, and
    # centring left the stat cards floating in the middle of it instead of starting level
    # with the top of the chart card.
    if has_mix:
        stats_col, chart_col = st.columns([1, 2], gap="medium", vertical_alignment="top")
    else:
        stats_col, chart_col = st.container(), None
    s_card = kpi("Sessions", sessions)
    e_card = kpi("Events / Session", float(k["EVENTS"]) / sessions, decimals=1)
    stats_col.metric(s_card[0], s_card[1], help=s_card[3], border=True)
    stats_col.metric(e_card[0], e_card[1], help=e_card[3], border=True)
    if has_mix:
        shown = int(mix["EVENTS"].sum())
        chart = donut_chart(mix, "BUCKET", "EVENTS", order=EVENT_BUCKET_ORDER, centre_label=f"{shown:,}", centre_sublabel="Total Events")
        skipped = int(k["EVENTS"]) - shown
        with chart_col.container(border=True):
            st.altair_chart(chart, width="content")
            if skipped > 0: st.caption(_MIX_NOTE.format(shown=shown, total=int(k["EVENTS"]), skipped=skipped))
            with st.expander("View as table"): st.dataframe(mix, width="stretch", hide_index=True)

    # Qualifies the Sessions card directly above it, so it sits here rather than in Session
    # quality: the number it bounds is the one the reader has just looked at. Gated in
    # campaign_report.concentration() rather than here, so the page, the PDF and the emailed
    # body cannot disagree about whether a campaign counts as concentrated.
    _conc = concentration(k)
    if _conc is not None: _absent(_CONCENTRATION_NOTE.format(share=_conc, sessions=sessions, ips=int(k["DISTINCT_IPS"])))

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

    # -------------------------------------------------------- pages (conditional)
    # Before Content deliberately, and the pair reads as one idea: the page is the container,
    # the asset is the content opened on it. Reach per page, not entry page - the bars answer
    # "which pages did this campaign put in front of people", the same question Content answers
    # one level down.
    st.markdown("##### Pages")
    pages = data.get("pages")
    if pages is None or pages.empty:
        _absent(_ABSENT["pages"])
    elif len(pages) == 1:
        one = pages.iloc[0]
        st.markdown(_ONE_PAGE.format(page=one["PAGE"], url=one["URL"], sessions=int(one["SESSIONS"]), views=int(one["VIEWS"]), per=float(one["VIEWS_PER_SESSION"])))
    else:
        st.caption(_PAGES_NOTE.format(views=int(k["PAGE_VIEWS"]), pages=int(k["PAGES"])))
        st.altair_chart(page_bar(pages), width="stretch")
        with st.expander("Full addresses and views per session"):
            st.dataframe(pages, width="stretch", hide_index=True, column_config=_PAGE_COLUMNS, column_order=_PAGE_ORDER)

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
