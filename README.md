# Analytics Dashboard

A Streamlit dashboard over `CIT_DATA_CORE.TRACKING.INSYTE_TRAKING_EVENTS` — roughly
22.6M web-tracking events for InSyte, Demand AI's content platform. Nine pages, all
driven from one table, with every figure defined in SQL rather than in the UI.

Runs both locally and as a native **Streamlit in Snowflake** app.

## Pages

| Page | What it answers |
|---|---|
| **Executive Dashboard** | Sessions, content reach, AI attach, identified accounts, lead conversion — plus sessions over time |
| **Session Analytics** | How long sessions last and how many events they carry; duration bands and percentiles |
| **Audience** | Identified vs anonymous, top companies by domain, timezone regions, language, and an IP-based proxy for anonymous activity |
| **Campaign Analytics** | Campaigns ranked by sessions, and a full per-campaign breakdown you can open, share or download |
| **Conversion** | Session funnel, form performance, and action reach |
| **Pages & Sources** | Top pages, traffic source mix, external referrers |
| **Content Performance** | Whether opening content correlates with converting; reach per asset and PDF read depth |
| **AI Usage** | Model mix, model migration over time, and whether AI sessions behave differently |
| **Tenant Breakdown** | Per-tenant comparison, and why the blended platform averages mislead |

Most charts have a **View as table** expander — a tooltip cannot be screenshotted or
pasted into a deck, and these are the numbers people quote.

## The campaign drill-down

Campaign Analytics is the deepest page:

- **Click a bar** — or search every campaign in the range from the picker — to replace the
  ranking with one campaign's full breakdown: activity over time, session quality,
  engagement funnel, geography, content, accounts reached, sources and AI usage.
- **Sections are gated on measured availability.** A section with no data says why in one
  line instead of drawing an empty axis — an empty axis reads as "zero", which is a
  stronger and different claim than "not measured here".
- **`?campaign_id=<id>`** opens one campaign directly, so a single campaign's view can be
  shared. Only the ID travels; the recipient's own date range applies, and the header says
  so outright.
- **Download report (PDF)** is built from the same `collect()` call the page renders from,
  so a saved file and the screen cannot disagree about one campaign.
- **`?campaign_id=<id>&pdf=true`** downloads that PDF on open — a link you can mail to
  someone who only wants the report. The visible button stays as the fallback.

## Things worth knowing before reading the numbers

**Internal-traffic filter.** A sidebar toggle removes Demand AI's own traffic on two
counts: content under the `demand_ai` tenant, and visits from `demandai.co` addresses
(staff browsing customers' content). It is row-level and applied once in
`db.table_fqn()`, so all 48 query builders inherit it. Campaign Analytics keeps the
*tenant* half — a list *of* campaigns should include Demand AI's own — while still
dropping staff visitors. Which rule is active is stated in the page body, so a
screenshot carries the caveat with it.

**Engagement excludes consent.** Dismissing a cookie banner is not engagement with a
campaign. Consent is recorded three separate ways — a `cookie-form` submit, a click
carrying `attributes:consent`, and a `consent-response` event — and all three are
excluded from Engagement and from Conversion, then reported as a plain sentence instead.

**Conversion counts lead forms only.** The consent banner fires the same `form_submit`
event as a real form, as do unsubscribe and comment forms. Those are excluded by pattern
rather than by literal ID, because the data already carries several spellings of each.

**Individual identities are never shown.** Company reach is aggregated to the email
domain, and below **five** identified people the breakdown is not even *queried* — so it
cannot reach a table, a PDF or an export by accident.

**Results are cached for an hour.** The sidebar shows the age and offers both a refresh
and a "Preload all pages" pass that warms every page's queries in one go.

**Date presets** are whole calendar months — 1, 3, 6, 12, 24 — clipped to the start of
tracking. A wide range pushes the "vs previous" comparison off the back of the data, so
the page says when that delta overstates growth rather than leaving it to be worked out.

## Setup

**1. Virtual environment**

```
python -m venv .venv
.venv\Scripts\activate
```

**2. Dependencies** (Python 3.11)

```
pip install -r requirements.txt
```

**3. Snowflake access**

```
copy .streamlit\secrets.toml.example .streamlit\secrets.toml
```

Local development uses Snowflake's **external browser (SSO)** flow — no password or key
file is stored. The first query of a session opens a browser tab for your normal login.

**4. Run**

```
streamlit run streamlit_app.py
```

Opens at `http://localhost:8501`.

## Running in Streamlit in Snowflake

Two paths work. **Workspaces Deploy** is the live one and the primary loop; **stage +
`CREATE STREAMLIT`** is the fallback, and the two use different runtimes with different
package rules. Both are verified — the notes under each are measured, not assumed.

**No secrets file is needed inside Snowflake.** `st.connection("snowflake")` resolves the
active session, so `secrets.toml` is a local-development concern only.

### Prerequisites

Privileges on the target role (`R_CIT_DATA_ADMIN` here):

| Privilege | On | Why |
|---|---|---|
| `CREATE STREAMLIT` | schema `CIT_DATA_CORE.TRACKING` | to create the app object |
| `USAGE` | warehouse `COMPUTE_WH` | where the app's SQL runs |
| `USAGE` | compute pool `DEV` | container runtime only (path A) |
| `SELECT` | `CIT_DATA_CORE.TRACKING.INSYTE_TRAKING_EVENTS` | the data |

### What goes in the workspace

Twenty-four files. This is the deploy manifest — every file the app actually reaches — and
it is deliberately smaller than the repo; see [Project structure](#project-structure) for
the full tree. Both paths need this same set, path B just puts it on a stage instead.

```
Analytics_Dashboard/
├── streamlit_app.py              main_file, named in snowflake.yml
├── pyproject.toml                required - Deploy fails without it
├── snowflake.yml                 app identifier, warehouse, compute pool
├── environment.yml               path B only; harmless on path A
├── .streamlit/
│   └── config.toml               theme; without it you get default styling
├── pages/                        9 files - names must match streamlit_app.py
│   ├── 1_Executive_Dashboard.py
│   ├── 3_Session_Analytics.py
│   ├── 4_Audience.py
│   ├── 5_Campaign_Analytics.py
│   ├── 6_Conversion.py
│   ├── 7_Pages_and_Sources.py
│   ├── 8_Content_Performance.py
│   ├── 9_AI_Usage.py
│   └── 10_Tenant_Breakdown.py
└── src/                          10 files - the whole import graph
    ├── __init__.py
    ├── campaign_detail.py
    ├── campaign_report.py
    ├── charts.py
    ├── components.py
    ├── db.py
    ├── minipdf.py
    ├── prefetch.py
    ├── queries.py
    └── theme.py
```

`pyproject.toml` is required **even though it only requests `streamlit[snowflake]`**.
Deleting it fails with *"Installing dependencies failed because the pyproject.toml file
does not exist"*.

Everything else in the repo is deliberately left out:

| Left out | Why |
|---|---|
| `.streamlit/secrets.toml` and `.example` | Ignored inside Snowflake — `st.connection` resolves the active session before it ever reads secrets, so uploading the real one ships credentials for no benefit |
| `requirements.txt` | Local and Community Cloud only; the container runtime reads `pyproject.toml` |
| `campaign_report_owners.csv` | No app code reads it — it seeds the planned scheduled delivery |
| `notebooks/`, `scripts/`, `sql/`, `keys/` | Not imported by the app, and `keys/` holds private keys |
| `README.md`, `Streamlit-dashboard.docx` | Documentation |
| `.venv/`, `__pycache__/`, `.gitignore` | Never |

Two things that catch people:

- **`src/campaign_report.py` and `src/minipdf.py` are easy to miss.** `minipdf` is imported
  lazily inside `campaign_report`, so omitting either breaks *export* rather than load —
  the deployment looks healthy until someone clicks **Download report**.
- **Don't renumber `pages/`.** The gap at `2_` is expected. The numbers are vestigial, but
  the filename strings must match `streamlit_app.py` character-for-character or the page
  404s.

### Path A — Snowsight Workspaces Deploy (the live deployment)

1. In Snowsight, open **Projects → Workspaces**.
2. Create a workspace (or open the existing `Analytics_Dashboard` one) and get the repo
   files into it — from a connected Git repo, or by uploading the folder.
3. Confirm `snowflake.yml` matches the target you want:

   ```yaml
   entities:
     streamlit_app:
       type: streamlit
       identifier:
         database: CIT_DATA_CORE
         schema: TRACKING
         name: ANALYTICS_DASHBOARD
       query_warehouse: COMPUTE_WH
       compute_pool: DEV
       main_file: streamlit_app.py
   ```

4. Click **Deploy**.
5. Open the app from **Projects → Streamlit**.

**Deploy ships the whole folder.** It ignores `snowflake.yml`'s `artifacts` list entirely
— the committed list names a nonexistent `srs.txt` and omits `pages/` and `src/`, yet
every page deploys and imports from `src` correctly. Don't spend time curating that list.

**The app's source is the workspace, not a stage.** Deploy links the app to
`snow://workspace/"USER$"."PUBLIC"."DEFAULT$"/versions/live/Analytics_Dashboard/` and
versions it (`VERSION$1`, `LAST`). `ANALYTICS_DASHBOARD_STAGE` is vestigial on this path.

**To update:** change the files in the workspace and press **Deploy** again.

### Verify what you actually got

```sql
DESCRIBE STREAMLIT CIT_DATA_CORE.TRACKING.ANALYTICS_DASHBOARD;
```

Read three fields:

- `runtime_name` — `SYSTEM$ST_CONTAINER_RUNTIME_PY3_11` means the container runtime
- `compute_pool` — `DEV`
- `query_warehouse` — `COMPUTE_WH`

Note that **`run_mode: WarehouseOnly` in `snowflake.yml` is ignored by Deploy** — the
`compute_pool` wins. So cost splits two ways: queries bill to the warehouse, the app
itself to the pool. `idle_auto_shutdown_time_seconds: 86400` keeps the app resident for a
full day after last use; lower it if pool cost matters.

### The container runtime's dependency list is closed

The resolved package set is exactly `python==3.11.*`, `snowflake-snowpark-python`,
`streamlit` — with `pandas` and `altair` arriving transitively. The runtime installs from
`pyproject.toml` via **PyPI, which needs an External Access Integration this account does
not have**, so:

> **Any new third-party import will break the deployment.**

This is not a style preference. It is why the charts are Altair (bundled with Streamlit)
rather than Plotly, and why the campaign PDF is written by `src/minipdf.py` from the
standard library rather than by `fpdf2`.

Measured on the running app: streamlit **1.60.0**, python **3.11.15**, pandas **2.3.3**,
altair **6.2.2**.

### Path B — stage + `CREATE STREAMLIT` (warehouse runtime)

Use this if you need the warehouse runtime, where packages come from `environment.yml`
via the Snowflake Anaconda channel and no External Access Integration is involved.

1. Upload the files to the stage — **Data → Databases → CIT_DATA_CORE → TRACKING →
   Stages → ANALYTICS_DASHBOARD_STAGE → + Files**. Set the *path* field per directory so
   `pages/`, `src/` and `.streamlit/` keep their layout; a flat upload will not import.
2. Verify the layout survived:

   ```sql
   LS @CIT_DATA_CORE.TRACKING.ANALYTICS_DASHBOARD_STAGE;
   ```

3. Create the app (also in `sql/deploy_warehouse_app.sql`):

   ```sql
   CREATE OR REPLACE STREAMLIT CIT_DATA_CORE.TRACKING.ANALYTICS_DASHBOARD
     ROOT_LOCATION = '@CIT_DATA_CORE.TRACKING.ANALYTICS_DASHBOARD_STAGE'
     MAIN_FILE = 'streamlit_app.py'
     QUERY_WAREHOUSE = COMPUTE_WH;
   ```

**To update:** re-upload the changed files, then close and reopen the app — a
`ROOT_LOCATION` app re-reads the stage on each new session. Re-running the `CREATE`
statement is only needed if something seems cached, and **`CREATE OR REPLACE` drops the
object's grants**, so re-grant afterwards if anyone else has `USAGE`.

`ROOT_LOCATION` is the legacy form: no multi-file editing in Snowsight (the classic
editor writes only `streamlit_app.py`, straight to the stage), no Git integration, and
`ALTER STREAMLIT` limited to `SET`/`UNSET`/`RENAME`.

### Known traps

- **There is no supported way to sync workspace files to a stage.** Workspace files live
  in an internal user-specific database with no documented export path, so the two paths
  above are genuinely separate — you cannot deploy via Workspaces and then reuse those
  files for path B.
- **`snow streamlit deploy` (CLI) is not used here.** An attempt bundled `pages/`, `src/`
  and `.streamlit/` as *empty* directories.
- **`SQL compilation error: Missing MAIN_FILE`** has appeared once from Deploy and then
  the same unmodified `snowflake.yml` deployed fine. If you see it, retry before
  debugging the config.
- **The Snowsight editor re-indents multi-line calls on paste** and breaks them. If you
  edit in the browser, keep calls on one line and verify before saving.
- **Notebooks have a separate environment** from the app — they carry altair, pandas and
  snowpark but *no streamlit*, so packages must be satisfied twice. Notebook chart cells
  also need `alt.renderers.enable("mimetype")` in the setup cell.
- **The Snowflake clock can lag your local date** by up to a day, which is why the date
  picker pads its `max_value`.

## Other hosting

For headless hosting outside Snowflake (e.g. Streamlit Community Cloud) there is no
browser to complete SSO, so key-pair auth is required; the variant is commented in
`.streamlit/secrets.toml.example`. Never commit `secrets.toml` or anything under `keys/`
— both are gitignored.

## Project structure

```
streamlit_app.py             Router. st.navigation with explicit labels and order
pages/                       One file per page. The file numbers are vestigial - order
                             comes from streamlit_app.py, and there is no page 2
src/
  db.py                      Connection, 1h query cache, and table_fqn() - the single
                             place the internal-traffic filter is applied
  queries.py                 All SQL, one function per query, with the measurements
                             behind each definition recorded in comments
  charts.py                  Altair builders (ranked bar, stacked share, trend line,
                             trend bar, funnel, diverging bar)
  components.py              Shared UI: KPI row, date range + presets, preload, and
                             auto_download() for the ?pdf=true link
  campaign_report.py         One campaign's report, assembled with NO Streamlit calls,
                             so the page and any scheduled job render from one source
  campaign_detail.py         Presentation for the drill-down; renders from collect()
  minipdf.py                 PDF writer, standard library only, on fpdf2's method surface
  prefetch.py                Warms every page's queries in one pass
  theme.py                   Palette and chart chrome, matched to demandai.net
.streamlit/
  config.toml                Theme (light + dark), chart palette, Inter
  secrets.toml.example       Connection template (committed)
  secrets.toml               Local config (gitignored)
sql/deploy_warehouse_app.sql Warehouse-runtime deployment statement
scripts/                     Standalone connectivity check, not part of the app
notebooks/                   Exploratory analysis of the source table
campaign_report_owners.csv   Seed mapping of tenant to report owner, for the planned
                             scheduled email delivery. OWNER_EMAIL is filled by hand -
                             no owner field exists anywhere in the source data
```

## Notes on the data

These are measured properties of the source table, not assumptions, and each one changes
how a figure should be read.

- **`SESSION_ID` is absent on campaign rows before November 2025.** Session-derived
  sections are therefore empty for older windows, and the drill-down says so explicitly
  rather than reporting a zero.
- **`campaign_name` capture began March 2026.** Before that every label is the raw
  tracking ID. Roughly half of all campaign IDs are UUIDs carrying a separate name; the
  other half are human-readable slugs that *are* the name.
- **No persistent visitor ID exists.** Identity comes only from an `email` URL parameter
  riding on personalised links. The Audience page's anonymous section uses `REQUEST_IP` +
  `SESSION_ID` as an explicit proxy, not verified identity.
- **Region comes from the browser timezone** — the only location signal present. `UTC` and
  `Etc/*` are settings rather than places, so they group as unknown.
- **"Direct / none" traffic means no referrer was recorded**, not that someone typed the
  URL. For a link opened from an email client that is the normal case.
- **Duration is the span from a session's first to last event**, not time spent reading.
  It is heavily right-skewed — most sessions contain a single event.
- **Actions overlap.** Action reach counts sessions per action, so its bars deliberately
  sum to more than the funnel stage above them.
- **One session can touch several campaigns**, so the campaign list's share-of-sessions
  column sums to more than 100%.
