"""
One-off connectivity check: confirms access to the target table via your
existing SSO login (external browser auth - no keys/admin setup needed) and
prints the real schema so the dashboard's queries can be built against it.

Run manually: python scripts/test_snowflake_connection.py
A browser tab will open once for you to complete your normal Okta/SSO login.
"""

import snowflake.connector

# Fill these in from your own .streamlit/secrets.toml
ACCOUNT = "your_account_identifier"
USER = "your_username@yourcompany.com"
ROLE = "R_CIT_DATA_READ"
WAREHOUSE = "SNOWFLAKE_LEARNING_WH"
DATABASE = "CIT_DATA_CORE"
SCHEMA = "TRACKING"
TABLE = "INSYTE_TRAKING_EVENTS"

conn = snowflake.connector.connect(
    account=ACCOUNT,
    user=USER,
    authenticator="externalbrowser",
    role=ROLE,
    warehouse=WAREHOUSE,
    database=DATABASE,
    schema=SCHEMA,
)

try:
    cur = conn.cursor()

    print(f"\n=== Warehouses visible to role {ROLE} ===")
    cur.execute("SHOW WAREHOUSES")
    for row in cur.fetchall():
        print(f"  {row[0]}")

    print(f"\n=== Explicitly setting role/warehouse ===")
    cur.execute(f"USE ROLE {ROLE}")
    print(f"  USE ROLE {ROLE} - ok")
    cur.execute(f"USE WAREHOUSE {WAREHOUSE}")
    print(f"  USE WAREHOUSE {WAREHOUSE} - ok")

    print(f"\n=== Columns: {DATABASE}.{SCHEMA}.{TABLE} ===")
    cur.execute(f"DESCRIBE TABLE {DATABASE}.{SCHEMA}.{TABLE}")
    for row in cur.fetchall():
        print(f"  {row[0]:<40} {row[1]}")

    print(f"\n=== Row count ===")
    cur.execute(f"SELECT COUNT(*) FROM {DATABASE}.{SCHEMA}.{TABLE}")
    print(f"  {cur.fetchone()[0]:,}")

    print(f"\n=== Sample rows (5) ===")
    cur.execute(f"SELECT * FROM {DATABASE}.{SCHEMA}.{TABLE} LIMIT 5")
    columns = [c[0] for c in cur.description]
    print(f"  {columns}")
    for row in cur.fetchall():
        print(f"  {row}")

finally:
    conn.close()
