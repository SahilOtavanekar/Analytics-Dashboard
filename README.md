# Analytics Dashboard

A Streamlit dashboard visualizing user tracking events from
`CIT_DATA_CORE.TRACKING.INSYTE_TRAKING_EVENTS` in Snowflake.

## Pages

1. **Executive Dashboard** — Total Events, Total Sessions, Total Campaigns
2. **Event Analytics** — Top Events (ranked bar) and Event Type Share (100%-stacked bar)
3. **Session Analytics** — session KPIs, Sessions Over Time, Session Duration Distribution
4. **User Activity** — visitor activity as an IP/session-based proxy (this data has no
   persistent user ID), with automatic detection of outlier/bot-like traffic
5. **Campaign Analytics** — Top Campaigns (by resolved campaign name) and Traffic by Channel

Every chart has a "View as table" expander for the underlying data.

## Setup

**1. Create and activate a virtual environment**

```
python -m venv .venv
.venv\Scripts\activate
```

**2. Install dependencies**

```
pip install -r requirements.txt
```

**3. Configure Snowflake access**

Copy the example secrets file and adjust if your account/role/warehouse differ:

```
copy .streamlit\secrets.toml.example .streamlit\secrets.toml
```

Authentication uses Snowflake's **external browser (SSO)** flow — no password or key
file is stored. The first query in a session opens a browser tab for you to complete
your normal login.

**4. Run the app**

```
streamlit run streamlit_app.py
```

The app opens at `http://localhost:8501`.

## Project structure

```
streamlit_app.py            Landing page with navigation
pages/                       One file per dashboard page (numbered for sidebar order)
src/
  db.py                      Cached Snowflake connection + run_query() helper
  queries.py                 All SQL, one function per query
  charts.py                  Reusable Plotly chart builders (bar, stacked bar, line, histogram)
  theme.py                   Shared color palette and chart chrome constants
  components.py               Shared UI widgets (KPI row, date range filter)
.streamlit/
  secrets.toml.example       Template for connection config (committed)
  secrets.toml               Your local config (gitignored)
scripts/
  test_snowflake_connection.py   Standalone connectivity/schema check, not part of the app
```

## Notes on the data

- **No persistent user/visitor ID exists** in the source table. The User Activity page
  is explicit about this and uses `REQUEST_IP` + `SESSION_ID` as a proxy, not verified
  identity.
- **`CAMPAIGN_ID` is often an opaque UUID.** Campaign Analytics resolves a human-readable
  label via `MODE(PROPERTIES:campaign_name)`, falling back to the raw ID when no name
  exists in the data.
- Session duration and sessions-per-visitor are both heavily right-skewed (most sessions
  are very short, with a long tail of outliers) — their charts clip the *visible* axis
  range to the 95th percentile so the shape is readable; full data remains in the table
  view.
