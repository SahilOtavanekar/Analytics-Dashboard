"""All SQL for the dashboard, one function per query.

Two conventions worth knowing before editing:

**Event counts use COUNT(DISTINCT MESSAGE_ID), never COUNT(*).** The source table
contains ~1.29M exactly-duplicated rows (5.9% of the table, 2.4% of the last 30
days) - same MESSAGE_ID, same timestamp to the millisecond, same session, same
path. MESSAGE_ID has no nulls anywhere. COUNT(*) overstates every event metric.

Deduplicating on it is very nearly, but not quite, exact - an earlier version of
this note claimed it was. Measured over a 30-day window: of 19,273 MESSAGE_IDs
appearing more than once, 19,265 are byte-identical duplicates (the worst single
ID carries 5,889 identical rows), but 8 carry genuinely distinct events, differing
in timestamp, session or URL. Those collapse to one, losing 1,334 events - 0.08%
of the window. Immaterial to any reading of these numbers, but it is an undercount
rather than a guarantee, and worth knowing before anyone reconciles against a
COUNT(*) from elsewhere.

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

# Consent was not the only thing riding the lead event. Enumerated across all 36 distinct
# form_id values in the data: alongside cookie-form there are consent-form and consentForm
# (39 sessions), unsubscribeForm (4) and commentform (2), and every one of them was counted
# as a conversion. A campaign named dai-unsubscribe was reporting a 5.9% conversion rate.
#
# Matched by PATTERN rather than by a list of ids, because a list of ids demonstrably drifts:
# the data already holds consent-form AND consentForm, registerForm AND registrationForm AND
# register-form, and ngd-user-data-form AND ngd-user-data-formss. Exact-matching one spelling
# is how cookie-form came to be the only form ever excluded.
#
# Confirmed as not-a-lead rather than assumed: these forms never carry an identity - 0% of
# their sessions arrive with an email against 25.7% for genuine lead forms.
#
# question-form is deliberately NOT here, though it looks similar (2.5% identified, 2,557
# sessions across 377 campaigns). It is half of all non-cookie conversions, so excluding it
# would roughly halve the reported rate everywhere - too large a move to make on an inference
# about what the form is. That one needs a product answer.
_FORM_ID = "LOWER(COALESCE(PROPERTIES:form_id::STRING, ''))"
_NOT_A_LEAD_FORM = (
    f"({_FORM_ID} LIKE '%cookie%'"
    f" OR {_FORM_ID} LIKE '%consent%'"
    f" OR {_FORM_ID} LIKE '%unsubscribe%'"
    f" OR {_FORM_ID} LIKE '%comment%')"
)
_LEAD_SUBMIT = f"({_NORM} = 'form submit' AND NOT {_NOT_A_LEAD_FORM})"

# A consent decision, recorded TWO different ways - which is how half of it stayed hidden.
# ACCEPTING fires form_submit with form_id='cookie-form'. REJECTING fires `clicked` on a button
# carrying attributes:consent='rejected'. Measured over 30 days: 4,586 rejections across 1,714
# sessions and 318 campaigns were counted as ordinary clicks, so one decision appeared as two
# unrelated bars and the rejection half read as interest in the content.
# Three spellings, not two: `consent-response` is its own event name carrying a consent_status
# property. Small - 12 rows, 2 sessions, 1 campaign - but leaving it out meant the page excluded
# two kinds of consent and then charted a third as an action.
_CONSENT_EVENT = (
    f"({_COOKIE_FORM}"
    f" OR PROPERTIES:attributes:consent IS NOT NULL"
    f" OR LOWER(COALESCE(EVENT_NAME, '')) = 'consent-response')"
)

# What "engaged" means: the session did something that was neither a page view nor a consent
# decision. Dismissing a cookie banner is not engagement with a campaign, and counting it
# inflated Engaged everywhere - on SG0326-009, 18 of the 100 "engaged" sessions did nothing but
# answer the banner, so 5.0% should read 4.1%.
_ENGAGED_EVENT = f"(NOT {_PAGE_VIEW} AND NOT {_CONSENT_EVENT})"

# What the action MEANT, read from PROPERTIES:attributes rather than from EVENT_NAME. Almost
# every action in this data is called `clicked`, so labelling by name produced a single bar
# reading "Clicked" that mixed opening a document (17,629 rows across 325 campaigns), asking a
# question (894) and dismissing a consent banner (4,586) into one number.
#
# attributes is where the product records what was clicked. attributes:element is deliberately
# NOT used: it holds the HTML tag (li, button, div, a, input, summary), and a reader does not
# care that a click landed on a <li>.
#
# `visited` is mapped by its top-level asset rather than left to fall through. Untouched it
# rendered as "Visited", the same word as the funnel's first stage but meaning something else
# entirely - 1,127 sessions across 6 campaigns, and every one of them carries an asset and a
# title, so it belongs with the content clicks.
# Order matters. Every key was enumerated by flattening OBJECT_KEYS(PROPERTIES:attributes)
# rather than guessed, after a first attempt that tested only `asset` and dropped 37 of one
# campaign's 63 engaged sessions into "Other click" when they were titled link clicks:
#
#   title   18,270 rows / 341 campaigns   asset  17,762 / 325   consent 4,619 / 318
#   click    2,346 / 1 campaign           category  894 / 71    dismiss   383 / 78
#   file        23 / 1 campaign
#
# asset and title co-occur on a content open, so asset is tested FIRST and only title-without-
# asset falls through to a link click. `file` is one campaign's spelling of the same thing.
# `click` and `id`/`set` are single-campaign keys and are left to the generic buckets.
_ACTION_LABEL = f"""
        CASE
            WHEN PROPERTIES:attributes:asset IS NOT NULL
              OR PROPERTIES:attributes:file IS NOT NULL THEN 'Opened content'
            WHEN LOWER(COALESCE(EVENT_NAME, '')) = 'visited'
                 AND PROPERTIES:asset IS NOT NULL THEN 'Opened content'
            WHEN PROPERTIES:attributes:category::STRING = 'query' THEN 'Asked a question'
            WHEN LOWER(COALESCE(EVENT_NAME, '')) = 'pdf-page-visit' THEN 'Read a document'
            WHEN {_NORM} = 'form submit' THEN 'Submitted a form'
            WHEN PROPERTIES:attributes:title IS NOT NULL
              OR PROPERTIES:attributes:click IS NOT NULL THEN 'Opened a link'
            WHEN PROPERTIES:attributes:dismiss IS NOT NULL THEN 'Dismissed a prompt'
            WHEN LOWER(COALESCE(EVENT_NAME, '')) IN ('clicked', 'button clicked') THEN 'Other click'
            ELSE INITCAP(LOWER(REPLACE(EVENT_NAME, '_', ' ')))
        END"""

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
    """InSyte's value chain, not generic web metrics. Takes 2 parameters.

    This used to return total events / sessions / campaigns, which describe any
    website and say nothing about whether the product is working. InSyte turns
    documents into AI-led experiences that identify accounts, so the chain that
    matters is: content reached -> AI engaged on that content -> account
    identified -> lead captured. Each of these maps to a capability the product
    is actually sold on.

    AI_ATTACH_PCT is deliberately AI-on-content, not AI overall: "Transform
    Content Into Conversations" is a claim about what happens inside a document,
    and 1,512 sessions use AI with no asset at all, which would flatter it.

    The constants below are defined further down this module. That is fine - the
    f-string is evaluated when the function is called, not at import.
    """
    return f"""
        WITH raw AS (
            SELECT
                SESSION_ID,
                IFF({_ASSET} IS NOT NULL, 1, 0) AS SAW_CONTENT,
                IFF({_AI_REQUEST}, 1, 0) AS USED_AI,
                IFF({_LEAD_SUBMIT}, 1, 0) AS CONVERTED,
                IFF({_VALID_EMAIL}, 1, 0) AS IDENTIFIED,
                IFF({_VALID_EMAIL}, {_DOMAIN}, NULL) AS COMPANY
            FROM {table_fqn()}
            WHERE EVENT_TS::DATE BETWEEN ? AND ?
              AND SESSION_ID IS NOT NULL
              AND {_NOT_TEST}
        ), sess AS (
            SELECT
                SESSION_ID,
                MAX(SAW_CONTENT) AS SAW_CONTENT,
                MAX(USED_AI) AS USED_AI,
                MAX(CONVERTED) AS CONVERTED,
                MAX(IDENTIFIED) AS IDENTIFIED
            FROM raw
            GROUP BY SESSION_ID
        )
        SELECT
            COUNT(*) AS TOTAL_SESSIONS,
            COALESCE(SUM(SAW_CONTENT), 0) AS CONTENT_SESSIONS,
            COALESCE((SUM(IFF(SAW_CONTENT = 1 AND USED_AI = 1, 1, 0)) * 100.0
                      / NULLIF(SUM(SAW_CONTENT), 0))::FLOAT, 0) AS AI_ATTACH_PCT,
            COALESCE(SUM(IDENTIFIED), 0) AS IDENTIFIED_SESSIONS,
            -- Counted over raw rows, matching audience_kpis_sql exactly, so the two
            -- pages cannot disagree on how many companies were reached.
            (SELECT COUNT(DISTINCT COMPANY) FROM raw) AS COMPANIES,
            COALESCE((AVG(CONVERTED) * 100)::FLOAT, 0) AS LEAD_CONV_PCT
        FROM sess
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
                MAX(IFF({_ENGAGED_EVENT}, 1, 0)) AS DID_ACT,
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

    Grouped on what the action MEANT, from PROPERTIES:attributes - see _ACTION_LABEL. Grouping
    on EVENT_NAME collapsed opening a document, asking a question and rejecting a cookie banner
    into one bar reading "Clicked", because almost every action in this data carries that name.

    Consent is excluded entirely rather than shown as a peer bar. Accepting was already split
    out; REJECTING was not, and arrived here as an ordinary click - 1,714 sessions across 318
    campaigns. One decision, two bars, and half of it presented as interest.
    """
    return f"""
        WITH all_sessions AS (
            SELECT SESSION_ID
            FROM {table_fqn()}
            WHERE EVENT_TS::DATE BETWEEN ? AND ?
              AND SESSION_ID IS NOT NULL
            GROUP BY SESSION_ID
        ), acted AS (
            SELECT DISTINCT SESSION_ID, {_ACTION_LABEL} AS ACTION
            FROM {table_fqn()}
            WHERE EVENT_TS::DATE BETWEEN ? AND ?
              AND SESSION_ID IS NOT NULL
              AND EVENT_NAME IS NOT NULL
              AND {_ENGAGED_EVENT}
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
            -- Split on the SAME rule the conversion metric uses, not on cookie-form alone.
            -- Keyed on _COOKIE_FORM this table listed consent-form under "Lead form" while the
            -- funnel beside it excluded that form from conversions - one page, two answers.
            IFF({_NOT_A_LEAD_FORM}, 'Consent / not a lead', 'Lead form') AS FORM_KIND,
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
    """Lead submits vs excluded submits, so the size of the correction stays visible.

    The two buckets have to PARTITION every form submit, because the page adds them together
    and reports the sum as the total. Counting the excluded side with _COOKIE_FORM while the
    lead side used the wider rule left consent, unsubscribe and comment submits in neither
    bucket, so that total silently under-reported and the percentage drawn from it was wrong.
    """
    return f"""
        SELECT
            COUNT(DISTINCT IFF({_LEAD_SUBMIT}, SESSION_ID, NULL)) AS LEAD_SESSIONS,
            COUNT(DISTINCT IFF({_NOT_A_LEAD_FORM}, SESSION_ID, NULL)) AS CONSENT_SESSIONS,
            COUNT(DISTINCT IFF({_LEAD_SUBMIT}, MESSAGE_ID, NULL)) AS LEAD_SUBMITS,
            COUNT(DISTINCT IFF({_NOT_A_LEAD_FORM}, MESSAGE_ID, NULL)) AS CONSENT_SUBMITS
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
                MAX(IFF({_ENGAGED_EVENT}, 1, 0)) AS ACTED,
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
    """Percentiles and degenerate-case counts - what .describe() should have shown.

    Every aggregate is COALESCEd. SUM, MAX, AVG and APPROX_PERCENTILE all return
    NULL over an empty set while COUNT returns 0, so a date range with no sessions
    otherwise hands the page a row of NaN. See session_integrity_sql for the
    variant of this that crashed a page outright.
    """
    return f"""
        SELECT
            COUNT(*) AS SESSIONS,
            COALESCE(ROUND(APPROX_PERCENTILE(DURATION_SECONDS, 0.50) / 60.0, 3), 0) AS P50_MINUTES,
            COALESCE(ROUND(APPROX_PERCENTILE(DURATION_SECONDS, 0.75) / 60.0, 3), 0) AS P75_MINUTES,
            COALESCE(ROUND(APPROX_PERCENTILE(DURATION_SECONDS, 0.90) / 60.0, 2), 0) AS P90_MINUTES,
            COALESCE(ROUND(APPROX_PERCENTILE(DURATION_SECONDS, 0.95) / 60.0, 2), 0) AS P95_MINUTES,
            COALESCE(ROUND(APPROX_PERCENTILE(DURATION_SECONDS, 0.99) / 60.0, 2), 0) AS P99_MINUTES,
            COALESCE(ROUND(AVG(DURATION_SECONDS) / 60.0, 2), 0) AS MEAN_MINUTES,
            COALESCE(ROUND(MAX(DURATION_SECONDS) / 86400.0, 2), 0) AS MAX_DAYS,
            COALESCE(SUM(IFF(DURATION_SECONDS = 0, 1, 0)), 0) AS INSTANT_SESSIONS,
            COALESCE(SUM(IFF(DURATION_SECONDS > 7200, 1, 0)), 0) AS OVER_2_HOURS,
            COALESCE(SUM(IFF(DURATION_SECONDS > 86400, 1, 0)), 0) AS OVER_24_HOURS
        FROM ({_session_summary_cte()})
    """


def session_integrity_sql() -> str:
    """How far SESSION_ID can be trusted to mean "one visit". Aggregates COALESCEd.

    Without the COALESCE this returned SESSIONS = 0 alongside NULL for the other
    three columns on any range with no sessions, and Session Analytics called
    int() on one of them - a hard ValueError and a red traceback in place of the
    page. COUNT returns 0 over an empty set; SUM and MAX return NULL.

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
            COALESCE(SUM(IFF(IPS > 1, 1, 0)), 0) AS MULTI_IP_SESSIONS,
            COALESCE(MAX(IPS), 0) AS MAX_IPS_ON_ONE_SESSION,
            COALESCE(SUM(IFF(DURATION_SECONDS > 86400, 1, 0)), 0) AS OVER_24_HOURS
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
                MAX(IFF({_ENGAGED_EVENT}, 1, 0)) AS ACTED,
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
    """Aggregates COALESCEd - see session_duration_percentiles_sql for why.

    P95_SESSIONS additionally feeds the bot-warning threshold on the Audience
    page. A NaN there made every comparison false, so the warning silently could
    not fire rather than failing loudly.
    """
    return f"""
        SELECT
            COUNT(*) AS VISITORS,
            COALESCE(APPROX_PERCENTILE(SESSION_COUNT, 0.50), 0) AS P50_SESSIONS,
            COALESCE(APPROX_PERCENTILE(SESSION_COUNT, 0.75), 0) AS P75_SESSIONS,
            COALESCE(APPROX_PERCENTILE(SESSION_COUNT, 0.90), 0) AS P90_SESSIONS,
            COALESCE(APPROX_PERCENTILE(SESSION_COUNT, 0.95), 0) AS P95_SESSIONS,
            COALESCE(APPROX_PERCENTILE(SESSION_COUNT, 0.99), 0) AS P99_SESSIONS,
            COALESCE(ROUND(AVG(SESSION_COUNT), 2), 0) AS MEAN_SESSIONS,
            COALESCE(MAX(SESSION_COUNT), 0) AS MAX_SESSIONS,
            COALESCE(SUM(IFF(SESSION_COUNT = 1, 1, 0)), 0) AS SINGLE_SESSION_VISITORS,
            COALESCE(SUM(IFF(SESSION_COUNT > 100, 1, 0)), 0) AS OVER_100_SESSIONS
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
                MAX(IFF({_ENGAGED_EVENT}, 1, 0)) AS ACTED,
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


def _campaign_table() -> str:
    """The table for every campaign-scoped query on Campaign Analytics.

    Identical to table_fqn() except when the reader has internal traffic excluded, where it
    keeps campaigns running on the demand_ai tenant and still drops demandai.co visitors. A
    list OF campaigns should contain Demand AI's own - demand_ai_internal_website_track is
    third by sessions and disappeared completely with the filter on - while staff activity
    stays out of every audience and identity figure. See db.table_fqn for the full reasoning.

    Every campaign builder below routes through this one function, for the same reason
    table_fqn exists: 14 call sites, one rule, and no chance of one query reporting a
    different population from the query beside it.
    """
    return table_fqn(keep_own_campaigns=True)


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
        FROM {_campaign_table()}
        WHERE EVENT_TS::DATE BETWEEN ? AND ?
    """


# The page asks for far more campaigns than it charts, because the search-as-you-type
# picker needs every campaign in the window and the LIMIT does not reduce the work: measured
# on a 3-month window, top_campaigns_sql costs 4.62s at LIMIT 10 and 4.68s at LIMIT 2000 for
# the same GROUP BY. A separate lighter query for the picker was built first and measured
# 2.3s at three months and 12.4s at a year - a whole extra scan to re-derive rows this query
# already had. The page charts the first ten rows and offers all of them in the picker.
#
# The number is a payload guard rather than a ranking. The widest window in this dataset
# holds 1,027 campaigns, so it is never reached today; it exists so a table that grows an
# order of magnitude does not silently start shipping tens of thousands of options.
CAMPAIGN_PICKER_LIMIT = 2000


def top_campaigns_sql(limit: int = 10) -> str:
    """The window's biggest campaigns, with enough context to describe each one.

    CAMPAIGN_ID is the stable key, but it is not a label. Three shapes exist, measured
    over the whole dataset: 425 campaigns whose ID is a readable slug and which carry no
    campaign_name at all (ibm-ai, epicor-ai), 526 with a UUID and a name, and 70 with a
    UUID and no name. Only that last group is unreadable, and it can rank - its largest
    member has 2,569 sessions against a rank-10 floor of 2,018 on a 3-month window - so
    LABEL_IS_OPAQUE is returned rather than letting a UUID pass as a campaign name.
    campaign_name capture began 2026-03; before that every label comes from the ID.

    Ranked by sessions, then events, then ID. That ordering does three jobs:

      - sessions is reach, which the old events ranking did not measure. Events are
        97-99.9% page views, so ranking on them ranked content volume: SE0426-005 sat
        7th on 642 sessions because each of its sessions fired 159 events.
      - events is a tiebreak that carries real weight. SESSION_ID was not recorded on
        campaign rows before 2025-11 (0% of rows for 2025-06..2025-10, 2.93M events),
        so on any window inside that period every campaign has zero sessions and this
        ordering degenerates to exactly the old events ranking - the same ten rows the
        reader would otherwise have seen, not an arbitrary ten. The page relabels the
        axis when it happens rather than drawing ten bars of length zero.
      - CAMPAIGN_ID last, because ties straddle rank 10 on narrow windows (3 of 11
        windows tested) and LIMIT without a total order reshuffles between loads.

    CAMPAIGN_ID IS NOT NULL is not cosmetic. Without it the null-campaign group ranks
    on its own: 2025-06-14..07-14 currently returns a single bar with a NULL label and
    660 events, and the group holds 109,989 events across the whole dataset. _NOT_TEST
    matches every sibling ranking query; no campaign row is a test event today, so it
    changes nothing now and stops it mattering later.

    Every rate is COALESCEd because its denominator genuinely reaches zero: on the
    October 2025 window all ten ranked campaigns have no sessions, and an unguarded
    ratio there is NULL, which reaches the tooltip as a blank and reads as a zero.
    """
    return f"""
        WITH ev AS (
            SELECT
                CAMPAIGN_ID, SESSION_ID, MESSAGE_ID, EVENT_TS,
                PROPERTIES:campaign_name::STRING AS NAME,
                {_TENANT} AS TENANT,
                {_ASSET} AS ASSET,
                IFF({_LEAD_SUBMIT}, 1, 0) AS IS_LEAD
            FROM {_campaign_table()}
            WHERE EVENT_TS::DATE BETWEEN ? AND ?
              AND CAMPAIGN_ID IS NOT NULL
              AND {_NOT_TEST}
        ), per_session AS (
            SELECT
                CAMPAIGN_ID, SESSION_ID,
                MAX(IFF(ASSET IS NOT NULL, 1, 0)) AS HAD_ASSET,
                MAX(IS_LEAD) AS HAD_LEAD
            FROM ev
            WHERE SESSION_ID IS NOT NULL
            GROUP BY 1, 2
        ), sess AS (
            SELECT
                CAMPAIGN_ID,
                COUNT(*) AS SESSIONS,
                SUM(HAD_ASSET) AS ASSET_SESSIONS,
                SUM(HAD_LEAD) AS LEAD_SESSIONS
            FROM per_session
            GROUP BY 1
        ), camp AS (
            SELECT
                CAMPAIGN_ID,
                MODE(NAME) AS NAME,
                MODE(TENANT) AS TENANT,
                COUNT(DISTINCT MESSAGE_ID) AS EVENTS,
                COUNT(DISTINCT ASSET) AS ASSETS,
                MIN(EVENT_TS)::DATE AS FIRST_SEEN,
                MAX(EVENT_TS)::DATE AS LAST_SEEN,
                COUNT(DISTINCT EVENT_TS::DATE) AS ACTIVE_DAYS
            FROM ev
            GROUP BY 1
        ), window_total AS (
            SELECT COUNT(DISTINCT SESSION_ID) AS WINDOW_SESSIONS FROM ev
        )
        SELECT
            c.CAMPAIGN_ID,
            COALESCE(c.NAME, c.CAMPAIGN_ID) AS CAMPAIGN_LABEL,
            IFF(c.NAME IS NULL, FALSE, TRUE) AS HAS_NAME,
            IFF(c.NAME IS NULL AND REGEXP_LIKE(c.CAMPAIGN_ID,
                '^[0-9a-f]{{8}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{4}}-[0-9a-f]{{12}}$', 'i'),
                TRUE, FALSE) AS LABEL_IS_OPAQUE,
            c.TENANT,
            COALESCE(s.SESSIONS, 0) AS SESSIONS,
            c.EVENTS,
            w.WINDOW_SESSIONS,
            COALESCE((c.EVENTS / NULLIF(s.SESSIONS, 0))::FLOAT, 0) AS EVENTS_PER_SESSION,
            COALESCE((s.SESSIONS / NULLIF(w.WINDOW_SESSIONS, 0) * 100)::FLOAT, 0) AS SESSION_SHARE_PCT,
            c.ASSETS,
            COALESCE((s.ASSET_SESSIONS / NULLIF(s.SESSIONS, 0) * 100)::FLOAT, 0) AS ASSET_PCT,
            COALESCE((s.LEAD_SESSIONS / NULLIF(s.SESSIONS, 0) * 100)::FLOAT, 0) AS LEAD_PCT,
            c.FIRST_SEEN,
            c.LAST_SEEN,
            c.ACTIVE_DAYS
        FROM camp c
        LEFT JOIN sess s ON c.CAMPAIGN_ID = s.CAMPAIGN_ID
        CROSS JOIN window_total w
        ORDER BY SESSIONS DESC, c.EVENTS DESC, c.CAMPAIGN_ID
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
                MAX(IFF({_ENGAGED_EVENT}, 1, 0)) AS ACTED
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
                MAX(IFF({_ENGAGED_EVENT}, 1, 0)) AS ACTED,
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


# ------------------------------------------------------------------ campaign drill-down
#
# One campaign, every angle that has data for it. EVERY query below takes exactly three
# parameters in this order: start date, end date, campaign id - so the SQL text is
# identical for every campaign and only the bound parameter changes. run_query caches on
# (sql, params), so each campaign gets its own cache entry and switching between two
# already-viewed campaigns costs nothing.
#
# Which sections are worth building was measured across the 336 campaigns with >=50
# sessions in a 3-month window, rather than assumed:
#
#   geography, session duration, actions, activity   100%   always rendered
#   referrers                                         95%   conditional
#   content (assets)                                  87%   conditional
#   pdf read depth                                    65%   conditional
#   lead conversion                                   50%   conditional
#   identity (any)                                    57%   conditional, and gated
#   identity (>=5 identified people)                  42%   the PII floor below
#   AI usage                                          19%   conditional
#
# Two angles the global dashboard has are deliberately NOT reproduced here. Top Pages:
# a campaign resolves to 1-5 distinct paths, because the campaign essentially IS one
# document, so the chart would be one or two bars. Language: locale is 0 or 1 distinct
# value per campaign. Both would be permanently near-empty.
#
# AI is a conditional section rather than an absent one. An earlier note in this project
# claimed ml_request events carry no campaign ID; that was wrong - 1,265,377 of 1,265,649
# of them do. AI is simply concentrated in 138 of 1,023 campaigns, none of which are the
# largest by sessions.
_CAMPAIGN_SCOPE = """
        WHERE EVENT_TS::DATE BETWEEN ? AND ?
          AND CAMPAIGN_ID = ?
          AND {not_test}"""


def _campaign_events_cte() -> str:
    """Every column the drill-down needs, for one campaign, read in a single scan."""
    return f"""
        SELECT
            SESSION_ID, MESSAGE_ID, EVENT_TS, EVENT_NAME, EVENT_TYPE, TIMEZONE,
            SEARCH_URL, REFERER_URL,
            PROPERTIES:campaign_name::STRING AS NAME,
            {_TENANT} AS TENANT,
            {_EMAIL} AS ADDR,
            {_ASSET} AS ASSET,
            PROPERTIES:page_visited AS PAGE_VISITED,
            IFF({_AI_REQUEST}, 1, 0) AS IS_AI,
            IFF({_LEAD_SUBMIT}, 1, 0) AS IS_LEAD,
            IFF({_COOKIE_FORM}, 1, 0) AS IS_COOKIE,
            -- The two halves of a consent decision, kept apart because they answer different
            -- questions and were previously in two unrelated places on the page.
            IFF({_COOKIE_FORM}, 1, 0) AS IS_CONSENT_YES,
            IFF(PROPERTIES:attributes:consent IS NOT NULL, 1, 0) AS IS_CONSENT_NO,
            -- Projected here so the action chart can group on meaning rather than on EVENT_NAME;
            -- the label needs PROPERTIES, which does not survive this CTE's column list.
            {_ACTION_LABEL} AS ACTION_LABEL,
            IFF({_ENGAGED_EVENT}, 1, 0) AS IS_ACTION
        FROM {_campaign_table()}
        {_CAMPAIGN_SCOPE.format(not_test=_NOT_TEST)}
    """


# Validity is re-tested against the CTE's own ADDR column rather than reusing
# _VALID_EMAIL, which re-derives the address from QUERY_PARAMETERS and would not resolve
# against a projected column. Same rule, applied one layer up.
_ADDR_VALID = "ADDR LIKE '%@%.%'"


def campaign_detail_kpis_sql() -> str:
    """One row describing the campaign, and the gate for every conditional section.

    Deliberately one query rather than ten small ones. The page needs to know whether a
    section has any data BEFORE it decides to render it, and asking that per section
    would mean a round trip per section just to discover an empty state. Everything here
    comes from a single scan; the section queries only run for sections that survive.

    Counts, not rates. The page divides them, so a zero denominator is handled once in
    Python rather than needing a COALESCE on every ratio here.
    """
    return f"""
        WITH ev AS ({_campaign_events_cte()}
        ), sess AS (
            SELECT
                SESSION_ID,
                MAX(IS_AI) AS USED_AI,
                MAX(IS_LEAD) AS CONVERTED,
                MAX(IS_ACTION) AS ACTED,
                MAX(IS_CONSENT_YES) AS SAID_YES,
                MAX(IS_CONSENT_NO) AS SAID_NO,
                MAX(IFF(ASSET IS NOT NULL, 1, 0)) AS SAW_CONTENT,
                MAX(IFF({_ADDR_VALID}, 1, 0)) AS IDENTIFIED,
                COUNT(DISTINCT MESSAGE_ID) AS EVENTS,
                DATEDIFF('second', MIN(EVENT_TS), MAX(EVENT_TS)) AS SECS
            FROM ev
            WHERE SESSION_ID IS NOT NULL
            GROUP BY SESSION_ID
        ), ev_agg AS (
            -- One pass over the events, not one pass per column. Written as 13 scalar
            -- subqueries first, which measured 4-6s because each re-scanned the CTE;
            -- folded into a single aggregate row it lands near a second. This query is on
            -- the critical path of every drill-in, so it is the one worth flattening.
            SELECT
                MODE(NAME) AS CAMPAIGN_NAME,
                MODE(TENANT) AS TENANT,
                COUNT(DISTINCT MESSAGE_ID) AS EVENTS,
                MIN(EVENT_TS)::DATE AS FIRST_SEEN,
                MAX(EVENT_TS)::DATE AS LAST_SEEN,
                COUNT(DISTINCT EVENT_TS::DATE) AS ACTIVE_DAYS,
                COUNT(DISTINCT ASSET) AS ASSETS,
                COUNT(DISTINCT IFF({_ADDR_VALID}, ADDR, NULL)) AS PEOPLE,
                COUNT(DISTINCT IFF({_ADDR_VALID}, SPLIT_PART(ADDR, '@', 2), NULL)) AS COMPANIES,
                COUNT(DISTINCT IFF(IS_AI = 1, MESSAGE_ID, NULL)) AS AI_EVENTS,
                COUNT(DISTINCT IFF(PAGE_VISITED IS NOT NULL, MESSAGE_ID, NULL)) AS PDF_EVENTS,
                COUNT(DISTINCT IFF(COALESCE(REFERER_URL, '') <> '', REFERER_URL, NULL)) AS REFERRERS,
                COUNT(DISTINCT TIMEZONE) AS TIMEZONES
            FROM ev
        ), sess_agg AS (
            SELECT
                COUNT(*) AS SESSIONS,
                COALESCE(SUM(USED_AI), 0) AS AI_SESSIONS,
                COALESCE(SUM(CONVERTED), 0) AS LEAD_SESSIONS,
                COALESCE(SUM(ACTED), 0) AS ACTED_SESSIONS,
                COALESCE(SUM(SAID_YES), 0) AS CONSENT_YES_SESSIONS,
                COALESCE(SUM(SAID_NO), 0) AS CONSENT_NO_SESSIONS,
                COALESCE(SUM(SAW_CONTENT), 0) AS CONTENT_SESSIONS,
                COALESCE(SUM(IDENTIFIED), 0) AS IDENTIFIED_SESSIONS,
                COALESCE((APPROX_PERCENTILE(EVENTS, 0.5))::FLOAT, 0) AS MEDIAN_EVENTS,
                COALESCE((APPROX_PERCENTILE(SECS, 0.5) / 60.0)::FLOAT, 0) AS MEDIAN_DURATION_MINUTES,
                COALESCE((AVG(SECS) / 60.0)::FLOAT, 0) AS MEAN_DURATION_MINUTES,
                COALESCE(SUM(IFF(SECS = 0, 1, 0)), 0) AS INSTANT_SESSIONS
            FROM sess
        )
        SELECT * FROM ev_agg CROSS JOIN sess_agg
    """


def campaign_lookup_sql() -> str:
    """Does this campaign id exist, and does it have anything in the selected window?

    Three parameters like every other drill-down query: start, end, campaign id.

    Both halves are needed because "nothing found" has two very different causes, and a
    reader typing an id deserves to be told which one they hit. An id that is simply wrong
    is a typo. An id that is real but ran outside the selected dates is a date-range
    problem, and this returns the dates it did run so the message can say so instead of
    leaving someone to widen the range by trial and error.

    Bounded to the data floor rather than left unbounded: the table holds 205 rows stamped
    before 2020 and three in the future, and an unbounded MIN/MAX would report 1978.
    """
    return f"""
        SELECT
            COUNT(DISTINCT IFF(EVENT_TS::DATE BETWEEN ? AND ?, MESSAGE_ID, NULL)) AS EVENTS_IN_WINDOW,
            COUNT(DISTINCT MESSAGE_ID) AS EVENTS_EVER,
            MIN(EVENT_TS)::DATE AS FIRST_EVER,
            MAX(EVENT_TS)::DATE AS LAST_EVER,
            MODE(PROPERTIES:campaign_name::STRING) AS CAMPAIGN_NAME
        FROM {_campaign_table()}
        WHERE CAMPAIGN_ID = ?
          AND EVENT_TS::DATE BETWEEN '2025-06-01' AND CURRENT_DATE()
          AND {_NOT_TEST}
    """


def campaign_daily_sql() -> str:
    """Daily sessions and events for one campaign - the shape of its run."""
    return f"""
        SELECT
            EVENT_TS::DATE AS EVENT_DATE,
            COUNT(DISTINCT SESSION_ID) AS SESSION_COUNT,
            COUNT(DISTINCT MESSAGE_ID) AS EVENT_COUNT
        FROM {_campaign_table()}
        {_CAMPAIGN_SCOPE.format(not_test=_NOT_TEST)}
        GROUP BY EVENT_DATE
        ORDER BY EVENT_DATE
    """


def campaign_duration_bands_sql() -> str:
    """Session duration in the same seven bands Session Analytics uses.

    The most discriminating section per campaign, measured: ibm-ai has 2,584 of 9,306
    sessions with any duration at all and a 0s median, while snowflake-apac-ai has 8,733
    of 8,977 and a 42s median. Same order of size, entirely different reading behaviour.
    Bands are shared with page 3 on purpose so the two are directly comparable.
    """
    return f"""
        WITH s AS (
            SELECT SESSION_ID, DATEDIFF('second', MIN(EVENT_TS), MAX(EVENT_TS)) AS DURATION_SECONDS
            FROM {_campaign_table()}
            {_CAMPAIGN_SCOPE.format(not_test=_NOT_TEST)}
              AND SESSION_ID IS NOT NULL
            GROUP BY SESSION_ID
        )
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
        FROM s
        GROUP BY BAND, BAND_ORDER
        ORDER BY BAND_ORDER
    """


def campaign_funnel_sql() -> str:
    """The same three nesting stages as page 6, scoped to one campaign."""
    return f"""
        WITH sess AS (
            SELECT
                SESSION_ID,
                MAX(IFF({_ENGAGED_EVENT}, 1, 0)) AS DID_ACT,
                MAX(IFF({_LEAD_SUBMIT}, 1, 0)) AS DID_CONVERT
            FROM {_campaign_table()}
            {_CAMPAIGN_SCOPE.format(not_test=_NOT_TEST)}
              AND SESSION_ID IS NOT NULL
            GROUP BY SESSION_ID
        )
        SELECT 1 AS STAGE_ORDER, 'Visited' AS STAGE, COUNT(*) AS SESSIONS FROM sess
        UNION ALL
        SELECT 2, 'Took an action', COALESCE(SUM(DID_ACT), 0) FROM sess
        UNION ALL
        SELECT 3, 'Submitted a lead form', COALESCE(SUM(DID_CONVERT), 0) FROM sess
        ORDER BY STAGE_ORDER
    """


def campaign_actions_sql() -> str:
    """Which actions this campaign's visitors took, as reach.

    The denominator comes from a scalar subquery over the same CTE rather than a second
    scan, which also keeps this at three parameters like every other query here - the
    global action_reach_sql needs its range twice for exactly this reason.
    """
    return f"""
        WITH ev AS ({_campaign_events_cte()}
        ), all_sessions AS (
            SELECT COUNT(DISTINCT SESSION_ID) AS N FROM ev WHERE SESSION_ID IS NOT NULL
        ), acted AS (
            -- Consent is absent from this chart by construction: IS_ACTION excludes it, and the
            -- accept/reject counts are reported as a sentence beside the chart instead. It used
            -- to be the second-largest bar here while not being engagement at all.
            SELECT DISTINCT SESSION_ID, ACTION_LABEL AS ACTION
            FROM ev
            WHERE SESSION_ID IS NOT NULL
              AND EVENT_NAME IS NOT NULL
              AND IS_ACTION = 1
        )
        SELECT
            ACTION,
            COUNT(DISTINCT SESSION_ID) AS SESSIONS,
            COALESCE((COUNT(DISTINCT SESSION_ID) * 100.0
                / NULLIF((SELECT N FROM all_sessions), 0))::FLOAT, 0) AS PCT_OF_SESSIONS
        FROM acted
        GROUP BY ACTION
        ORDER BY SESSIONS DESC
    """


def campaign_geo_sql(timezone_limit: int = 10) -> str:
    """Region and timezone for one campaign, one scan, split by KIND like audience_geo_sql."""
    return f"""
        WITH base AS (
            SELECT SESSION_ID, TIMEZONE
            FROM {_campaign_table()}
            {_CAMPAIGN_SCOPE.format(not_test=_NOT_TEST)}
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
        )
        SELECT * FROM region
        UNION ALL SELECT * FROM tz
        ORDER BY KIND, SESSIONS DESC
    """


def campaign_assets_sql(limit: int = 12) -> str:
    """Content this campaign put in front of people, by reach."""
    return f"""
        SELECT
            {_ASSET_LABEL} AS ASSET,
            COUNT(DISTINCT SESSION_ID) AS SESSIONS,
            COUNT(DISTINCT MESSAGE_ID) AS EVENTS,
            ROUND(COUNT(DISTINCT MESSAGE_ID) * 1.0
                  / NULLIF(COUNT(DISTINCT SESSION_ID), 0), 1) AS EVENTS_PER_SESSION
        FROM {_campaign_table()}
        {_CAMPAIGN_SCOPE.format(not_test=_NOT_TEST)}
          AND {_ASSET} IS NOT NULL
          AND SESSION_ID IS NOT NULL
        GROUP BY {_ASSET}
        ORDER BY SESSIONS DESC
        LIMIT {limit}
    """


def campaign_read_depth_sql(limit: int = 10) -> str:
    """How far into this campaign's documents people got."""
    return f"""
        SELECT
            {_ASSET_LABEL} AS ASSET,
            COUNT(DISTINCT SESSION_ID) AS SESSIONS,
            ROUND(AVG(TRY_TO_NUMBER(PROPERTIES:page_visited::STRING)), 1) AS AVG_PAGE_REACHED,
            MAX(TRY_TO_NUMBER(PROPERTIES:page_visited::STRING)) AS DEEPEST_PAGE
        FROM {_campaign_table()}
        {_CAMPAIGN_SCOPE.format(not_test=_NOT_TEST)}
          AND PROPERTIES:page_visited IS NOT NULL
          AND {_ASSET} IS NOT NULL
          AND SESSION_ID IS NOT NULL
        GROUP BY {_ASSET}
        ORDER BY SESSIONS DESC
        LIMIT {limit}
    """


def campaign_companies_sql(limit: int = 12) -> str:
    """Which accounts this campaign actually reached.

    Consumer mailboxes are excluded, matching Top Companies on the Audience page - they
    are people, not accounts. Returns domains and headcounts only; an individual address
    is never returned by any query in this module.
    """
    return f"""
        SELECT
            {_DOMAIN} AS COMPANY,
            COUNT(DISTINCT {_EMAIL}) AS PEOPLE,
            COUNT(DISTINCT SESSION_ID) AS SESSIONS
        FROM {_campaign_table()}
        {_CAMPAIGN_SCOPE.format(not_test=_NOT_TEST)}
          AND {_VALID_EMAIL}
          AND {_DOMAIN} NOT IN {_FREE_MAIL}
          AND SESSION_ID IS NOT NULL
        GROUP BY 1
        ORDER BY SESSIONS DESC
        LIMIT {limit}
    """


def campaign_sources_sql(referrer_limit: int = 8) -> str:
    """Source mix and named referrers for one campaign, one scan, split by KIND."""
    return f"""
        WITH base AS (
            SELECT MESSAGE_ID, REFERER_URL
            FROM {_campaign_table()}
            {_CAMPAIGN_SCOPE.format(not_test=_NOT_TEST)}
        ), grp AS (
            SELECT 'group' AS KIND, {_SOURCE_GROUP} AS LABEL,
                   COUNT(DISTINCT MESSAGE_ID) AS EVENTS
            FROM base GROUP BY 1, 2
        ), ref AS (
            SELECT 'referrer' AS KIND, {_REF_HOST} AS LABEL,
                   COUNT(DISTINCT MESSAGE_ID) AS EVENTS
            FROM base
            WHERE {_SOURCE_GROUP} = 'External'
            GROUP BY 1, 2
            QUALIFY ROW_NUMBER() OVER (ORDER BY EVENTS DESC) <= {referrer_limit}
        )
        SELECT * FROM grp
        UNION ALL SELECT * FROM ref
        ORDER BY KIND, EVENTS DESC
    """


def campaign_ai_sql() -> str:
    """Model mix for one campaign. Only 19% of campaigns reach this query."""
    return f"""
        SELECT
            {_MODEL} AS MODEL,
            COUNT(DISTINCT MESSAGE_ID) AS REQUESTS,
            COUNT(DISTINCT SESSION_ID) AS SESSIONS
        FROM {_campaign_table()}
        {_CAMPAIGN_SCOPE.format(not_test=_NOT_TEST)}
          AND {_AI_REQUEST}
        GROUP BY 1
        ORDER BY REQUESTS DESC
    """
