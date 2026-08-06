import pandas as pd
import streamlit as st

TABLE_FQN = "CIT_DATA_CORE.TRACKING.INSYTE_TRAKING_EVENTS"


def table_fqn() -> str:
    return TABLE_FQN


@st.cache_data(ttl=300, show_spinner="Querying Snowflake...")
def run_query(sql: str, params: list | None = None) -> pd.DataFrame:
    conn = st.connection("snowflake")
    session = conn.session()
    return session.sql(sql, params=params or []).to_pandas()
