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

## Deployment

### Snowsight Workspaces — the live deployment

`snowflake.yml` is the config that deploys today, via **Deploy** from Snowsight
Workspaces. Deploy ships the whole folder regardless of the `artifacts` list, and the
live app runs the **container runtime** on compute pool `DEV`, with
`query_warehouse = COMPUTE_WH`.

One consequence is worth knowing: the container runtime resolves packages from
`pyproject.toml` via PyPI, which needs an External Access Integration this account does
not have. **The dependency list is effectively closed** — which is why the charts are
Altair (it ships with Streamlit) and why the PDF is written by `src/minipdf.py` from the
standard library rather than by a third-party package.

### Stage + `CREATE STREAMLIT` — warehouse-runtime fallback

`sql/deploy_warehouse_app.sql` creates the app from
`@CIT_DATA_CORE.TRACKING.ANALYTICS_DASHBOARD_STAGE`. The warehouse runtime reads
`environment.yml` (Snowflake Anaconda channel) instead of `pyproject.toml`, so no
External Access Integration is needed. Uploading to the stage is manual, and the path
field must be set per directory so `pages/`, `src/` and `.streamlit/` keep their layout.

### Headless hosting

No secrets file is needed inside Snowflake — `st.connection("snowflake")` resolves the
active session. For headless hosting outside Snowflake (e.g. Streamlit Community Cloud)
there is no browser to complete SSO, so key-pair auth is required; the variant is
commented in `.streamlit/secrets.toml.example`. Never commit `secrets.toml` or anything
under `keys/` — both are gitignored.

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
