import pandas as pd
import snowflake.connector
import streamlit as st

TABLE_FQN = "CIT_DATA_CORE.TRACKING.INSYTE_TRAKING_EVENTS"


def _get_sis_session_connection():
    """Return the raw connection behind the active Snowpark session when
    running inside Streamlit in Snowflake, or None otherwise (local dev,
    Community Cloud)."""
    try:
        from snowflake.snowpark.context import get_active_session

        session = get_active_session()
    except Exception:
        return None
    return session.connection if hasattr(session, "connection") else session._conn._conn


def _load_private_key_der(pem_text: str) -> bytes:
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives import serialization

    key = serialization.load_pem_private_key(
        pem_text.encode(), password=None, backend=default_backend()
    )
    return key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


@st.cache_resource
def get_connection():
    sis_conn = _get_sis_session_connection()
    if sis_conn is not None:
        return sis_conn

    cfg = st.secrets["snowflake"]

    # Headless hosts (e.g. Streamlit Community Cloud) have no browser to complete
    # external-browser SSO, so key-pair auth is used there instead when a private
    # key is present in secrets. Local dev keeps using SSO.
    if "private_key" in cfg:
        return snowflake.connector.connect(
            account=cfg["account"],
            user=cfg["user"],
            private_key=_load_private_key_der(cfg["private_key"]),
            role=cfg["role"],
            warehouse=cfg["warehouse"],
            database=cfg["database"],
            schema=cfg["schema"],
        )

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
    return TABLE_FQN


@st.cache_data(ttl=300, show_spinner="Querying Snowflake...")
def run_query(sql: str, params: dict | None = None) -> pd.DataFrame:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(sql, params or {})
    columns = [c[0] for c in cur.description]
    return pd.DataFrame(cur.fetchall(), columns=columns)
