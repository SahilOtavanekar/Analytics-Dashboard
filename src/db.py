import pandas as pd
import streamlit as st

TABLE_FQN = "CIT_DATA_CORE.TRACKING.INSYTE_TRAKING_EVENTS"

# One hour, up from the five minutes this used to be.
#
# Measured on this warehouse: a bare "SELECT 1" costs ~275ms of round trip, and the
# median dashboard query 550-670ms, so a 12-query page takes ~8-9s cold. Nothing in
# the client can fix that - async submission via collect_nowait() was measured at
# 13.6s against 9.5s sequential, because submission still blocks per statement and
# the X-Small warehouse is single-cluster, so concurrent queries queue rather than
# parallelise.
#
# What does fix it is not going to Snowflake at all. The default window is the last
# 30 days of an append-only table, so an hour of staleness changes almost nothing,
# while a 5-minute TTL expired mid-session and made navigation feel like a cold
# start every time. The sidebar shows the age and offers an explicit refresh.
CACHE_TTL_SECONDS = 3600


# Demand AI's own traffic, used for testing and for its marketing site, is tagged
# with this tenant. Verified as a complete and unambiguous handle before being used
# as one: it is the only spelling in the data (no case or punctuation variants), and
# zero demand_ai events appear outside internal hosts - www.demandai.net, localhost,
# 127.0.0.1 and an Amplify preview branch.
#
# It is NOT a general "internal traffic" rule. ~8,400 events a month sit on
# internal-looking hosts under other tenant IDs, and amplifye.ai is the product
# domain rather than a dev environment - insyte.amplifye.ai serves customer content
# under real customer tenants. Excluding those would remove genuine customer data.
INTERNAL_TENANT = "demand_ai"
EXCLUDE_INTERNAL_KEY = "exclude_internal"


def exclude_internal() -> bool:
    """Is the reader currently excluding Demand AI's own traffic?

    Read from session state rather than a module global: the cache and the Python
    process are shared across viewers in Snowflake, so a module-level flag would let
    one reader's choice change another reader's numbers.
    """
    try:
        return bool(st.session_state[EXCLUDE_INTERNAL_KEY])
    except Exception:
        # No script run in progress (plain import, notebook, test probe) - default
        # to the unfiltered table so nothing silently drops rows.
        return False


def table_fqn() -> str:
    """The table every query reads from, filtered if the reader asked for it.

    Applying the filter here rather than in each WHERE clause is deliberate. All 34
    table references in queries.py go through this function, so one substitution
    reaches every KPI, chart and table - no per-query edits, no change to any
    parameter list, and no chance of a query being missed and quietly reporting
    unfiltered numbers beside filtered ones.

    Because run_query caches on the SQL text, the two states produce different cache
    keys automatically; toggling does not serve stale figures from the other state.
    """
    if not exclude_internal():
        return TABLE_FQN
    return (
        f"(SELECT * FROM {TABLE_FQN} "
        f"WHERE COALESCE(PROPERTIES:tenant_id::STRING, '') <> '{INTERNAL_TENANT}')"
    )


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner="Querying Snowflake...")
def run_query(sql: str, params: list | None = None) -> pd.DataFrame:
    conn = st.connection("snowflake")
    session = conn.session()
    return session.sql(sql, params=params or []).to_pandas()


def clear_cache() -> None:
    """Drop every cached result so the next query goes to Snowflake."""
    run_query.clear()
