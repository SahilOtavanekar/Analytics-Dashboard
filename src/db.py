import pandas as pd
import streamlit as st

TABLE_FQN = "CIT_DATA_CORE.TRACKING.INSYTE_TRAKING_EVENTS"

# One hour, up from the five minutes this used to be.
#
# Measured on this warehouse: a bare "SELECT 1" costs ~275ms of round trip, and the
# median dashboard query 550-670ms, so a 12-query page takes ~8-9s cold. Nothing in
# the client can fix that - async submission via collect_nowait() was measured at
# 13.6s against 9.5s sequential, because submission still blocks per statement and
# the X-Small warehouse is single-cluster, so concurrent queries queue rather than
# parallelise.
#
# What does fix it is not going to Snowflake at all. The default window is the last
# 30 days of an append-only table, so an hour of staleness changes almost nothing,
# while a 5-minute TTL expired mid-session and made navigation feel like a cold
# start every time. The sidebar shows the age and offers an explicit refresh.
CACHE_TTL_SECONDS = 3600


# Demand AI's own traffic, used for testing and for its marketing site, is tagged
# with this tenant. Verified as a complete and unambiguous handle before being used
# as one: it is the only spelling in the data (no case or punctuation variants), and
# zero demand_ai events appear outside internal hosts - www.demandai.net, localhost,
# 127.0.0.1 and an Amplify preview branch.
#
# It is NOT a general "internal traffic" rule. ~8,400 events a month sit on
# internal-looking hosts under other tenant IDs, and amplifye.ai is the product
# domain rather than a dev environment - insyte.amplifye.ai serves customer content
# under real customer tenants. Excluding those would remove genuine customer data.
INTERNAL_TENANT = "demand_ai"
EXCLUDE_INTERNAL_KEY = "exclude_internal"

# Internal traffic arrives two independent ways, and filtering only the first left
# demandai.co at the top of Top Companies with the filter switched on:
#
#   tenant_id = 'demand_ai'  - whose CONTENT is being viewed (Demand AI's own site)
#   email domain demandai.co - who the VISITOR is (Demand AI staff)
#
# All 1,113 sessions from staff addresses in a 30-day window sit on CUSTOMER
# tenants, none on demand_ai, so the tenant rule could never reach them.
#
# Matched by pattern, not equality: the data carries five spellings - demandai.co,
# a malformed demandai.co" with a trailing quote, demandai.com, demandai-ai.co and
# demand-ai.co. Two patterns cover all five. The COALESCE matters - a row with no
# email must not match and be dropped.
_VISITOR_DOMAIN = (
    "SPLIT_PART(LOWER(COALESCE(QUERY_PARAMETERS:email::STRING, "
    'QUERY_PARAMETERS:"amp;email"::STRING, \'\')), \'@\', 2)'
)

# The two halves are named separately because Campaign Analytics keeps one and drops the
# other - see table_fqn(keep_own_campaigns=True). Everywhere else applies both.
_TENANT_IS_EXTERNAL = f"COALESCE(PROPERTIES:tenant_id::STRING, '') <> '{INTERNAL_TENANT}'"
_VISITOR_IS_EXTERNAL = (
    f"NOT (COALESCE({_VISITOR_DOMAIN}, '') ILIKE '%demandai%'"
    f" OR COALESCE({_VISITOR_DOMAIN}, '') ILIKE '%demand-ai%')"
)
_INTERNAL_PREDICATE = f"{_TENANT_IS_EXTERNAL} AND {_VISITOR_IS_EXTERNAL}"


def exclude_internal() -> bool:
    """Is the reader currently excluding Demand AI's own traffic?

    Read from session state rather than a module global: the cache and the Python
    process are shared across viewers in Snowflake, so a module-level flag would let
    one reader's choice change another reader's numbers.
    """
    try:
        return bool(st.session_state[EXCLUDE_INTERNAL_KEY])
    except Exception:
        # No script run in progress (plain import, notebook, test probe) - default
        # to the unfiltered table so nothing silently drops rows.
        return False


def table_fqn(keep_own_campaigns: bool = False) -> str:
    """The table every query reads from, filtered if the reader asked for it.

    `keep_own_campaigns` drops only the TENANT half of the filter, keeping the visitor half.
    Campaign Analytics passes it, and nothing else does. The reason is that the two halves
    answer different questions, and only one of them is unwanted there:

        tenant_id = 'demand_ai'   whose CONTENT this is - Demand AI's own campaigns
        demandai.co visitor       who is VISITING - Demand AI staff

    Excluding internal traffic is about keeping staff activity out of customer figures. On
    Campaign Analytics the reader is looking at a list OF campaigns, and Demand AI's own are
    legitimately part of that list - demand_ai_internal_website_track is the third largest by
    sessions and vanished entirely with the filter on. Staff visitors are still stripped, so
    the audience and identity sections of any campaign stay free of demandai.co people.

    The consequence is deliberate and stated on the page: with the filter on, this page's
    campaign count and session total include the demand_ai tenant while every other page's
    exclude it. See _EXCLUDE_ACTIVE_OWN in src/components.py for the wording.

    Applying the filter here rather than in each WHERE clause is deliberate. All 34
    table references in queries.py go through this function, so one substitution
    reaches every KPI, chart and table - no per-query edits, no change to any
    parameter list, and no chance of a query being missed and quietly reporting
    unfiltered numbers beside filtered ones.

    Because run_query caches on the SQL text, the two states produce different cache
    keys automatically; toggling does not serve stale figures from the other state.

    The filter is row-level, not session-level. A staff member browsing a customer's
    document loses the rows carrying their address - so they vanish from Top
    Companies and every identity metric - but the remaining rows of that session
    still count toward that customer's session total, as anonymous activity. A
    deliberate choice: excluding whole sessions would need a window over the
    unfiltered table on every query, and the activity did happen on that content.
    """
    if not exclude_internal():
        return TABLE_FQN
    predicate = _VISITOR_IS_EXTERNAL if keep_own_campaigns else _INTERNAL_PREDICATE
    return f"(SELECT * FROM {TABLE_FQN} WHERE {predicate})"


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner="Querying Snowflake...")
def run_query(sql: str, params: list | None = None) -> pd.DataFrame:
    conn = st.connection("snowflake")
    session = conn.session()
    return session.sql(sql, params=params or []).to_pandas()


def clear_cache() -> None:
    """Drop every cached result so the next query goes to Snowflake."""
    run_query.clear()
