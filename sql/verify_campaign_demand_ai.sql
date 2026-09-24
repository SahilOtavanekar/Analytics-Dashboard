-- Verification queries for the Campaign Analytics drill-down.

-- Campaign : demand_ai_internal_website_track
-- Window   : 2026-08-25 to 2026-09-24   (the Last 1 month preset)

-- ============================================================================
-- Sessions
-- the Sessions card
-- COUNT(DISTINCT SESSION_ID) already skips NULLs, which is the same population the app's
-- sess CTE builds with its WHERE SESSION_ID IS NOT NULL.
-- ============================================================================
SELECT COUNT(DISTINCT SESSION_ID) AS SESSIONS
FROM CIT_DATA_CORE.TRACKING.INSYTE_TRAKING_EVENTS
WHERE EVENT_TS::DATE BETWEEN '2026-08-25' AND '2026-09-24'
  AND CAMPAIGN_ID = 'demand_ai_internal_website_track'
  AND SESSION_ID IS NOT NULL
  AND (EVENT_NAME IS NULL OR LOWER(EVENT_NAME) NOT IN ('test event', 'test download event'));

-- ============================================================================
-- Events / Session
-- the Events / Session card
-- NOTE the two halves come from different populations, which is how the app computes it:
-- EVENTS counts every event in scope INCLUDING those with no session id, while SESSIONS
-- counts only real sessions. So this is all events per sessioned session, not events per
-- session. Immaterial on recent windows - session ids are fully populated from May 2026 -
-- but a range reaching back before Nov 2025 inflates it. Compare the two columns: if
-- EVENTS equals the event total from a SESSION_ID IS NOT NULL run, the ratio is exact.
-- ============================================================================

--MESSAGE_ID is the event's own identifier, assigned by the tracker when the event fires. One event → one MESSAGE_ID. It's the closest thing this table has to a primary key: it has no nulls anywhere.


SELECT COUNT(DISTINCT MESSAGE_ID) AS EVENTS,
       COUNT(DISTINCT SESSION_ID) AS SESSIONS,
       ROUND(COUNT(DISTINCT MESSAGE_ID) * 1.0
             / NULLIF(COUNT(DISTINCT SESSION_ID), 0), 1) AS EVENTS_PER_SESSION
FROM CIT_DATA_CORE.TRACKING.INSYTE_TRAKING_EVENTS
WHERE EVENT_TS::DATE BETWEEN '2026-08-25' AND '2026-09-24'
  AND CAMPAIGN_ID = 'demand_ai_internal_website_track'
  AND (EVENT_NAME IS NULL OR LOWER(EVENT_NAME) NOT IN ('test event', 'test download event'));

-- ============================================================================
-- Events donut
-- source: queries.campaign_event_mix_sql()
-- Three exclusive buckets, AI requests excluded. The BUCKET totals are the ring, their sum is
-- the number in the hole, and EVENTS above minus that sum is the AI figure in the caption.
-- ============================================================================
SELECT           
        CASE
            WHEN COALESCE(LOWER(REPLACE(EVENT_NAME, '_', ' ')), '') = 'form submit' THEN 'Form submit'
            WHEN (COALESCE(EVENT_TYPE, '') = 'page' OR COALESCE(LOWER(EVENT_NAME), '') = 'page visit') THEN 'Page visit'
            ELSE 'Clicks'
        END AS BUCKET,
            COUNT(DISTINCT MESSAGE_ID) AS EVENTS
        FROM CIT_DATA_CORE.TRACKING.INSYTE_TRAKING_EVENTS
        
        WHERE EVENT_TS::DATE BETWEEN '2026-08-25' AND '2026-09-24'
          AND CAMPAIGN_ID = 'demand_ai_internal_website_track'
          AND (EVENT_NAME IS NULL OR LOWER(EVENT_NAME) NOT IN ('test event', 'test download event'))
        GROUP BY 1;

-- ============================================================================
-- Activity over time
-- source: queries.campaign_daily_sql()
-- One row per day; SESSION_COUNT is the bar height.
-- ============================================================================
SELECT
            EVENT_TS::DATE AS EVENT_DATE,
            COUNT(DISTINCT SESSION_ID) AS SESSION_COUNT,
            COUNT(DISTINCT MESSAGE_ID) AS EVENT_COUNT
        FROM CIT_DATA_CORE.TRACKING.INSYTE_TRAKING_EVENTS
        
        WHERE EVENT_TS::DATE BETWEEN '2026-08-25' AND '2026-09-24'
          AND CAMPAIGN_ID = 'demand_ai_internal_website_track'
          AND (EVENT_NAME IS NULL OR LOWER(EVENT_NAME) NOT IN ('test event', 'test download event'))
        GROUP BY EVENT_DATE
        ORDER BY EVENT_DATE;

-- ============================================================================
-- Session quality
-- source: queries.campaign_duration_bands_sql()
-- The duration-band bars. The sentence printed above them - median, mean, instant share
-- and median events - comes from the app's KPI query; the short query at the end of this
-- section reproduces just those four figures.
-- ============================================================================
WITH s AS (
            SELECT SESSION_ID, DATEDIFF('second', MIN(EVENT_TS), MAX(EVENT_TS)) AS DURATION_SECONDS
            FROM CIT_DATA_CORE.TRACKING.INSYTE_TRAKING_EVENTS
            
        WHERE EVENT_TS::DATE BETWEEN '2026-08-25' AND '2026-09-24'
          AND CAMPAIGN_ID = 'demand_ai_internal_website_track'
          AND (EVENT_NAME IS NULL OR LOWER(EVENT_NAME) NOT IN ('test event', 'test download event'))
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
        ORDER BY BAND_ORDER;

-- the four figures in the sentence above the bands
WITH s AS (
    SELECT SESSION_ID,
           DATEDIFF('second', MIN(EVENT_TS), MAX(EVENT_TS)) AS SECS,
           COUNT(DISTINCT MESSAGE_ID) AS EVENTS
    FROM CIT_DATA_CORE.TRACKING.INSYTE_TRAKING_EVENTS
    WHERE EVENT_TS::DATE BETWEEN '2026-08-25' AND '2026-09-24'
      AND CAMPAIGN_ID = 'demand_ai_internal_website_track'
      AND SESSION_ID IS NOT NULL
      AND (EVENT_NAME IS NULL OR LOWER(EVENT_NAME) NOT IN ('test event', 'test download event'))
    GROUP BY SESSION_ID
)
SELECT APPROX_PERCENTILE(SECS, 0.5) / 60.0 AS MEDIAN_DURATION_MINUTES,
       AVG(SECS) / 60.0                    AS MEAN_DURATION_MINUTES,
       SUM(IFF(SECS = 0, 1, 0))            AS INSTANT_SESSIONS,
       APPROX_PERCENTILE(EVENTS, 0.5)      AS MEDIAN_EVENTS,
       COUNT(*)                            AS SESSIONS
FROM s;

-- ============================================================================
-- Where they are
-- source: queries.campaign_geo_sql()
-- Both breakdowns in one scan - filter KIND='region' for the chart, KIND='timezone' for the
-- table beneath it.
-- ============================================================================
WITH base AS (
            SELECT SESSION_ID, TIMEZONE
            FROM CIT_DATA_CORE.TRACKING.INSYTE_TRAKING_EVENTS
            
        WHERE EVENT_TS::DATE BETWEEN '2026-08-25' AND '2026-09-24'
          AND CAMPAIGN_ID = 'demand_ai_internal_website_track'
          AND (EVENT_NAME IS NULL OR LOWER(EVENT_NAME) NOT IN ('test event', 'test download event'))
              AND SESSION_ID IS NOT NULL
        ), region AS (
            SELECT 'region' AS KIND, 
        CASE
            WHEN TIMEZONE IS NULL OR TRIM(TIMEZONE) = '' THEN 'Unknown'
            WHEN TIMEZONE IN ('UTC', 'GMT') OR TIMEZONE ILIKE 'Etc/%' THEN 'Unknown / UTC'
            WHEN POSITION('/' IN TIMEZONE) > 0 THEN SPLIT_PART(TIMEZONE, '/', 1)
            ELSE 'Unknown / UTC'
        END AS LABEL, COUNT(DISTINCT SESSION_ID) AS SESSIONS
            FROM base GROUP BY 1, 2
        ), tz AS (
            SELECT 'timezone' AS KIND, TIMEZONE AS LABEL, COUNT(DISTINCT SESSION_ID) AS SESSIONS
            FROM base
            WHERE TIMEZONE IS NOT NULL AND TRIM(TIMEZONE) <> ''
            GROUP BY 1, 2
            QUALIFY ROW_NUMBER() OVER (ORDER BY SESSIONS DESC) <= 10
        )
        SELECT * FROM region
        UNION ALL SELECT * FROM tz
        ORDER BY KIND, SESSIONS DESC;

-- ============================================================================
-- Pages
-- source: queries.campaign_pages_sql()
-- Query strings are stripped before grouping - QUERY_PARAMETERS:email rides in them on
-- personalised links, so grouping on the raw URL would put addresses in the output.
-- ============================================================================
WITH base AS (
            SELECT SESSION_ID, MESSAGE_ID, EVENT_NAME, EVENT_TYPE, SEARCH_URL,
                   COALESCE(PARSE_URL(SEARCH_URL, 1):scheme::STRING, 'https') AS SCHEME
            FROM CIT_DATA_CORE.TRACKING.INSYTE_TRAKING_EVENTS
            
        WHERE EVENT_TS::DATE BETWEEN '2026-08-25' AND '2026-09-24'
          AND CAMPAIGN_ID = 'demand_ai_internal_website_track'
          AND (EVENT_NAME IS NULL OR LOWER(EVENT_NAME) NOT IN ('test event', 'test download event'))
              AND SESSION_ID IS NOT NULL
        ), all_sessions AS (
            SELECT COUNT(DISTINCT SESSION_ID) AS N FROM base
        ), grouped AS (
            SELECT
                COALESCE(PARSE_URL(SEARCH_URL, 1):host::STRING, '') AS HOST,
                '/' || COALESCE(PARSE_URL(SEARCH_URL, 1):path::STRING, '') AS PATH,
                COALESCE(PARSE_URL(SEARCH_URL, 1):host::STRING, '') || '/' || COALESCE(PARSE_URL(SEARCH_URL, 1):path::STRING, '') AS PAGE_URL,
                MODE(SCHEME) AS SCHEME,
                COUNT(DISTINCT SESSION_ID) AS SESSIONS,
                COUNT(DISTINCT MESSAGE_ID) AS VIEWS,
                ROUND(COUNT(DISTINCT MESSAGE_ID) * 1.0
                      / NULLIF(COUNT(DISTINCT SESSION_ID), 0), 1) AS VIEWS_PER_SESSION,
                COALESCE((COUNT(DISTINCT SESSION_ID) * 100.0
                    / NULLIF((SELECT N FROM all_sessions), 0))::FLOAT, 0) AS PCT_OF_SESSIONS
            FROM base
            WHERE (COALESCE(EVENT_TYPE, '') = 'page' OR COALESCE(LOWER(EVENT_NAME), '') = 'page visit')
              AND SEARCH_URL IS NOT NULL
              AND (COALESCE(PARSE_URL(SEARCH_URL, 1):host::STRING, '') NOT IN ('localhost', '127.0.0.1') AND COALESCE(PARSE_URL(SEARCH_URL, 1):host::STRING, '') <> '')
            GROUP BY 1, 2, 3
        )
        SELECT
            IFF(COUNT(*) OVER (PARTITION BY PATH) > 1, PAGE_URL, PATH) AS PAGE,
            SESSIONS,
            VIEWS,
            VIEWS_PER_SESSION,
            PCT_OF_SESSIONS,
            HOST,
            SCHEME || '://' || HOST || PATH AS URL
        FROM grouped
        ORDER BY SESSIONS DESC
        LIMIT 12;

-- ============================================================================
-- How they arrived
-- source: queries.campaign_sources_sql()
-- Also two-in-one: KIND='group' is the source mix, KIND='referrer' the external referrers.
-- ============================================================================
WITH base AS (
            SELECT MESSAGE_ID, REFERER_URL
            FROM CIT_DATA_CORE.TRACKING.INSYTE_TRAKING_EVENTS
            
        WHERE EVENT_TS::DATE BETWEEN '2026-08-25' AND '2026-09-24'
          AND CAMPAIGN_ID = 'demand_ai_internal_website_track'
          AND (EVENT_NAME IS NULL OR LOWER(EVENT_NAME) NOT IN ('test event', 'test download event'))
        ), grp AS (
            SELECT 'group' AS KIND, 
        CASE
            WHEN COALESCE(REFERER_URL, '') = '' THEN 'Direct / none'
            WHEN PARSE_URL(REFERER_URL, 1):host::STRING IN ('localhost', '127.0.0.1')
              OR PARSE_URL(REFERER_URL, 1):host::STRING ILIKE '%amplifye.ai'
              OR PARSE_URL(REFERER_URL, 1):host::STRING ILIKE '%demandai.net'
              OR PARSE_URL(REFERER_URL, 1):host::STRING ILIKE '%atlassian.net'
              OR PARSE_URL(REFERER_URL, 1):host::STRING ILIKE '%amplifyapp.com'
              OR PARSE_URL(REFERER_URL, 1):host::STRING ILIKE '%amazonaws.com'
              OR PARSE_URL(REFERER_URL, 1):host::STRING ILIKE '%cloudfront.net' THEN 'Internal / dev'
            WHEN PARSE_URL(REFERER_URL, 1):host::STRING ILIKE '%officeapps.live.com'
              OR PARSE_URL(REFERER_URL, 1):host::STRING ILIKE '%sharepoint.com'
              OR PARSE_URL(REFERER_URL, 1):host::STRING ILIKE '%office.net'
              OR PARSE_URL(REFERER_URL, 1):host::STRING ILIKE '%microsoft.com' THEN 'Email / Office'
            ELSE 'External'
        END AS LABEL,
                   COUNT(DISTINCT MESSAGE_ID) AS EVENTS
            FROM base GROUP BY 1, 2
        ), ref AS (
            SELECT 'referrer' AS KIND, PARSE_URL(REFERER_URL, 1):host::STRING AS LABEL,
                   COUNT(DISTINCT MESSAGE_ID) AS EVENTS
            FROM base
            WHERE 
        CASE
            WHEN COALESCE(REFERER_URL, '') = '' THEN 'Direct / none'
            WHEN PARSE_URL(REFERER_URL, 1):host::STRING IN ('localhost', '127.0.0.1')
              OR PARSE_URL(REFERER_URL, 1):host::STRING ILIKE '%amplifye.ai'
              OR PARSE_URL(REFERER_URL, 1):host::STRING ILIKE '%demandai.net'
              OR PARSE_URL(REFERER_URL, 1):host::STRING ILIKE '%atlassian.net'
              OR PARSE_URL(REFERER_URL, 1):host::STRING ILIKE '%amplifyapp.com'
              OR PARSE_URL(REFERER_URL, 1):host::STRING ILIKE '%amazonaws.com'
              OR PARSE_URL(REFERER_URL, 1):host::STRING ILIKE '%cloudfront.net' THEN 'Internal / dev'
            WHEN PARSE_URL(REFERER_URL, 1):host::STRING ILIKE '%officeapps.live.com'
              OR PARSE_URL(REFERER_URL, 1):host::STRING ILIKE '%sharepoint.com'
              OR PARSE_URL(REFERER_URL, 1):host::STRING ILIKE '%office.net'
              OR PARSE_URL(REFERER_URL, 1):host::STRING ILIKE '%microsoft.com' THEN 'Email / Office'
            ELSE 'External'
        END = 'External'
            GROUP BY 1, 2
            QUALIFY ROW_NUMBER() OVER (ORDER BY EVENTS DESC) <= 8
        )
        SELECT * FROM grp
        UNION ALL SELECT * FROM ref
        ORDER BY KIND, EVENTS DESC;

-- ============================================================================
-- AI usage
-- source: queries.campaign_ai_sql()
-- Model mix. Empty unless the campaign has AI activity.
-- ============================================================================
SELECT
            
        CASE
            WHEN PROPERTIES:llm::STRING ILIKE '%opus-4-6%'   THEN 'Claude Opus 4.6'
            WHEN PROPERTIES:llm::STRING ILIKE '%sonnet-4-6%' THEN 'Claude Sonnet 4.6'
            WHEN PROPERTIES:llm::STRING ILIKE '%3-5-sonnet%' THEN 'Claude 3.5 Sonnet'
            WHEN PROPERTIES:llm IS NULL
              OR LOWER(PROPERTIES:llm::STRING) IN ('undefined', 'null', '') THEN 'Unrecorded'
            ELSE REGEXP_REPLACE(PROPERTIES:llm::STRING, '^[a-z]{2}\\.', '')
        END AS MODEL,
            COUNT(DISTINCT MESSAGE_ID) AS REQUESTS,
            COUNT(DISTINCT SESSION_ID) AS SESSIONS
        FROM CIT_DATA_CORE.TRACKING.INSYTE_TRAKING_EVENTS
        
        WHERE EVENT_TS::DATE BETWEEN '2026-08-25' AND '2026-09-24'
          AND CAMPAIGN_ID = 'demand_ai_internal_website_track'
          AND (EVENT_NAME IS NULL OR LOWER(EVENT_NAME) NOT IN ('test event', 'test download event'))
          AND COALESCE(PROPERTIES:ml_request::STRING, '') = 'true'
        GROUP BY 1
        ORDER BY REQUESTS DESC;
