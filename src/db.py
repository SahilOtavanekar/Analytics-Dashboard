import pandas as pd
import snowflake.connector
import streamlit as st


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
    cfg = st.secrets["snowflake"]
    return f'{cfg["database"]}.{cfg["schema"]}.{cfg["table"]}'


@st.cache_data(ttl=300, show_spinner="Querying Snowflake...")
def run_query(sql: str, params: dict | None = None) -> pd.DataFrame:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(sql, params or {})
    columns = [c[0] for c in cur.description]
    return pd.DataFrame(cur.fetchall(), columns=columns)
