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


def table_fqn() -> str:
    return TABLE_FQN


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner="Querying Snowflake...")
def run_query(sql: str, params: list | None = None) -> pd.DataFrame:
    conn = st.connection("snowflake")
    session = conn.session()
    return session.sql(sql, params=params or []).to_pandas()


def clear_cache() -> None:
    """Drop every cached result so the next query goes to Snowflake."""
    run_query.clear()
