"""Warm every page's queries once, so navigation afterwards hits cache only.

Why this exists, from measurements on this warehouse:

  bare SELECT 1                    ~275ms   (pure round trip, no scanning)
  median dashboard query        550-670ms
  heaviest page (Audience)      7.9-9.5s cold, 12 queries

Client-side concurrency does not help - Snowpark's collect_nowait() was measured at
13.6s against 9.5s sequential for the same 12 queries, because submission still
blocks per statement and the X-Small warehouse is single-cluster, so the queries
queue instead of running side by side. Firing all 43 at once took 48s.

So the fix is to pay the cost once, up front, against a one-hour cache. Warming all
pages takes roughly the same wall-clock as visiting them one at a time, but it
happens while the reader is still on the landing page, and every navigation after
that is instant until the range changes or the cache is refreshed.

QUERIES must mirror what the pages actually call, including non-default limits and
repeated parameter lists. tests assert that it does; a query missing here is not a
bug, only a page that stays slow, but a WRONG parameter list here would warm the
cache under a key no page ever asks for, silently doing nothing.
"""

import datetime as dt

import src.queries as Q
from src.db import run_query


def query_plan(start: dt.date, end: dt.date, prev_start: dt.date, prev_end: dt.date):
    """Every (page, sql, params) the dashboard executes for one date range."""
    p = [start, end]
    v = [prev_start, prev_end]
    return [
        ("Executive", Q.executive_kpis_sql(), p),
        ("Executive", Q.executive_kpis_sql(), v),
        # The Executive Dashboard also charts sessions_over_time_sql, but Session
        # Analytics already warms it with the same parameters and the cache is keyed
        # on (sql, params) - listing it twice would just pay for it twice.
        ("Session Analytics", Q.session_kpis_sql(), p),
        ("Session Analytics", Q.session_kpis_sql(), v),
        ("Session Analytics", Q.sessions_over_time_sql(), p),
        ("Session Analytics", Q.session_duration_bands_sql(), p),
        ("Session Analytics", Q.session_duration_percentiles_sql(), p),
        ("Session Analytics", Q.session_integrity_sql(), p),
        ("Audience", Q.audience_kpis_sql(), p),
        ("Audience", Q.audience_kpis_sql(), v),
        ("Audience", Q.identity_cohort_sql(), p),
        ("Audience", Q.top_accounts_sql(), p),
        ("Audience", Q.audience_geo_sql(), p),
        ("Audience", Q.user_activity_kpis_sql(), p),
        ("Audience", Q.user_activity_kpis_sql(), v),
        ("Audience", Q.visitor_sessions_bands_sql(), p),
        ("Audience", Q.visitor_sessions_percentiles_sql(), p),
        ("Audience", Q.top_visitor_by_sessions_sql(), p),
        ("Campaign Analytics", Q.campaign_kpis_sql(), p),
        ("Campaign Analytics", Q.campaign_kpis_sql(), v),
        ("Campaign Analytics", Q.top_campaigns_sql(), p),
        ("Conversion", Q.funnel_sql(), p),
        ("Conversion", Q.funnel_sql(), v),
        ("Conversion", Q.form_performance_sql(), p),
        ("Conversion", Q.consent_split_kpis_sql(), p),
        ("Conversion", Q.action_reach_sql(), p + p),
        ("Pages & Sources", Q.page_coverage_sql(), p),
        ("Pages & Sources", Q.page_coverage_sql(), v),
        ("Pages & Sources", Q.traffic_sources_sql(), p),
        ("Pages & Sources", Q.top_pages_sql(), p),
        ("Pages & Sources", Q.top_external_referrers_sql(), p),
        ("Content Performance", Q.asset_kpis_sql(), p),
        ("Content Performance", Q.asset_kpis_sql(), v),
        ("Content Performance", Q.asset_cohort_sql(), p),
        ("Content Performance", Q.top_assets_sql(), p),
        ("Content Performance", Q.asset_read_depth_sql(), p),
        ("AI Usage", Q.ai_kpis_sql(), p),
        ("AI Usage", Q.ai_kpis_sql(), v),
        ("AI Usage", Q.model_mix_sql(), p),
        ("AI Usage", Q.model_trend_sql(), p),
        ("AI Usage", Q.ai_cohort_sql(), p),
        ("Tenant Breakdown", Q.tenant_kpis_sql(), p),
        ("Tenant Breakdown", Q.tenant_kpis_sql(), v),
        ("Tenant Breakdown", Q.tenant_spread_sql(), p),
        ("Tenant Breakdown", Q.tenant_comparison_sql(), p + p),
        ("Tenant Breakdown", Q.tenant_movement_sql(), p + v),
    ]


def warm(start, end, prev_start, prev_end, on_progress=None) -> tuple[int, int]:
    """Run every query once. Returns (succeeded, total).

    A failure is swallowed deliberately: warming is an optimisation, and one broken
    query must not stop the other 50 from being cached or block the landing page.
    The page that owns the failing query will surface the error properly when the
    reader opens it.
    """
    plan = query_plan(start, end, prev_start, prev_end)
    ok = 0
    for i, (page, sql, params) in enumerate(plan, start=1):
        try:
            run_query(sql, params)
            ok += 1
        except Exception:
            pass
        if on_progress is not None:
            on_progress(i, len(plan), page)
    return ok, len(plan)
