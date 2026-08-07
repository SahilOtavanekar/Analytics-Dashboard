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

# The cookie-consent banner submits through the SAME form_submit event as real lead
# forms, and it dominates them. In a 30-day window: 4,542 of 6,178 form submits
# carry form_id = 'cookie-form' against 660 for the next largest form, and 2,154 of
# the 2,400 "converting" sessions did nothing but accept cookies. Counting consent
# as conversion reported 6.27% where the lead-conversion rate is 1.11%.
#
# Consent is still a real event and still appears in Action Reach, labelled as
# itself. It is only excluded from the conversion definition.
_COOKIE_FORM = "COALESCE(PROPERTIES:form_id::STRING, '') = 'cookie-form'"
_LEAD_SUBMIT = f"({_NORM} = 'form submit' AND NOT {_COOKIE_FORM})"

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

    The final stage counts LEAD form submits only - see _LEAD_SUBMIT. Including the
    cookie-consent banner, which fires the same event, put this stage 5.6x too high.
    """
    return f"""
        WITH sess AS (
            SELECT
                SESSION_ID,
                MAX(IFF(NOT {_PAGE_VIEW}, 1, 0)) AS DID_ACT,
                MAX(IFF({_LEAD_SUBMIT}, 1, 0)) AS DID_CONVERT
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
        SELECT 3 AS STAGE_ORDER, 'Submitted a lead form' AS STAGE, COALESCE(SUM(DID_CONVERT), 0) FROM sess
        ORDER BY STAGE_ORDER
    """


def action_reach_sql() -> str:
    """Share of sessions performing each action. Overlapping, deliberately not a funnel.

    Cookie consent is split out from Form Submit rather than dropped. Both fire the
    same event name, so merging them showed one "Form Submit" bar that was 87%
    consent clicks - the single most misleading bar on the dashboard.
    """
    return f"""
        WITH all_sessions AS (
            SELECT SESSION_ID
            FROM {table_fqn()}
            WHERE EVENT_TS::DATE BETWEEN ? AND ?
              AND SESSION_ID IS NOT NULL
            GROUP BY SESSION_ID
        ), acted AS (
            SELECT DISTINCT SESSION_ID,
                   IFF({_COOKIE_FORM}, 'Cookie Consent', INITCAP({_NORM})) AS ACTION
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


def form_performance_sql() -> str:
    """Every form by submitting sessions, consent banner labelled as such.

    form_id sits on 0.4% of all rows, which is not a coverage gap - it is only
    emitted on form events, and 6,176 of 6,178 form submits carry one.
    """
    return f"""
        SELECT
            COALESCE(PROPERTIES:form_id::STRING, '(unidentified)') AS FORM_ID,
            IFF({_COOKIE_FORM}, 'Consent banner', 'Lead form') AS FORM_KIND,
            COUNT(DISTINCT MESSAGE_ID) AS SUBMITS,
            COUNT(DISTINCT SESSION_ID) AS SESSIONS
        FROM {table_fqn()}
        WHERE EVENT_TS::DATE BETWEEN ? AND ?
          AND {_NORM} = 'form submit'
          AND {_NOT_TEST}
        GROUP BY 1, 2
        ORDER BY SUBMITS DESC
    """


def consent_split_kpis_sql() -> str:
    """Lead submits vs consent clicks, so the size of the correction stays visible."""
    return f"""
        SELECT
            COUNT(DISTINCT IFF({_LEAD_SUBMIT}, SESSION_ID, NULL)) AS LEAD_SESSIONS,
            COUNT(DISTINCT IFF({_COOKIE_FORM}, SESSION_ID, NULL)) AS CONSENT_SESSIONS,
            COUNT(DISTINCT IFF({_LEAD_SUBMIT}, MESSAGE_ID, NULL)) AS LEAD_SUBMITS,
            COUNT(DISTINCT IFF({_COOKIE_FORM}, MESSAGE_ID, NULL)) AS CONSENT_SUBMITS
        FROM {table_fqn()}
        WHERE EVENT_TS::DATE BETWEEN ? AND ?
          AND {_NORM} = 'form submit'
          AND {_NOT_TEST}
    """


# Content identity comes from QUERY_PARAMETERS:asset, not PROPERTIES:asset_id. The
# query param carries 751,262 events across 1,120 assets; asset_id carries 43,388
# across 772, because it is only emitted on pdf-page-visit. The slugs are clean -
# zero URL-encoded, zero HTML-entity and zero blank values in a 30-day window - but
# 14% run past 80 characters, so display goes through _ASSET_LABEL.
_ASSET = "QUERY_PARAMETERS:asset::STRING"

# Slugs are hyphenated filenames ("ai-agent-trends-2026.pdf"). Strip the extension,
# unhyphenate and title-case for display; grouping still happens on the raw slug so
# two assets can never be merged by their labels.
# The alternation is spelled 'htm|html' rather than 'html?' on purpose: a literal
# '?' inside a statement bound with qmark parameters is asking for trouble, even
# though Snowflake does currently parse it correctly inside a string literal.
_ASSET_LABEL = f"""
        INITCAP(REPLACE(REGEXP_REPLACE({_ASSET}, '\\\\.(pdf|htm|html)$', '', 1, 0, 'i'), '-', ' '))"""


def asset_kpis_sql() -> str:
    """Reach of tracked content. Sessions are the denominator, not events."""
    return f"""
        SELECT
            COUNT(DISTINCT {_ASSET}) AS TOTAL_ASSETS,
            COUNT(DISTINCT IFF({_ASSET} IS NOT NULL, SESSION_ID, NULL)) AS ASSET_SESSIONS,
            COUNT(DISTINCT SESSION_ID) AS TOTAL_SESSIONS,
            COUNT(DISTINCT IFF({_ASSET} IS NOT NULL, MESSAGE_ID, NULL)) AS ASSET_EVENTS
        FROM {table_fqn()}
        WHERE EVENT_TS::DATE BETWEEN ? AND ?
          AND {_NOT_TEST}
    """


def top_assets_sql(limit: int = 15) -> str:
    """Assets by reach, with depth alongside.

    EVENTS_PER_SESSION separates "many people glanced" from "few people read it",
    which raw reach hides: the top asset by sessions draws 20 events per session
    while the third draws 60.
    """
    return f"""
        SELECT
            {_ASSET_LABEL} AS ASSET,
            COUNT(DISTINCT SESSION_ID) AS SESSIONS,
            COUNT(DISTINCT MESSAGE_ID) AS EVENTS,
            ROUND(COUNT(DISTINCT MESSAGE_ID) * 1.0
                  / NULLIF(COUNT(DISTINCT SESSION_ID), 0), 1) AS EVENTS_PER_SESSION
        FROM {table_fqn()}
        WHERE EVENT_TS::DATE BETWEEN ? AND ?
          AND {_ASSET} IS NOT NULL
          AND SESSION_ID IS NOT NULL
          AND {_NOT_TEST}
        GROUP BY {_ASSET}
        ORDER BY SESSIONS DESC
        LIMIT {limit}
    """


def asset_cohort_sql() -> str:
    """Sessions that saw an asset vs those that didn't.

    The headline finding on this page, and it runs against intuition: asset sessions
    are ~5x more engaged (42.1% vs 8.1%) yet convert at roughly half the rate (0.79%
    vs 1.40%). Both cohorts are large - 17,779 and 20,538 sessions - so neither rate
    is a small-sample artifact.
    """
    return f"""
        WITH sess AS (
            SELECT
                SESSION_ID,
                MAX(IFF({_ASSET} IS NOT NULL, 1, 0)) AS SAW_ASSET,
                MAX(IFF(NOT {_PAGE_VIEW}, 1, 0)) AS ACTED,
                MAX(IFF({_LEAD_SUBMIT}, 1, 0)) AS CONVERTED,
                COUNT(DISTINCT MESSAGE_ID) AS EVENT_COUNT
            FROM {table_fqn()}
            WHERE EVENT_TS::DATE BETWEEN ? AND ?
              AND SESSION_ID IS NOT NULL
              AND {_NOT_TEST}
            GROUP BY SESSION_ID
        )
        SELECT
            IFF(SAW_ASSET = 1, 'Saw an asset', 'No asset') AS COHORT,
            COUNT(*) AS SESSIONS,
            COALESCE((AVG(ACTED) * 100)::FLOAT, 0) AS ENGAGEMENT_PCT,
            COALESCE((AVG(CONVERTED) * 100)::FLOAT, 0) AS LEAD_CONV_PCT,
            COALESCE((APPROX_PERCENTILE(EVENT_COUNT, 0.5))::FLOAT, 0) AS MEDIAN_EVENTS
        FROM sess
        GROUP BY SAW_ASSET
        ORDER BY SESSIONS DESC
    """


def asset_read_depth_sql(limit: int = 12) -> str:
    """How far into a document people actually get.

    PROPERTIES:page_visited is emitted only on pdf-page-visit and runs 1-118, so it
    is a genuine page number rather than a flag. Restricted to rows that also carry
    the asset query param (61.6% of pdf events); the remainder identify the document
    only by UUID, which would put raw GUIDs on the axis next to readable titles.
    """
    return f"""
        SELECT
            {_ASSET_LABEL} AS ASSET,
            COUNT(DISTINCT SESSION_ID) AS SESSIONS,
            ROUND(AVG(TRY_TO_NUMBER(PROPERTIES:page_visited::STRING)), 1) AS AVG_PAGE_REACHED,
            MAX(TRY_TO_NUMBER(PROPERTIES:page_visited::STRING)) AS DEEPEST_PAGE
        FROM {table_fqn()}
        WHERE EVENT_TS::DATE BETWEEN ? AND ?
          AND PROPERTIES:page_visited IS NOT NULL
          AND {_ASSET} IS NOT NULL
          AND SESSION_ID IS NOT NULL
        GROUP BY {_ASSET}
        ORDER BY SESSIONS DESC
        LIMIT {limit}
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


# --------------------------------------------------------------------------- audience
#
# The email query parameter is NOT reliably an email. Of 507,886 events carrying it
# in a 30-day window, only 99,692 are well formed; the other 408,194 contain no "@"
# at all and are drawn from just 18 distinct placeholder strings. Filtering on
# LIKE '%@%.%' is therefore mandatory - counting raw presence overstates identified
# coverage by roughly 3x (37% of sessions against a true 13%).
#
# The tracker also emits an HTML-entity-mangled 'amp;email' key alongside 'email'.
# It only adds ~3.5k events, but folding it in is free and the alternative is
# silently dropping them.
_EMAIL = (
    'LOWER(NULLIF(TRIM(COALESCE(QUERY_PARAMETERS:email::STRING, '
    'QUERY_PARAMETERS:"amp;email"::STRING)), \'\'))'
)
_VALID_EMAIL = f"({_EMAIL} LIKE '%@%.%')"
_DOMAIN = f"SPLIT_PART({_EMAIL}, '@', 2)"

# Consumer mailbox providers are people, not accounts. 19 of them account for 848
# sessions, which would otherwise rank above real companies in an account list.
_FREE_MAIL = (
    "('gmail.com','yahoo.com','hotmail.com','outlook.com','live.com','aol.com',"
    "'icloud.com','protonmail.com','gmx.com','mail.com','yandex.com','qq.com',"
    "'naver.com','163.com','yahoo.co.uk','googlemail.com')"
)

# TIMEZONE is 100% populated with 90 IANA zones and is the only geography signal in
# the table. The prefix before "/" gives a clean continent. UTC and Etc/* are
# settings rather than places, so they are named as such instead of being charted
# as if they were a region.
_REGION = """
        CASE
            WHEN TIMEZONE IS NULL OR TRIM(TIMEZONE) = '' THEN 'Unknown'
            WHEN TIMEZONE IN ('UTC', 'GMT') OR TIMEZONE ILIKE 'Etc/%' THEN 'Unknown / UTC'
            WHEN POSITION('/' IN TIMEZONE) > 0 THEN SPLIT_PART(TIMEZONE, '/', 1)
            ELSE 'Unknown / UTC'
        END"""


def audience_kpis_sql() -> str:
    """Identified people and companies. Only well-formed addresses count - see _EMAIL."""
    return f"""
        SELECT
            COUNT(DISTINCT IFF({_VALID_EMAIL}, {_EMAIL}, NULL)) AS IDENTIFIED_PEOPLE,
            COUNT(DISTINCT IFF({_VALID_EMAIL}, {_DOMAIN}, NULL)) AS COMPANIES,
            COUNT(DISTINCT IFF({_VALID_EMAIL}, SESSION_ID, NULL)) AS IDENTIFIED_SESSIONS,
            COUNT(DISTINCT SESSION_ID) AS TOTAL_SESSIONS,
            -- Consumer-mailbox totals belong here, not in top_accounts_sql. Summing
            -- them from a LIMITed ranking counts only those that survived the limit
            -- and reports the result as a total.
            COUNT(DISTINCT IFF({_VALID_EMAIL} AND {_DOMAIN} IN {_FREE_MAIL},
                               {_EMAIL}, NULL)) AS FREE_MAIL_PEOPLE,
            COUNT(DISTINCT IFF({_VALID_EMAIL} AND {_DOMAIN} IN {_FREE_MAIL},
                               SESSION_ID, NULL)) AS FREE_MAIL_SESSIONS
        FROM {table_fqn()}
        WHERE EVENT_TS::DATE BETWEEN ? AND ?
          AND {_NOT_TEST}
    """


def identity_cohort_sql() -> str:
    """Identified vs anonymous sessions.

    Identified sessions are ~4x more engaged (69.2% vs 17.1%) and convert somewhat
    better (1.27% vs 1.09%). Expected rather than surprising - an address usually
    means the visitor arrived from a targeted email - but it quantifies what the
    identified channel is worth.
    """
    return f"""
        WITH sess AS (
            SELECT
                SESSION_ID,
                MAX(IFF({_VALID_EMAIL}, 1, 0)) AS IDENTIFIED,
                MAX(IFF(NOT {_PAGE_VIEW}, 1, 0)) AS ACTED,
                MAX(IFF({_LEAD_SUBMIT}, 1, 0)) AS CONVERTED,
                COUNT(DISTINCT MESSAGE_ID) AS EVENT_COUNT
            FROM {table_fqn()}
            WHERE EVENT_TS::DATE BETWEEN ? AND ?
              AND SESSION_ID IS NOT NULL
              AND {_NOT_TEST}
            GROUP BY SESSION_ID
        )
        SELECT
            IFF(IDENTIFIED = 1, 'Identified', 'Anonymous') AS COHORT,
            COUNT(*) AS SESSIONS,
            COALESCE((AVG(ACTED) * 100)::FLOAT, 0) AS ENGAGEMENT_PCT,
            COALESCE((AVG(CONVERTED) * 100)::FLOAT, 0) AS LEAD_CONV_PCT,
            COALESCE((APPROX_PERCENTILE(EVENT_COUNT, 0.5))::FLOAT, 0) AS MEDIAN_EVENTS
        FROM sess
        GROUP BY IDENTIFIED
        ORDER BY SESSIONS DESC
    """


def top_accounts_sql(limit: int = 15) -> str:
    """Companies by session volume, keyed on email domain.

    Returns the domain and a headcount, never an address - individual identities are
    deliberately not queryable from the dashboard.

    The limit is applied per KIND, not overall. A plain LIMIT ranked both kinds
    together, so consumer mailboxes took slots from the corporate chart: asking for
    15 rendered 13 bars, silently. Partitioning gives a full N of each.
    """
    return f"""
        SELECT
            {_DOMAIN} AS COMPANY,
            IFF({_DOMAIN} IN {_FREE_MAIL}, 'Free mail', 'Corporate') AS KIND,
            COUNT(DISTINCT {_EMAIL}) AS PEOPLE,
            COUNT(DISTINCT SESSION_ID) AS SESSIONS,
            COUNT(DISTINCT MESSAGE_ID) AS EVENTS
        FROM {table_fqn()}
        WHERE EVENT_TS::DATE BETWEEN ? AND ?
          AND {_VALID_EMAIL}
          AND SESSION_ID IS NOT NULL
          AND {_NOT_TEST}
        GROUP BY 1, 2
        QUALIFY ROW_NUMBER() OVER (PARTITION BY KIND ORDER BY SESSIONS DESC) <= {limit}
        ORDER BY KIND, SESSIONS DESC
    """


def audience_geo_sql(timezone_limit: int = 12, locale_limit: int = 10) -> str:
    """Region, timezone and locale in ONE statement. Returns (KIND, LABEL, SESSIONS).

    These were three queries over the same 30-day window. Three statements meant
    three scans and three round trips: measured 3,842ms. Folded into one CTE scanned
    once, the same three result sets come back in 813ms - a 79% saving, and the
    largest single performance win available on this page.

    The caller splits on KIND. Ordering and per-kind limits are applied here with
    QUALIFY so the client still receives only the rows it charts.
    """
    return f"""
        WITH base AS (
            SELECT SESSION_ID, TIMEZONE, PROPERTIES:locale::STRING AS LOCALE
            FROM {table_fqn()}
            WHERE EVENT_TS::DATE BETWEEN ? AND ?
              AND SESSION_ID IS NOT NULL
        ), region AS (
            SELECT 'region' AS KIND, {_REGION} AS LABEL, COUNT(DISTINCT SESSION_ID) AS SESSIONS
            FROM base GROUP BY 1, 2
        ), tz AS (
            SELECT 'timezone' AS KIND, TIMEZONE AS LABEL, COUNT(DISTINCT SESSION_ID) AS SESSIONS
            FROM base
            WHERE TIMEZONE IS NOT NULL AND TRIM(TIMEZONE) <> ''
            GROUP BY 1, 2
            QUALIFY ROW_NUMBER() OVER (ORDER BY SESSIONS DESC) <= {timezone_limit}
        ), loc AS (
            SELECT 'locale' AS KIND, LOCALE AS LABEL, COUNT(DISTINCT SESSION_ID) AS SESSIONS
            FROM base
            WHERE LOCALE IS NOT NULL AND TRIM(LOCALE) <> ''
            GROUP BY 1, 2
            QUALIFY ROW_NUMBER() OVER (ORDER BY SESSIONS DESC) <= {locale_limit}
        )
        SELECT * FROM region
        UNION ALL SELECT * FROM tz
        UNION ALL SELECT * FROM loc
        ORDER BY KIND, SESSIONS DESC
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


# -------------------------------------------------------------------------- AI usage
#
# PROPERTIES:llm is NOT an indicator that an AI call happened. It sits on 862,752
# events where ml_request = 'false' - it records the model a surface is configured
# with, not a request. Actual usage is ml_request = 'true' (248,960 events / 5,224
# sessions in 30 days). Measuring llm presence would overstate AI usage ~4x.
_AI_REQUEST = "COALESCE(PROPERTIES:ml_request::STRING, '') = 'true'"

# 'flase' appears on 26 rows - a typo in the tracking script, surfaced on the page
# rather than silently folded into 'false'.
_ML_TYPO = "COALESCE(PROPERTIES:ml_request::STRING, '') = 'flase'"

# Unrecognised models fall through to the raw value with the region prefix stripped,
# so a newly deployed model shows up as itself instead of vanishing into "Other".
_MODEL = """
        CASE
            WHEN PROPERTIES:llm::STRING ILIKE '%opus-4-6%'   THEN 'Claude Opus 4.6'
            WHEN PROPERTIES:llm::STRING ILIKE '%sonnet-4-6%' THEN 'Claude Sonnet 4.6'
            WHEN PROPERTIES:llm::STRING ILIKE '%3-5-sonnet%' THEN 'Claude 3.5 Sonnet'
            WHEN PROPERTIES:llm IS NULL
              OR LOWER(PROPERTIES:llm::STRING) IN ('undefined', 'null', '') THEN 'Unrecorded'
            ELSE REGEXP_REPLACE(PROPERTIES:llm::STRING, '^[a-z]{2}\\\\.', '')
        END"""


def ai_kpis_sql() -> str:
    return f"""
        SELECT
            COUNT(DISTINCT IFF({_AI_REQUEST}, MESSAGE_ID, NULL)) AS AI_REQUESTS,
            COUNT(DISTINCT IFF({_AI_REQUEST}, SESSION_ID, NULL)) AS AI_SESSIONS,
            COUNT(DISTINCT SESSION_ID) AS TOTAL_SESSIONS,
            COUNT(DISTINCT IFF({_AI_REQUEST}, {_MODEL}, NULL)) AS MODELS_USED,
            COUNT(DISTINCT IFF({_ML_TYPO}, MESSAGE_ID, NULL)) AS MALFORMED_FLAG_EVENTS
        FROM {table_fqn()}
        WHERE EVENT_TS::DATE BETWEEN ? AND ?
          AND {_NOT_TEST}
    """


def model_mix_sql() -> str:
    """Which models served actual requests in this window."""
    return f"""
        SELECT {_MODEL} AS MODEL, COUNT(DISTINCT MESSAGE_ID) AS REQUESTS
        FROM {table_fqn()}
        WHERE EVENT_TS::DATE BETWEEN ? AND ?
          AND {_AI_REQUEST}
          AND {_NOT_TEST}
        GROUP BY 1
        ORDER BY REQUESTS DESC
    """


def model_trend_sql() -> str:
    """Weekly requests per model - the migration curve.

    Weekly rather than daily: a 30-day window gives 4-5 points, which reads as a
    trend, where 30 daily points on three series is noise.
    """
    return f"""
        SELECT
            DATE_TRUNC('week', EVENT_TS)::DATE AS WEEK_START,
            {_MODEL} AS MODEL,
            COUNT(DISTINCT MESSAGE_ID) AS REQUESTS
        FROM {table_fqn()}
        WHERE EVENT_TS::DATE BETWEEN ? AND ?
          AND {_AI_REQUEST}
          AND {_NOT_TEST}
        GROUP BY 1, 2
        ORDER BY WEEK_START, MODEL
    """


def ai_cohort_sql() -> str:
    """Sessions that made an AI request vs those that didn't."""
    return f"""
        WITH sess AS (
            SELECT
                SESSION_ID,
                MAX(IFF({_AI_REQUEST}, 1, 0)) AS USED_AI,
                MAX(IFF(NOT {_PAGE_VIEW}, 1, 0)) AS ACTED,
                MAX(IFF({_LEAD_SUBMIT}, 1, 0)) AS CONVERTED,
                COUNT(DISTINCT MESSAGE_ID) AS EVENT_COUNT
            FROM {table_fqn()}
            WHERE EVENT_TS::DATE BETWEEN ? AND ?
              AND SESSION_ID IS NOT NULL
              AND {_NOT_TEST}
            GROUP BY SESSION_ID
        )
        SELECT
            IFF(USED_AI = 1, 'Used AI', 'No AI') AS COHORT,
            COUNT(*) AS SESSIONS,
            COALESCE((AVG(ACTED) * 100)::FLOAT, 0) AS ENGAGEMENT_PCT,
            COALESCE((AVG(CONVERTED) * 100)::FLOAT, 0) AS LEAD_CONV_PCT,
            COALESCE((APPROX_PERCENTILE(EVENT_COUNT, 0.5))::FLOAT, 0) AS MEDIAN_EVENTS
        FROM sess
        GROUP BY USED_AI
        ORDER BY SESSIONS DESC
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


# ---------------------------------------------------------------------- multi-tenancy
#
# This dataset is not one product's traffic. It carries 321 tenants, none above 7.1%
# of events, and their rates diverge wildly: across 105 tenants with >=50 sessions,
# lead conversion runs 0% to 84.6% and only 16 sit within 2pp of the blended figure.
# The blend is well centred - it lands near the median tenant - but it predicts no
# individual tenant, so every other page should be read as a platform total.
#
# Capture began in Q4 2025 (0% before, 74.5% in 2025-10, ~99% from 2026-01), and the
# tenant count grew from 1 to 255 over that period. Any trend crossing that boundary
# is partly onboarding rather than growth.
_TENANT = "COALESCE(PROPERTIES:tenant_id::STRING, '(untagged)')"


def tenant_kpis_sql() -> str:
    return f"""
        SELECT
            COUNT(DISTINCT PROPERTIES:tenant_id::STRING) AS ACTIVE_TENANTS,
            COALESCE(
                (COUNT(DISTINCT IFF(PROPERTIES:tenant_id IS NOT NULL, MESSAGE_ID, NULL)) * 100.0
                 / NULLIF(COUNT(DISTINCT MESSAGE_ID), 0))::FLOAT, 0
            ) AS PCT_TAGGED,
            COUNT(DISTINCT SESSION_ID) AS TOTAL_SESSIONS,
            COUNT(DISTINCT CAMPAIGN_ID) AS TOTAL_CAMPAIGNS
        FROM {table_fqn()}
        WHERE EVENT_TS::DATE BETWEEN ? AND ?
          AND {_NOT_TEST}
    """


def tenant_spread_sql(min_sessions: int = 50) -> str:
    """How far the blended rates are from any real tenant. Takes 2 parameters.

    Two subtleties, both learned the hard way:

    `sess` holds one row per (tenant, session) so that per-tenant rates are right,
    but a session can carry two tenant IDs - so averaging `sess` directly counts
    those sessions twice and produced 1.05% against the Conversion page's 1.11%.
    BLENDED_* therefore roll `sess` up to one row per session first, which makes
    this page agree with page 6 by construction.

    A percentage-point band is not a useful spread measure once consent clicks are
    excluded and the rate sits near 1% - 88 of 105 tenants land within 2pp of it
    simply because most convert nobody. ZERO_CONV_TENANTS says that plainly instead.
    """
    return f"""
        WITH sess AS (
            SELECT
                {_TENANT} AS TENANT,
                SESSION_ID,
                MAX(IFF({_LEAD_SUBMIT}, 1, 0)) AS CONVERTED,
                MAX(IFF(NOT {_PAGE_VIEW}, 1, 0)) AS ACTED
            FROM {table_fqn()}
            WHERE EVENT_TS::DATE BETWEEN ? AND ?
              AND SESSION_ID IS NOT NULL
              AND {_NOT_TEST}
            GROUP BY 1, 2
        ), overall AS (
            SELECT SESSION_ID, MAX(CONVERTED) AS CONVERTED, MAX(ACTED) AS ACTED
            FROM sess
            GROUP BY SESSION_ID
        ), per_tenant AS (
            SELECT
                TENANT,
                COUNT(*) AS SESSIONS,
                AVG(CONVERTED) * 100 AS CONV_PCT,
                AVG(ACTED) * 100 AS ENG_PCT
            FROM sess
            GROUP BY TENANT
            HAVING COUNT(*) >= {min_sessions}
        )
        SELECT
            COALESCE(((SELECT AVG(CONVERTED) FROM overall) * 100)::FLOAT, 0) AS BLENDED_CONV_PCT,
            COALESCE(((SELECT AVG(ACTED) FROM overall) * 100)::FLOAT, 0) AS BLENDED_ENG_PCT,
            COUNT(*) AS TENANTS_COMPARED,
            COALESCE(MIN(CONV_PCT)::FLOAT, 0) AS CONV_MIN,
            COALESCE(APPROX_PERCENTILE(CONV_PCT, 0.50)::FLOAT, 0) AS CONV_MEDIAN,
            COALESCE(APPROX_PERCENTILE(CONV_PCT, 0.90)::FLOAT, 0) AS CONV_P90,
            COALESCE(MAX(CONV_PCT)::FLOAT, 0) AS CONV_MAX,
            COALESCE(SUM(IFF(CONV_PCT = 0, 1, 0)), 0) AS ZERO_CONV_TENANTS,
            COALESCE(MIN(ENG_PCT)::FLOAT, 0) AS ENG_MIN,
            COALESCE(APPROX_PERCENTILE(ENG_PCT, 0.50)::FLOAT, 0) AS ENG_MEDIAN,
            COALESCE(MAX(ENG_PCT)::FLOAT, 0) AS ENG_MAX
        FROM per_tenant
    """


def tenant_comparison_sql(min_sessions: int = 50, limit: int = 25) -> str:
    """Per-tenant rates, labelled with the tenant's most common campaign name.

    Takes 4 parameters - the date range twice, once for the session rollup and once
    for the label lookup. tenant_id is an opaque numeric key with no name anywhere in
    the table, so MODE(campaign_name) is the only human hint available; it is offered
    as a hint, not an identity, and is blank where no campaign is named.
    """
    return f"""
        WITH sess AS (
            SELECT
                {_TENANT} AS TENANT,
                SESSION_ID,
                MAX(IFF(NOT {_PAGE_VIEW}, 1, 0)) AS ACTED,
                MAX(IFF({_LEAD_SUBMIT}, 1, 0)) AS CONVERTED,
                COUNT(DISTINCT MESSAGE_ID) AS EVENT_COUNT
            FROM {table_fqn()}
            WHERE EVENT_TS::DATE BETWEEN ? AND ?
              AND SESSION_ID IS NOT NULL
              AND {_NOT_TEST}
            GROUP BY 1, 2
        ), labels AS (
            SELECT {_TENANT} AS TENANT, MODE(PROPERTIES:campaign_name::STRING) AS TOP_CAMPAIGN
            FROM {table_fqn()}
            WHERE EVENT_TS::DATE BETWEEN ? AND ?
              AND PROPERTIES:campaign_name IS NOT NULL
            GROUP BY 1
        )
        SELECT
            s.TENANT,
            COALESCE(l.TOP_CAMPAIGN, '') AS TOP_CAMPAIGN,
            COUNT(*) AS SESSIONS,
            COALESCE((AVG(s.ACTED) * 100)::FLOAT, 0) AS ENGAGEMENT_PCT,
            COALESCE((AVG(s.CONVERTED) * 100)::FLOAT, 0) AS LEAD_CONV_PCT,
            COALESCE((APPROX_PERCENTILE(s.EVENT_COUNT, 0.5))::FLOAT, 0) AS MEDIAN_EVENTS
        FROM sess s
        LEFT JOIN labels l ON s.TENANT = l.TENANT
        GROUP BY s.TENANT, l.TOP_CAMPAIGN
        HAVING COUNT(*) >= {min_sessions}
        ORDER BY SESSIONS DESC
        LIMIT {limit}
    """


def tenant_movement_sql(limit: int = 12) -> str:
    """Which accounts grew or went quiet. Takes 4 parameters: current range, then prior.

    This is the question the platform total cannot answer. A blended "traffic down
    37%" is not actionable; "three accounts went dark and five halved" is. FULL OUTER
    JOIN so a tenant that vanished entirely still appears, with zero current events.
    """
    return f"""
        WITH cur AS (
            SELECT {_TENANT} AS TENANT, COUNT(DISTINCT MESSAGE_ID) AS EVENTS
            FROM {table_fqn()}
            WHERE EVENT_TS::DATE BETWEEN ? AND ? AND {_NOT_TEST}
            GROUP BY 1
        ), prv AS (
            SELECT {_TENANT} AS TENANT, COUNT(DISTINCT MESSAGE_ID) AS EVENTS
            FROM {table_fqn()}
            WHERE EVENT_TS::DATE BETWEEN ? AND ? AND {_NOT_TEST}
            GROUP BY 1
        )
        SELECT
            COALESCE(c.TENANT, p.TENANT) AS TENANT,
            COALESCE(p.EVENTS, 0) AS PRIOR_EVENTS,
            COALESCE(c.EVENTS, 0) AS CURRENT_EVENTS,
            COALESCE(c.EVENTS, 0) - COALESCE(p.EVENTS, 0) AS CHANGE
        FROM cur c
        FULL OUTER JOIN prv p ON c.TENANT = p.TENANT
        ORDER BY ABS(COALESCE(c.EVENTS, 0) - COALESCE(p.EVENTS, 0)) DESC
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
