-- Warehouse-runtime deployment for the dashboard, run from a Snowsight worksheet.
--
-- Reconstructed from the statement Sahil runs by hand; the Workspace copy lives in
-- warehouse_mode.sql, which has no export path. Replace this if the two differ.
--
-- Prerequisite: the app files must already be in the stage. Uploading is manual
-- (Snowsight: Data > Databases > CIT_DATA_CORE > TRACKING > Stages > + Files),
-- with the path field set per directory so `pages/`, `src/`, and `.streamlit/`
-- keep their layout. Verify before opening the app:
--
--   LS @CIT_DATA_CORE.TRACKING.ANALYTICS_DASHBOARD_STAGE;
--
-- Warehouse runtime resolves packages with conda from the Snowflake Anaconda
-- channel via environment.yml, so no External Access Integration is needed.
-- Note CREATE OR REPLACE drops the object and its grants - re-grant afterwards
-- if anyone else has USAGE on the app.

CREATE OR REPLACE STREAMLIT CIT_DATA_CORE.TRACKING.ANALYTICS_DASHBOARD
  ROOT_LOCATION = '@CIT_DATA_CORE.TRACKING.ANALYTICS_DASHBOARD_STAGE'
  MAIN_FILE = 'streamlit_app.py'
  QUERY_WAREHOUSE = COMPUTE_WH;

-- Updating afterwards: re-upload the changed files to the stage, then close and
-- reopen the app - a ROOT_LOCATION app re-reads the stage on each new session,
-- so re-running the statement above is only needed if something seems cached.
