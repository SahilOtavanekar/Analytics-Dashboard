import pandas as pd
import snowflake.connector
import streamlit as st


@st.cache_resource
def get_connection():
    cfg = st.secrets["snowflake"]
    return snowflake.connector.connect(
        account=cfg["account"],
        user=cfg["user"],
        authenticator=cfg.get("authenticator", "externalbrowser"),
        role=cfg["role"],
        warehouse=cfg["warehouse"],
        database=cfg["database"],
        schema=cfg["schema"],
    )


def table_fqn() -> str:
    cfg = st.secrets["snowflake"]
    return f'{cfg["database"]}.{cfg["schema"]}.{cfg["table"]}'


@st.cache_data(ttl=300, show_spinner="Querying Snowflake...")
def run_query(sql: str, params: dict | None = None) -> pd.DataFrame:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(sql, params or {})
    columns = [c[0] for c in cur.description]
    return pd.DataFrame(cur.fetchall(), columns=columns)
