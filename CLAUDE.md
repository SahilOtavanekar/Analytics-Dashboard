# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

A Streamlit dashboard over one Snowflake table — `CIT_DATA_CORE.TRACKING.INSYTE_TRAKING_EVENTS`,
~22.6M web-tracking events for InSyte, Demand AI's content platform. Nine pages, every figure
defined in SQL rather than in the UI. Runs locally and as a native **Streamlit in Snowflake** app.

`README.md` is unusually complete — deployment paths, privileges, the upload manifest, and the
measured data caveats live there. Read it before changing behaviour; don't duplicate it here.

## Commands

```bash
.venv\Scripts\activate                   # Windows; repo is developed on Windows
pip install -r requirements.txt          # local only — SiS resolves its own packages
streamlit run streamlit_app.py           # opens :8501, SSO browser tab on first query
```

**There is no test suite and no lint config.** `scripts/test_snowflake_connection.py` is a
standalone connectivity probe, not a test. Verification in this repo is done three ways:

```bash
# 1. Expand a query builder to see the SQL the app actually sends. Reading the f-strings is
#    unreliable — predicates like _LEAD_SUBMIT only reveal themselves once interpolated.
python -c "import sys; sys.path.insert(0,'.'); import src.queries as Q; print(Q.campaign_funnel_sql())"

# 2. Regression-check every builder still expands (catches a broken f-string anywhere):
python -c "import sys,inspect; sys.path.insert(0,'.'); import src.queries as Q; \
[print('FAIL',n) for n in dir(Q) if n.endswith('_sql') and not n.startswith('_') \
 and callable(getattr(Q,n)) and not any(p.default is p.empty for p in inspect.signature(getattr(Q,n)).parameters.values()) \
 and getattr(Q,n)().count('(') != getattr(Q,n)().count(')')]"

# 3. Drive a page headlessly with streamlit.testing.v1.AppTest, patching src.db.run_query with
#    synthetic DataFrames. This is the only way to exercise render code without Snowflake.
```

Rendering can only be confirmed visually. AppTest, Vega specs and contrast maths all pass while
a chart still looks wrong — ask the user for a screenshot rather than iterating blind.

## Architecture

**All SQL lives in `src/queries.py`** — ~50 builders, one function per query, each returning a
string with `?` binds. Shared predicates are module constants (`_LEAD_SUBMIT`, `_PAGE_VIEW`,
`_ENGAGED_EVENT`, `_NOT_TEST`, `_ASSET`, `_ACTION_LABEL`). Change the constant, not the call
sites. Comments record the measurement behind each definition — they are the reasoning, not
decoration; read them before changing a rule.

**`src/db.py` is the single filter point.** `table_fqn()` returns either the table name or a
`(SELECT * FROM … WHERE …)` subquery when the reader has internal traffic excluded. All ~49 table
references route through it (or `_campaign_table()`, which keeps the `demand_ai` tenant for
Campaign Analytics), so one substitution reaches every KPI, chart and table. `run_query` caches on
`(sql, params)` for 1h, so toggling the filter changes the SQL text and therefore the cache key.

**`src/campaign_report.py` contains no Streamlit calls** — verified, zero imports. `collect()`
assembles one campaign's whole report as a dict; `to_pdf()` and `to_html()` render from that same
dict, as does `campaign_detail.render()`. That is what stops the page and its downloads telling
different stories. Keep it Streamlit-free so a scheduled job can call it.

**`src/minipdf.py` is a PDF writer using only `zlib`**, on fpdf2's method surface. It exists
because the deployed runtime cannot install fpdf2 (see constraints).

**`src/theme.py` resolves the palette per mode** via `st.context.theme.type`, degrading to light
outside a script run. `categorical()`, `series_color()`, `surface()` and `readable_on(fill)` are
the API; chart code must never hardcode a colour. `src/charts.py` holds the Altair builders and
calls into it.

`src/prefetch.py:warm()` runs every query once for the sidebar's "Preload all pages", swallowing
failures. `src/components.py` owns `date_range_filter()` (presets, and the internal-traffic
toggle) and `kpi_row()`; both the date range and the toggle use a two-key session-state pattern —
a widget key purged on navigation plus a durable key that survives a page change.

## Constraints that will bite

**The deployed dependency list is closed.** SiS runs the container runtime, whose resolved set is
exactly `python 3.11`, `snowflake-snowpark-python`, `streamlit` (pandas and altair arrive
transitively). Installing anything else needs an External Access Integration this account does not
have. **Any new third-party import breaks the deployment.** This is why charts are Altair and the
PDF is hand-written — not preference.

**The Snowsight Workspaces editor re-indents on paste** and breaks multi-line calls nested inside
an indented block. Keep every statement inside an indented block on a single line; assign
intermediates to locals and hoist long strings to module-level constants used via `.format()`.
Verify with `ast.parse` before handing a file over.

**Event counts use `COUNT(DISTINCT MESSAGE_ID)`, never `COUNT(*)`** — the table holds ~1.29M
byte-identical duplicate rows.

**`COALESCE` before you negate.** `NOT (nullable = 'x')` yields NULL, which drops the row. Several
predicates are COALESCE-guarded specifically so `NOT` over them is safe; a bare `NOT` over a
nullable column has silently deleted whole buckets here.

**Guard `SESSION_ID IS NOT NULL` in any GROUP BY.** ~3.47M rows have no session id (it was not
captured before Nov 2025), and SQL collapses them into one pseudo-session spanning the range.

**Individual identities are never shown.** Company reach aggregates to the email domain and is not
even *queried* below `IDENTITY_FLOOR` (5) identified people. Strip query strings from URLs before
grouping or displaying — `QUERY_PARAMETERS:email` rides in them on personalised links, so a raw
`SEARCH_URL` in a table, PDF or export leaks addresses.

**Colour follows the entity, never its rank** — pin `alt.Scale(domain=…, range=…)` to a fixed
order so a filter that changes series counts cannot repaint the survivors. On-fill text takes
`readable_on(fill)`; the validated palette deliberately spans light and dark slots.

## Deployment

Snowsight **Workspaces → Deploy** is the live path (container runtime, compute pool `DEV`), with
stage + `CREATE STREAMLIT` as the warehouse-runtime fallback. `snowflake.yml`'s `artifacts` list
is inert — Deploy ships the whole folder. `execute_as: OWNER` means the app's execution role is
whatever role **owns** the Streamlit object, so an unresolvable owner fails at bootstrap before any
Python runs; fix it with `GRANT OWNERSHIP … COPY CURRENT GRANTS`, not by editing config.

No secrets are needed inside Snowflake: `st.connection("snowflake")` returns the active Snowpark
session before it ever reads `secrets.toml`, which is local-development only.
