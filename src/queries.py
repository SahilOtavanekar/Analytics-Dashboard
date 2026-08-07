"""All SQL for the dashboard, one function per query.

Two conventions worth knowing before editing:

**Event counts use COUNT(DISTINCT MESSAGE_ID), never COUNT(*).** The source table
contains ~1.29M exactly-duplicated rows (5.9% of the table, 2.4% of the last 30
days) - same MESSAGE_ID, same timestamp to the millisecond, same session, same
path. Verified by comparing distinct MESSAGE_IDs against distinct full-row hashes
and by inspecting duplicate groups: 1,037,068 groups, of which 0 vary in
SESSION_ID or PATH. MESSAGE_ID has no nulls anywhere, so deduplicating on it is
exact. COUNT(*) overstates every event metric.

**NULL guards on SESSION_ID and REQUEST_IP.** SQL collapses all NULLs into one
GROUP BY bucket, so without a guard the ~3.47M session-less rows become a single
pseudo-session spanning the range. Those rows are historical: SESSION_ID was not
captured at all before Nov 2025 (Aug-Oct 2025 is ~100% null) and is fully
populated from May 2026 onward. The default 30-day window is unaffected; a range
reaching into 2025 is not.
"""

from src.db import table_fqn

# A page view is EVENT_TYPE = 'page' OR a name of "page visit". Both halves matter:
# the ~3.4M unnamed events are all type 'page', so type catches them; and 107 events
# arrive as linkedin_track/google_track while being named "page visit", so the name
# check catches those. COALESCE because NOT(FALSE OR NULL) is NULL, not TRUE - a
# track event with no name would otherwise fall out of both sides of the split.
# "pdf-page-visit" is deliberately NOT a page view; it's a tracked action.
_PAGE_VIEW = "(COALESCE(EVENT_TYPE, '') = 'page' OR COALESCE(LOWER(EVENT_NAME), '') = 'page visit')"

# Development artefacts sitting in production data.
_NOT_TEST = "(EVENT_NAME IS NULL OR LOWER(EVENT_NAME) NOT IN ('test event', 'test download event'))"

# Event names arrive inconsistently cased and punctuated ("clicked"/"Clicked",
# "form_submit"/"Form Submit"), so every name comparison goes through this.
_NORM = "LOWER(REPLACE(EVENT_NAME, '_', ' '))"

# Page identity comes from SEARCH_URL, not PATH. The tracking implementation was
# swapped around Mar-Apr 2026: PATH and TAB_URL fell from 100% populated to 0%,
# while REFERER_URL and QUERY_PARAMETERS rose from 0% to 100%. SEARCH_URL is the
# one URL column populated across the whole history, and despite its name it holds
# the current page URL. Query strings are stripped so "?asset=..." variants of the
# same page don't rank as separate rows.
_PAGE_URL = (
    "COALESCE(PARSE_URL(SEARCH_URL, 1):host::STRING, '') || '/' || "
    "COALESCE(PARSE_URL(SEARCH_URL, 1):path::STRING, '')"
)

_REF_HOST = "PARSE_URL(REFERER_URL, 1):host::STRING"

# 81% of referrers are an empty string (direct), and most of the remainder is our
# own infrastructure or internal tooling - Atlassian, an S3 microsites bucket,
# localhost, Amplify preview URLs. Ranking raw referrer hosts would present
# internal traffic as acquisition, so sources are bucketed first. Edit these lists
# if the estate changes; everything unmatched counts as genuinely External.
_SOURCE_GROUP = f"""
        CASE
            WHEN COALESCE(REFERER_URL, '') = '' THEN 'Direct / none'
            WHEN {_REF_HOST} IN ('localhost', '127.0.0.1')
              OR {_REF_HOST} ILIKE '%amplifye.ai'
              OR {_REF_HOST} ILIKE '%demandai.net'
              OR {_REF_HOST} ILIKE '%atlassian.net'
              OR {_REF_HOST} ILIKE '%amplifyapp.com'
              OR {_REF_HOST} ILIKE '%amazonaws.com'
              OR {_REF_HOST} ILIKE '%cloudfront.net' THEN 'Internal / dev'
            WHEN {_REF_HOST} ILIKE '%officeapps.live.com'
              OR {_REF_HOST} ILIKE '%sharepoint.com'
              OR {_REF_HOST} ILIKE '%office.net'
              OR {_REF_HOST} ILIKE '%microsoft.com' THEN 'Email / Office'
            ELSE 'External'
        END"""


def top_pages_sql(limit: int = 15) -> str:
    """Most-viewed pages, labelled by PROPERTIES:title where one exists.

    Grouped on the canonical URL rather than the title, because titles drift while
    the URL is stable - the same pattern top_campaigns_sql uses for campaign names.
    """
    return f"""
        SELECT
            COALESCE(MODE(PROPERTIES:title::STRING), {_PAGE_URL}) AS PAGE,
            COUNT(DISTINCT MESSAGE_ID) AS EVENT_COUNT
        FROM {table_fqn()}
        WHERE EVENT_TS::DATE BETWEEN ? AND ?
          AND {_PAGE_VIEW}
          AND SEARCH_URL IS NOT NULL
        GROUP BY {_PAGE_URL}
        ORDER BY EVENT_COUNT DESC
        LIMIT {limit}
    """


def page_coverage_sql() -> str:
    return f"""
        SELECT
            COUNT(DISTINCT {_PAGE_URL}) AS DISTINCT_PAGES,
            COUNT(DISTINCT MESSAGE_ID) AS PAGE_VIEWS
        FROM {table_fqn()}
        WHERE EVENT_TS::DATE BETWEEN ? AND ?
          AND {_PAGE_VIEW}
          AND SEARCH_URL IS NOT NULL
    """


def traffic_sources_sql() -> str:
    return f"""
        SELECT {_SOURCE_GROUP} AS SOURCE_GROUP, COUNT(DISTINCT MESSAGE_ID) AS EVENT_COUNT
        FROM {table_fqn()}
        WHERE EVENT_TS::DATE BETWEEN ? AND ?
        GROUP BY 1
        ORDER BY EVENT_COUNT DESC
    """


def top_external_referrers_sql(limit: int = 12) -> str:
    return f"""
        SELECT {_REF_HOST} AS REFERRER, COUNT(DISTINCT MESSAGE_ID) AS EVENT_COUNT
        FROM {table_fqn()}
        WHERE EVENT_TS::DATE BETWEEN ? AND ?
          AND {_SOURCE_GROUP} = 'External'
        GROUP BY 1
        ORDER BY EVENT_COUNT DESC
        LIMIT {limit}
    """


def executive_kpis_sql() -> str:
    return f"""
        SELECT
            COUNT(DISTINCT MESSAGE_ID) AS total_events,
            COUNT(DISTINCT SESSION_ID) AS total_sessions,
            COUNT(DISTINCT CAMPAIGN_ID) AS total_campaigns
        FROM {table_fqn()}
        WHERE EVENT_TS::DATE BETWEEN ? AND ?
    """


def top_events_sql(limit: int = 10) -> str:
    # EVENT_NAME arrives inconsistently cased and punctuated: "clicked"/"Clicked"
    # and "form_submit"/"Form Submit" are the same action but ranked as separate
    # bars, understating both. Normalise for grouping, title-case for display.
    # Nulls are a real category (page views carrying no name), so they get a label
    # rather than being silently dropped or shown as blank.
    return f"""
        SELECT
            IFF(EVENT_NAME IS NULL, '(unnamed page view)',
                INITCAP(LOWER(REPLACE(EVENT_NAME, '_', ' ')))) AS EVENT_NAME,
            COUNT(DISTINCT MESSAGE_ID) AS EVENT_COUNT
        FROM {table_fqn()}
        WHERE EVENT_TS::DATE BETWEEN ? AND ?
          AND {_NOT_TEST}
        GROUP BY 1
        ORDER BY EVENT_COUNT DESC
        LIMIT {limit}
    """


def event_type_share_sql() -> str:
    # Superseded on the Event Analytics page by event_mix_sql(), which labels the
    # split in business terms. Kept because the raw type breakdown is still useful.
    return f"""
        SELECT EVENT_TYPE, COUNT(DISTINCT MESSAGE_ID) AS EVENT_COUNT
        FROM {table_fqn()}
        WHERE EVENT_TS::DATE BETWEEN ? AND ?
        GROUP BY EVENT_TYPE
        ORDER BY EVENT_COUNT DESC
    """


def event_split_kpis_sql() -> str:
    """Page views vs tracked actions - the volume/intent split."""
    return f"""
        SELECT
            COUNT(DISTINCT IFF({_PAGE_VIEW}, MESSAGE_ID, NULL)) AS PAGE_VIEWS,
            COUNT(DISTINCT IFF(NOT {_PAGE_VIEW}, MESSAGE_ID, NULL)) AS TRACKED_ACTIONS
        FROM {table_fqn()}
        WHERE EVENT_TS::DATE BETWEEN ? AND ?
          AND {_NOT_TEST}
    """


def tracked_actions_sql(limit: int = 15) -> str:
    """Intent signals only. Page views outnumber these ~20:1 and bury them when ranked together."""
    return f"""
        SELECT
            INITCAP(LOWER(REPLACE(EVENT_NAME, '_', ' '))) AS EVENT_NAME,
            COUNT(DISTINCT MESSAGE_ID) AS EVENT_COUNT
        FROM {table_fqn()}
        WHERE EVENT_TS::DATE BETWEEN ? AND ?
          AND NOT {_PAGE_VIEW}
          AND {_NOT_TEST}
          AND EVENT_NAME IS NOT NULL
        GROUP BY 1
        ORDER BY EVENT_COUNT DESC
        LIMIT {limit}
    """


def event_mix_sql() -> str:
    return f"""
        SELECT
            IFF({_PAGE_VIEW}, 'Page views', 'Tracked actions') AS EVENT_GROUP,
            COUNT(DISTINCT MESSAGE_ID) AS EVENT_COUNT
        FROM {table_fqn()}
        WHERE EVENT_TS::DATE BETWEEN ? AND ?
          AND {_NOT_TEST}
        GROUP BY 1
        ORDER BY EVENT_COUNT DESC
    """


def funnel_sql() -> str:
    """Three stages that genuinely nest, measured per session.

    The intuitive ordering - page visit -> pdf -> click -> form submit - is NOT a
    funnel, and the data says so plainly: in a 30-day window 9,001 sessions clicked
    against only 2,269 that opened a PDF, 7,184 clicking sessions never touched a
    PDF at all, and 413 form submissions involved no click. Presenting those as
    sequential stages would invent a journey nobody takes.

    Visited -> took any action -> submitted a form does nest, by construction: a
    form submit is an action, and an action requires a session.
    """
    return f"""
        WITH sess AS (
            SELECT
                SESSION_ID,
                MAX(IFF(NOT {_PAGE_VIEW}, 1, 0)) AS DID_ACT,
                MAX(IFF({_NORM} = 'form submit', 1, 0)) AS DID_CONVERT
            FROM {table_fqn()}
            WHERE EVENT_TS::DATE BETWEEN ? AND ?
              AND SESSION_ID IS NOT NULL
              AND {_NOT_TEST}
            GROUP BY SESSION_ID
        )
        SELECT 1 AS STAGE_ORDER, 'Visited' AS STAGE, COUNT(*) AS SESSIONS FROM sess
        UNION ALL
        SELECT 2 AS STAGE_ORDER, 'Took an action' AS STAGE, COALESCE(SUM(DID_ACT), 0) FROM sess
        UNION ALL
        SELECT 3 AS STAGE_ORDER, 'Submitted a form' AS STAGE, COALESCE(SUM(DID_CONVERT), 0) FROM sess
        ORDER BY STAGE_ORDER
    """


def action_reach_sql() -> str:
    """Share of sessions performing each action. Overlapping, deliberately not a funnel."""
    return f"""
        WITH all_sessions AS (
            SELECT SESSION_ID
            FROM {table_fqn()}
            WHERE EVENT_TS::DATE BETWEEN ? AND ?
              AND SESSION_ID IS NOT NULL
            GROUP BY SESSION_ID
        ), acted AS (
            SELECT DISTINCT SESSION_ID, INITCAP({_NORM}) AS ACTION
            FROM {table_fqn()}
            WHERE EVENT_TS::DATE BETWEEN ? AND ?
              AND SESSION_ID IS NOT NULL
              AND EVENT_NAME IS NOT NULL
              AND NOT {_PAGE_VIEW}
              AND {_NOT_TEST}
        )
        SELECT
            ACTION,
            COUNT(DISTINCT SESSION_ID) AS SESSIONS,
            COUNT(DISTINCT SESSION_ID) * 100.0
                / NULLIF((SELECT COUNT(*) FROM all_sessions), 0) AS PCT_OF_SESSIONS
        FROM acted
        GROUP BY ACTION
        ORDER BY SESSIONS DESC
    """


def _session_summary_cte() -> str:
    return f"""
        SELECT
            SESSION_ID,
            COUNT(DISTINCT MESSAGE_ID) AS EVENT_COUNT,
            DATEDIFF('second', MIN(EVENT_TS), MAX(EVENT_TS)) AS DURATION_SECONDS
        FROM {table_fqn()}
        WHERE EVENT_TS::DATE BETWEEN ? AND ?
          AND SESSION_ID IS NOT NULL
        GROUP BY SESSION_ID
    """


def session_kpis_sql() -> str:
    """Medians, not means. COUNT(*) counts session groups, not events - correct as written.

    Session duration is extreme enough that the mean is an artifact rather than a
    summary: half of all sessions have zero duration, the median is one second, and
    the mean is 23 minutes because a handful of sessions span weeks. Reporting the
    mean as "Avg Session Duration" overstated the typical session ~1,400x. Means are
    still returned, for the hover detail.
    """
    return f"""
        SELECT
            COUNT(*) AS TOTAL_SESSIONS,
            COALESCE((APPROX_PERCENTILE(EVENT_COUNT, 0.5))::FLOAT, 0) AS MEDIAN_EVENTS,
            COALESCE((AVG(EVENT_COUNT))::FLOAT, 0) AS MEAN_EVENTS,
            COALESCE((APPROX_PERCENTILE(DURATION_SECONDS, 0.5) / 60.0)::FLOAT, 0) AS MEDIAN_DURATION_MINUTES,
            COALESCE((AVG(DURATION_SECONDS) / 60.0)::FLOAT, 0) AS MEAN_DURATION_MINUTES,
            COALESCE(
                (SUM(IFF(DURATION_SECONDS = 0, 1, 0)) * 100.0 / NULLIF(COUNT(*), 0))::FLOAT, 0
            ) AS PCT_INSTANT
        FROM ({_session_summary_cte()})
    """


def session_duration_bands_sql() -> str:
    """Duration in interpretable bands.

    A linear histogram cannot render this distribution: 49.8% of sessions sit at
    exactly zero and the tail reaches 60 days, so one bar holds almost everything
    and the p95 clip doesn't rescue it. Fixed bands are readable and each one means
    something on its own. Returns 7 rows instead of 100k+, so it also stops shipping
    the whole session table to the client.
    """
    return f"""
        SELECT
            CASE
                WHEN DURATION_SECONDS = 0 THEN '0s (instant)'
                WHEN DURATION_SECONDS <= 10 THEN '1-10 seconds'
                WHEN DURATION_SECONDS <= 60 THEN '10-60 seconds'
                WHEN DURATION_SECONDS <= 300 THEN '1-5 minutes'
                WHEN DURATION_SECONDS <= 1800 THEN '5-30 minutes'
                WHEN DURATION_SECONDS <= 7200 THEN '30 min - 2 hours'
                ELSE 'over 2 hours'
            END AS BAND,
            CASE
                WHEN DURATION_SECONDS = 0 THEN 1
                WHEN DURATION_SECONDS <= 10 THEN 2
                WHEN DURATION_SECONDS <= 60 THEN 3
                WHEN DURATION_SECONDS <= 300 THEN 4
                WHEN DURATION_SECONDS <= 1800 THEN 5
                WHEN DURATION_SECONDS <= 7200 THEN 6
                ELSE 7
            END AS BAND_ORDER,
            COUNT(*) AS SESSIONS
        FROM ({_session_summary_cte()})
        GROUP BY BAND, BAND_ORDER
        ORDER BY BAND_ORDER
    """


def session_duration_percentiles_sql() -> str:
    """Percentiles and degenerate-case counts - what .describe() should have shown."""
    return f"""
        SELECT
            COUNT(*) AS SESSIONS,
            ROUND(APPROX_PERCENTILE(DURATION_SECONDS, 0.50) / 60.0, 3) AS P50_MINUTES,
            ROUND(APPROX_PERCENTILE(DURATION_SECONDS, 0.75) / 60.0, 3) AS P75_MINUTES,
            ROUND(APPROX_PERCENTILE(DURATION_SECONDS, 0.90) / 60.0, 2) AS P90_MINUTES,
            ROUND(APPROX_PERCENTILE(DURATION_SECONDS, 0.95) / 60.0, 2) AS P95_MINUTES,
            ROUND(APPROX_PERCENTILE(DURATION_SECONDS, 0.99) / 60.0, 2) AS P99_MINUTES,
            ROUND(AVG(DURATION_SECONDS) / 60.0, 2) AS MEAN_MINUTES,
            ROUND(MAX(DURATION_SECONDS) / 86400.0, 2) AS MAX_DAYS,
            SUM(IFF(DURATION_SECONDS = 0, 1, 0)) AS INSTANT_SESSIONS,
            SUM(IFF(DURATION_SECONDS > 7200, 1, 0)) AS OVER_2_HOURS,
            SUM(IFF(DURATION_SECONDS > 86400, 1, 0)) AS OVER_24_HOURS
        FROM ({_session_summary_cte()})
    """


def session_integrity_sql() -> str:
    """How far SESSION_ID can be trusted to mean "one visit".

    Measured, not assumed: the worst session in a recent 60-day window carried 45
    distinct IPs across 36 active days. Session IDs are reused across unrelated
    visitors, so any per-session metric is a blend for those rows.
    """
    return f"""
        WITH s AS (
            SELECT
                SESSION_ID,
                COUNT(DISTINCT REQUEST_IP) AS IPS,
                DATEDIFF('second', MIN(EVENT_TS), MAX(EVENT_TS)) AS DURATION_SECONDS
            FROM {table_fqn()}
            WHERE EVENT_TS::DATE BETWEEN ? AND ?
              AND SESSION_ID IS NOT NULL
            GROUP BY SESSION_ID
        )
        SELECT
            COUNT(*) AS SESSIONS,
            SUM(IFF(IPS > 1, 1, 0)) AS MULTI_IP_SESSIONS,
            MAX(IPS) AS MAX_IPS_ON_ONE_SESSION,
            SUM(IFF(DURATION_SECONDS > 86400, 1, 0)) AS OVER_24_HOURS
        FROM s
    """


def sessions_over_time_sql() -> str:
    return f"""
        SELECT
            EVENT_TS::DATE AS EVENT_DATE,
            COUNT(DISTINCT SESSION_ID) AS SESSION_COUNT
        FROM {table_fqn()}
        WHERE EVENT_TS::DATE BETWEEN ? AND ?
        GROUP BY EVENT_DATE
        ORDER BY EVENT_DATE
    """


def _visitor_summary_cte() -> str:
    # No persistent user/visitor ID exists in this data; REQUEST_IP is the closest
    # proxy for "who", acknowledging it can be shared (NAT, VPN, bots).
    return f"""
        SELECT
            REQUEST_IP,
            COUNT(DISTINCT SESSION_ID) AS SESSION_COUNT,
            COUNT(DISTINCT EVENT_TS::DATE) AS ACTIVE_DAYS
        FROM {table_fqn()}
        WHERE EVENT_TS::DATE BETWEEN ? AND ?
          AND REQUEST_IP IS NOT NULL
        GROUP BY REQUEST_IP
    """


def user_activity_kpis_sql() -> str:
    """Median first. COUNT(*) counts visitor groups, not events - correct as written.

    Sessions per visitor is as skewed as session duration: 59.7% of IPs have exactly
    one session, the median is 1, and the mean is 9.2 because one IP carries 4,053.
    The mean overstated the typical visitor ~9x.
    """
    return f"""
        SELECT
            COUNT(*) AS TOTAL_VISITORS,
            COALESCE(
                (SUM(CASE WHEN ACTIVE_DAYS > 1 THEN 1 ELSE 0 END) * 100.0 / NULLIF(COUNT(*), 0))::FLOAT, 0
            ) AS PCT_RETURNING,
            COALESCE((APPROX_PERCENTILE(SESSION_COUNT, 0.5))::FLOAT, 0) AS MEDIAN_SESSIONS_PER_VISITOR,
            COALESCE((AVG(SESSION_COUNT))::FLOAT, 0) AS MEAN_SESSIONS_PER_VISITOR,
            COALESCE(
                (SUM(CASE WHEN SESSION_COUNT = 1 THEN 1 ELSE 0 END) * 100.0 / NULLIF(COUNT(*), 0))::FLOAT, 0
            ) AS PCT_SINGLE_SESSION
        FROM ({_visitor_summary_cte()})
    """


def visitor_sessions_bands_sql() -> str:
    """Sessions per visitor in bands, so every visitor is represented.

    The histogram this replaces clipped its bin extent to the 95th percentile, which
    silently dropped ~5% of visitors from the drawing - including the single busiest
    one, which is the row a reader most wants to see. Bands put the outliers in an
    explicit "over 100" bucket instead, and return 7 rows rather than several
    thousand.
    """
    return f"""
        SELECT
            CASE
                WHEN SESSION_COUNT = 1 THEN '1 session'
                WHEN SESSION_COUNT <= 5 THEN '2-5 sessions'
                WHEN SESSION_COUNT <= 10 THEN '6-10 sessions'
                WHEN SESSION_COUNT <= 25 THEN '11-25 sessions'
                WHEN SESSION_COUNT <= 50 THEN '26-50 sessions'
                WHEN SESSION_COUNT <= 100 THEN '51-100 sessions'
                ELSE 'over 100 sessions'
            END AS BAND,
            CASE
                WHEN SESSION_COUNT = 1 THEN 1
                WHEN SESSION_COUNT <= 5 THEN 2
                WHEN SESSION_COUNT <= 10 THEN 3
                WHEN SESSION_COUNT <= 25 THEN 4
                WHEN SESSION_COUNT <= 50 THEN 5
                WHEN SESSION_COUNT <= 100 THEN 6
                ELSE 7
            END AS BAND_ORDER,
            COUNT(*) AS VISITORS
        FROM ({_visitor_summary_cte()})
        GROUP BY BAND, BAND_ORDER
        ORDER BY BAND_ORDER
    """


def visitor_sessions_percentiles_sql() -> str:
    return f"""
        SELECT
            COUNT(*) AS VISITORS,
            APPROX_PERCENTILE(SESSION_COUNT, 0.50) AS P50_SESSIONS,
            APPROX_PERCENTILE(SESSION_COUNT, 0.75) AS P75_SESSIONS,
            APPROX_PERCENTILE(SESSION_COUNT, 0.90) AS P90_SESSIONS,
            APPROX_PERCENTILE(SESSION_COUNT, 0.95) AS P95_SESSIONS,
            APPROX_PERCENTILE(SESSION_COUNT, 0.99) AS P99_SESSIONS,
            ROUND(AVG(SESSION_COUNT), 2) AS MEAN_SESSIONS,
            MAX(SESSION_COUNT) AS MAX_SESSIONS,
            SUM(IFF(SESSION_COUNT = 1, 1, 0)) AS SINGLE_SESSION_VISITORS,
            SUM(IFF(SESSION_COUNT > 100, 1, 0)) AS OVER_100_SESSIONS
        FROM ({_visitor_summary_cte()})
    """


def top_visitor_by_sessions_sql() -> str:
    return f"""
        SELECT REQUEST_IP, SESSION_COUNT
        FROM ({_visitor_summary_cte()})
        ORDER BY SESSION_COUNT DESC
        LIMIT 1
    """


def campaign_kpis_sql() -> str:
    return f"""
        SELECT
            COUNT(DISTINCT CAMPAIGN_ID) AS TOTAL_CAMPAIGNS,
            COUNT(DISTINCT SESSION_ID) AS TOTAL_SESSIONS,
            -- Numerator counts only events that actually carry a campaign: 0.53% of
            -- rows have none, and including them while excluding them from the
            -- distinct denominator overstates the average.
            COALESCE(
                (COUNT(DISTINCT IFF(CAMPAIGN_ID IS NOT NULL, MESSAGE_ID, NULL))
                 / NULLIF(COUNT(DISTINCT CAMPAIGN_ID), 0))::FLOAT, 0
            ) AS AVG_EVENTS_PER_CAMPAIGN
        FROM {table_fqn()}
        WHERE EVENT_TS::DATE BETWEEN ? AND ?
    """


def top_campaigns_sql(limit: int = 10) -> str:
    # CAMPAIGN_ID is the stable key, but it's often an opaque UUID; PROPERTIES:campaign_name
    # is only ~62% populated and occasionally drifts (renames/whitespace), so MODE() picks
    # the most common label per campaign and we fall back to the raw ID when no name exists.
    return f"""
        SELECT
            COALESCE(MODE(PROPERTIES:campaign_name::STRING), CAMPAIGN_ID) AS CAMPAIGN_LABEL,
            COUNT(DISTINCT MESSAGE_ID) AS EVENT_COUNT
        FROM {table_fqn()}
        WHERE EVENT_TS::DATE BETWEEN ? AND ?
        GROUP BY CAMPAIGN_ID
        ORDER BY EVENT_COUNT DESC
        LIMIT {limit}
    """


def channel_share_sql() -> str:
    # CHANNEL holds device type (desktop / web / mobile), not an acquisition channel.
    return f"""
        SELECT CHANNEL, COUNT(DISTINCT MESSAGE_ID) AS EVENT_COUNT
        FROM {table_fqn()}
        WHERE EVENT_TS::DATE BETWEEN ? AND ?
        GROUP BY CHANNEL
        ORDER BY EVENT_COUNT DESC
    """
