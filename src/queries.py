from src.db import table_fqn


def executive_kpis_sql() -> str:
    return f"""
        SELECT
            COUNT(*) AS total_events,
            COUNT(DISTINCT SESSION_ID) AS total_sessions,
            COUNT(DISTINCT CAMPAIGN_ID) AS total_campaigns
        FROM {table_fqn()}
        WHERE EVENT_TS::DATE BETWEEN %(start_date)s AND %(end_date)s
    """


def top_events_sql(limit: int = 10) -> str:
    return f"""
        SELECT EVENT_NAME, COUNT(*) AS EVENT_COUNT
        FROM {table_fqn()}
        WHERE EVENT_TS::DATE BETWEEN %(start_date)s AND %(end_date)s
        GROUP BY EVENT_NAME
        ORDER BY EVENT_COUNT DESC
        LIMIT {limit}
    """


def event_type_share_sql() -> str:
    return f"""
        SELECT EVENT_TYPE, COUNT(*) AS EVENT_COUNT
        FROM {table_fqn()}
        WHERE EVENT_TS::DATE BETWEEN %(start_date)s AND %(end_date)s
        GROUP BY EVENT_TYPE
        ORDER BY EVENT_COUNT DESC
    """


def _session_summary_cte() -> str:
    return f"""
        SELECT
            SESSION_ID,
            COUNT(*) AS EVENT_COUNT,
            DATEDIFF('second', MIN(EVENT_TS), MAX(EVENT_TS)) AS DURATION_SECONDS
        FROM {table_fqn()}
        WHERE EVENT_TS::DATE BETWEEN %(start_date)s AND %(end_date)s
        GROUP BY SESSION_ID
    """


def session_kpis_sql() -> str:
    return f"""
        SELECT
            COUNT(*) AS TOTAL_SESSIONS,
            COALESCE((AVG(EVENT_COUNT))::FLOAT, 0) AS AVG_EVENTS_PER_SESSION,
            COALESCE((AVG(DURATION_SECONDS) / 60.0)::FLOAT, 0) AS AVG_DURATION_MINUTES
        FROM ({_session_summary_cte()})
    """


def sessions_over_time_sql() -> str:
    return f"""
        SELECT
            EVENT_TS::DATE AS EVENT_DATE,
            COUNT(DISTINCT SESSION_ID) AS SESSION_COUNT
        FROM {table_fqn()}
        WHERE EVENT_TS::DATE BETWEEN %(start_date)s AND %(end_date)s
        GROUP BY EVENT_DATE
        ORDER BY EVENT_DATE
    """


def session_durations_sql() -> str:
    return f"""
        SELECT (DURATION_SECONDS / 60.0)::FLOAT AS DURATION_MINUTES
        FROM ({_session_summary_cte()})
    """


def _visitor_summary_cte() -> str:
    # No persistent user/visitor ID exists in this data; REQUEST_IP is the
    # closest proxy for "who", acknowledging it can be shared (NAT, VPN, bots).
    return f"""
        SELECT
            REQUEST_IP,
            COUNT(DISTINCT SESSION_ID) AS SESSION_COUNT,
            COUNT(DISTINCT EVENT_TS::DATE) AS ACTIVE_DAYS
        FROM {table_fqn()}
        WHERE EVENT_TS::DATE BETWEEN %(start_date)s AND %(end_date)s
        GROUP BY REQUEST_IP
    """


def user_activity_kpis_sql() -> str:
    return f"""
        SELECT
            COUNT(*) AS TOTAL_VISITORS,
            COALESCE(
                (SUM(CASE WHEN ACTIVE_DAYS > 1 THEN 1 ELSE 0 END) * 100.0 / NULLIF(COUNT(*), 0))::FLOAT, 0
            ) AS PCT_RETURNING,
            COALESCE((AVG(SESSION_COUNT))::FLOAT, 0) AS AVG_SESSIONS_PER_VISITOR
        FROM ({_visitor_summary_cte()})
    """


def sessions_per_visitor_sql() -> str:
    return f"""
        SELECT SESSION_COUNT::FLOAT AS SESSION_COUNT
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
            COALESCE(
                (COUNT(*) / NULLIF(COUNT(DISTINCT CAMPAIGN_ID), 0))::FLOAT, 0
            ) AS AVG_EVENTS_PER_CAMPAIGN
        FROM {table_fqn()}
        WHERE EVENT_TS::DATE BETWEEN %(start_date)s AND %(end_date)s
    """


def top_campaigns_sql(limit: int = 10) -> str:
    # CAMPAIGN_ID is the stable key, but it's often an opaque UUID; PROPERTIES:campaign_name
    # is only ~62% populated and occasionally drifts (renames/whitespace), so MODE() picks
    # the most common label per campaign and we fall back to the raw ID when no name exists.
    return f"""
        SELECT
            COALESCE(MODE(PROPERTIES:campaign_name::STRING), CAMPAIGN_ID) AS CAMPAIGN_LABEL,
            COUNT(*) AS EVENT_COUNT
        FROM {table_fqn()}
        WHERE EVENT_TS::DATE BETWEEN %(start_date)s AND %(end_date)s
        GROUP BY CAMPAIGN_ID
        ORDER BY EVENT_COUNT DESC
        LIMIT {limit}
    """


def channel_share_sql() -> str:
    return f"""
        SELECT CHANNEL, COUNT(*) AS EVENT_COUNT
        FROM {table_fqn()}
        WHERE EVENT_TS::DATE BETWEEN %(start_date)s AND %(end_date)s
        GROUP BY CHANNEL
        ORDER BY EVENT_COUNT DESC
    """
