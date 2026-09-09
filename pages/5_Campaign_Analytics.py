import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

import streamlit as st

from src.campaign_detail import SELECTED_KEY
from src.campaign_detail import render as detail_render
from src.campaign_report import STATUS_MISSING, classify_lookup, collect, filename, to_pdf
from src.charts import CAMPAIGN_PICK, campaign_bar
from src.components import AUTO_UNSUPPORTED, auto_download, date_range_filter, kpi, kpi_row, previous_window
from src.db import run_query
from src.queries import CAMPAIGN_PICKER_LIMIT, campaign_kpis_sql, campaign_lookup_sql, top_campaigns_sql

st.set_page_config(page_title="Campaign Analytics", page_icon="🎯", layout="wide")

# The download sits on the title's line, at the top right. It has to be RESERVED here rather
# than written here: the button needs the PDF, the PDF needs collect(), and collect() needs the
# date range - which date_range_filter only returns after writing the internal-traffic banner
# into the body below. A container holds its place in the layout, so filling it further down
# still lands it up here, beside the title.
head_left, head_right = st.columns([3, 1], vertical_alignment="bottom")
head_left.title("Campaign Analytics")
download_slot = head_right.container()

CAMPAIGN_NOTE = "Ranked by sessions. Hover a bar for that campaign's figures, or click it to open a full breakdown."
CHART_ROWS = 10
NAME_MAX = 64

# ?campaign_id=<id> opens that campaign directly, so one campaign's view can be sent to
# someone. Only the id is carried, deliberately: encoding the date range too would make a
# link fully reproducible, but it would also let a stale link silently override the range
# the recipient had chosen. The link therefore means "this campaign", and the recipient's
# own dates decide the period - which the detail header states outright.
QUERY_KEY = "campaign_id"

# The deployed app's own address, and the base of every link this page hands out. Hardcoded
# rather than derived because the two runtimes that could supply it both give the wrong answer:
# locally st.context.url is http://localhost:8501/..., and in Snowsight the app is inside an
# iframe. Snowsight routes the page after the #, so the campaign parameter lands on the end of
# the fragment - which is exactly where st.query_params reads it from.
#
# If the app is ever redeployed to another account or renamed, this line is what has to change;
# nothing else in the page knows the URL.
APP_URL = ("https://app.snowflake.com/streamlit/rtewwsa/myb70405/#/apps/"
           "CIT_DATA_CORE.TRACKING.ANALYTICS_DASHBOARD/Campaign_Analytics")
# Both lines name the pdf parameter, because a URL feature nobody can see is a URL feature
# nobody uses - and this is the one place a reader is already looking at the link's shape.
# Folded into the existing caption rather than added beside it: one message, not two.
SHARE_NOTE = "Shareable link for this campaign — the recipient's own date range applies: `{link}` — append `&pdf=true` to download the report on open."

# The download is built from campaign_report.collect() - the same call the page renders from -
# so a saved file and the screen cannot disagree about one campaign.
DL_PDF = ":material/download: Download report (PDF)"

DL_PDF_HELP = (
    "Download"
)
DL_PDF_FAIL = "PDF export is unavailable in this environment: {err}. The HTML report carries the same content."

# ?campaign_id=<id>&pdf=true downloads the same file the button below produces, without
# anyone having to find the button - which is what makes a link mailable to someone who
# only wants the report. The parameter is additive: the campaign's page still renders in
# full behind the download. Stripping it was considered and dropped, because the PDF is
# built from the same collect() the page renders from, so there is nothing left to save.
PDF_KEY = "pdf"
PDF_TRUE = {"1", "true", "yes", "on"}
# Keyed so the injected script can find this one button by class rather than by guessing.
DL_PDF_WIDGET = "campaign_pdf_download"
PDF_STARTED = ":material/download: Downloading **{file}**. If your browser blocked it, use the button at the top right — same file."
PDF_MANUAL = ":material/info: This link asked for an automatic download, which this runtime cannot start. The button at the top right is the same file."
PDF_FAILED = ":material/warning: This link asked for a download, but the PDF could not be built here: {err}"
PDF_NO_CAMPAIGN = f":material/info: `{PDF_KEY}=true` needs a campaign to export. Add `?{QUERY_KEY}=<id>&{PDF_KEY}=true`, or open a campaign below and use its download button."

PICK_LABEL = "Open any campaign"
PICK_HINT = "Search by name or ID, or click a bar above"
PICK_HELP = (
    "Every campaign in the selected date range, not just the ten charted — start typing a "
    "name or a tracking ID to filter the list. You can also paste an ID that is not listed; "
    "if it exists but ran on other dates, the range it did run will be named."
)
ID_UNKNOWN = "No campaign with ID `{id}` exists anywhere in the tracked data. Check for a typo — IDs are case-sensitive."
ID_WRONG_WINDOW = "Campaign `{id}`{named} exists but has no activity between the selected dates. It ran **{first} to {last}** with {events:,} events — widen the date range to open it."
ORPHANED = ":material/info: The open campaign is no longer in the top ten for this date range. Its figures below are recalculated for the new range."

# The tooltip's own fields, minus the ones measured and rejected: identified people and
# companies (13 of the top 25 campaigns have none and two have exactly one, so the column
# would be a blank that reads as a zero, and n=1 names a person) and AI attach (0.0% on
# eight of the top ten, because ml_request events carry no campaign ID).
TABLE_COLUMNS = [
    "CAMPAIGN_LABEL",
    "TENANT",
    "SESSIONS",
    "SESSION_SHARE_PCT",
    "EVENTS",
    "EVENTS_PER_SESSION",
    "ASSET_PCT",
    "LEAD_PCT",
    "FIRST_SEEN",
    "LAST_SEEN",
    "ACTIVE_DAYS",
]
TABLE_HEADERS = {
    "CAMPAIGN_LABEL": "Campaign",
    "TENANT": "Tenant ID",
    "SESSIONS": "Sessions",
    "SESSION_SHARE_PCT": "% of campaign sessions",
    "EVENTS": "Events",
    "EVENTS_PER_SESSION": "Events / session",
    "ASSET_PCT": "% sessions opened content",
    "LEAD_PCT": "% sessions submitted",
    "FIRST_SEEN": "First seen",
    "LAST_SEEN": "Last seen",
    "ACTIVE_DAYS": "Active days",
}

# keep_own_campaigns swaps the internal-traffic banner for the one that describes what this
# page actually does: every campaign query here routes through queries._campaign_table(), which
# keeps the demand_ai tenant and still drops demandai.co visitors. Passing it is not optional -
# the default banner would claim this page omits Demand AI's own tenant, and it does not.
start_date, end_date = date_range_filter(default_days=30, keep_own_campaigns=True)
prev_start, prev_end = previous_window(start_date, end_date)
params = [start_date, end_date]

# Asked for every campaign in the window, not ten: the picker searches all of them and the
# LIMIT does not reduce the scan, so one query feeds both. See CAMPAIGN_PICKER_LIMIT.
top_campaigns = run_query(top_campaigns_sql(CAMPAIGN_PICKER_LIMIT), params)
ranked = top_campaigns.head(CHART_ROWS)


def open_campaign(campaign_id):
    """Open one campaign, and put it in the URL so the view can be shared.

    Writing st.query_params only enqueues a page_info_changed message - it updates the
    address bar and does not itself rerun - so there is no loop between the URL and the
    session state. The explicit rerun below is what swaps the view.
    """
    st.session_state[SELECTED_KEY] = str(campaign_id)
    st.query_params[QUERY_KEY] = str(campaign_id)
    st.rerun()


# A link carrying ?campaign_id=... opens that campaign directly. The URL is the source of
# truth on arrival and the in-page controls own it afterwards, writing back on every change.
# Adoption is conditional so that pressing Back - which clears both - is not immediately
# undone by re-reading a parameter that is no longer there.
linked = st.query_params.get(QUERY_KEY)
if linked and linked != st.session_state.get(SELECTED_KEY):
    st.session_state[SELECTED_KEY] = str(linked)

# The detail view REPLACES the ranking rather than appending to it, and that includes the
# page-level KPI row - those three figures describe all campaigns, so leaving them above
# one campaign's breakdown would invite reading them as its own. Streamlit also scrolls to
# the top of the page on every rerun, so a detail section added below the chart would
# arrive out of view with nothing signalling it. The selected ID lives in a durable key, so
# the sidebar's date and filter controls can rerun the page without losing it.
selected = st.session_state.get(SELECTED_KEY)
if selected:
    if st.button(":material/arrow_back: Back to all campaigns"):
        st.session_state.pop(SELECTED_KEY, None)
        # Clear the URL too, or the next run re-adopts the parameter and Back does nothing.
        if QUERY_KEY in st.query_params:
            del st.query_params[QUERY_KEY]
        st.rerun()
    # Collected once, here, and handed to the renderer. The page needs the report before it
    # draws anything, to decide whether a share link and download buttons make sense at all:
    # both are assertions ABOUT a campaign, and neither belongs on a page whose only honest
    # content is "there is no such campaign". run_query caches on (sql, params), so the
    # renderer's own lookups are served from cache rather than repeated.
    report = collect(run_query, selected, start_date, end_date)
    resolves = report["status"] != STATUS_MISSING
    if resolves:
        # Always the deployed app's address, never this process's own. It was built from
        # st.context.url, which is right only when the reader is already on the deployed app:
        # in development it yields http://localhost:8501/..., so a link copied from a dev
        # session is unusable to everyone else - and inside Snowsight the app runs in an iframe
        # whose URL is not necessarily one a recipient can open either. A constant gives the
        # same shareable link wherever the page happens to be rendered.
        share_link = f"{APP_URL}?{QUERY_KEY}={selected}"
        st.caption(SHARE_NOTE.format(link=share_link))
        # Checked against the ten that are charted, not every campaign in the window, because
        # that is what the message claims. A campaign at rank 50 is genuinely no longer in
        # the top ten even though the picker can still reach it.
        if not ranked.empty and selected not in set(ranked["CAMPAIGN_ID"].astype(str)):
            # The reader narrowed the date range and the open campaign left the top ten. Its
            # detail is still valid for the new range, so it stays open - but say so,
            # because the alternative is silently discarding what they were reading.
            st.caption(ORPHANED)
        # Built from the same report the page is about to render, so a downloaded file can
        # never disagree with what is on screen. The HTML is deliberately self-contained and
        # chart-free - tables with CSS bars - because these same bytes are intended to become
        # the body of the scheduled email, and Snowflake's SYSTEM$SEND_EMAIL sends text/html
        # with no attachments and no JavaScript.
        # PDF generation is the one thing on this page with an external dependency, so a
        # failure is contained to its own button. Losing a download is a nuisance; losing the
        # campaign breakdown because a package did not install is not.
        try:
            pdf_bytes = to_pdf(report)
        except Exception as exc:
            pdf_bytes = None
            pdf_error = str(exc)
        pdf_name = filename(selected, start_date, end_date, "pdf")
        # Filled into the slot reserved beside the title. type="primary" takes the teal from
        # [theme.light]/[theme.dark] in .streamlit/config.toml rather than naming a colour here -
        # those two values were each solved for contrast against their own surface, so a
        # hardcoded fill would be wrong in one mode and would duplicate a decision already made.
        with download_slot:
            if pdf_bytes:
                # on_click="ignore" for two reasons, the first found by driving a real browser.
                # A click defaults to triggering a rerun, and by then this run has already
                # deleted the pdf parameter - so the "Downloading ..." confirmation below was
                # printed and then immediately wiped by the rerun the download itself caused.
                # Worse, the same happened if the click fired but the browser refused the file:
                # no file and no message. Nothing here needs a rerun - the button returns a value
                # this page ignores - so suppressing it keeps the confirmation on screen and
                # saves a full re-render on every manual click too.
                st.download_button(DL_PDF, data=pdf_bytes, file_name=pdf_name, mime="application/pdf", help=DL_PDF_HELP, key=DL_PDF_WIDGET, type="primary", width="stretch", on_click="ignore")
            else:
                # Left un-tinted: a disabled primary reads as an action that should work, and
                # this one cannot. The reason is in its tooltip and in the caption below.
                st.button(DL_PDF, disabled=True, help=DL_PDF_FAIL.format(err=pdf_error[:120]), width="stretch")
        # &pdf=true clicks that button for the reader. The button is rendered first and stays
        # visible deliberately: the click is the guaranteed path and the automatic one is
        # best-effort, so a blocked download costs a click rather than the file.
        #
        # Exactly one line is printed, and one is always printed. A link that asked for a
        # download and produced neither a file nor a sentence is the failure mode worth
        # spending a caption on - the disabled button's own reason is a hover tooltip, which
        # someone arriving from a mailed link has no reason to go looking for.
        if str(st.query_params.get(PDF_KEY, "")).strip().lower() in PDF_TRUE:
            if not pdf_bytes:
                st.caption(PDF_FAILED.format(err=pdf_error[:120]))
            elif auto_download(DL_PDF_WIDGET, pdf_name) == AUTO_UNSUPPORTED:
                st.caption(PDF_MANUAL)
            else:
                st.caption(PDF_STARTED.format(file=pdf_name))
            # Dropped from the URL once acted on, so a rerun cannot download again and what
            # is left in the address bar is the ordinary shareable link. Writing query params
            # only enqueues a page_info_changed, so this does not rerun. auto_download keeps
            # its own guard as well, for hosts where this write does not reach the address bar.
            del st.query_params[PDF_KEY]
    detail_render(selected, start_date, end_date, data=report)
    # Suppressed alongside the rest: a footer stating the range implies figures were shown
    # for it, and on this path there are none.
    if resolves:
        st.caption(f"Showing {start_date} to {end_date}.")
    st.stop()

# Reached only when no campaign is open, so pdf=true arrived without one to export. Said
# once, here, rather than ignored: a parameter that silently does nothing reads as a broken
# feature, and the fix is a single addition to the URL.
if str(st.query_params.get(PDF_KEY, "")).strip().lower() in PDF_TRUE:
    st.caption(PDF_NO_CAMPAIGN)
    del st.query_params[PDF_KEY]

now = run_query(campaign_kpis_sql(), params).iloc[0]
was = run_query(campaign_kpis_sql(), [prev_start, prev_end]).iloc[0]

kpi_row(
    [
        kpi("Total Campaigns", now["TOTAL_CAMPAIGNS"], was["TOTAL_CAMPAIGNS"]),
        kpi("Total Sessions", now["TOTAL_SESSIONS"], was["TOTAL_SESSIONS"]),
        kpi("Avg Events / Campaign", now["AVG_EVENTS_PER_CAMPAIGN"], was["AVG_EVENTS_PER_CAMPAIGN"]),
    ]
)

st.subheader("Top Campaigns")
st.caption(CAMPAIGN_NOTE)
if top_campaigns.empty:
    st.info("No campaign activity in this date range.")
else:
    chart, notes = campaign_bar(ranked, start_date, end_date)
    # on_select reruns the script when a bar is clicked; the selection comes back keyed by
    # the parameter campaign_bar declared. An empty selection is a click on empty space,
    # which must NOT close an open campaign - so it is ignored rather than written through.
    event = st.altair_chart(chart, width="stretch", on_select="rerun", key="campaign_chart")
    picked = (getattr(event, "selection", None) or {}).get(CAMPAIGN_PICK) or []
    if picked and picked[0].get("CAMPAIGN_ID"):
        open_campaign(picked[0]["CAMPAIGN_ID"])
    for note in notes:
        st.caption(note)
    # A picker beside the chart, not instead of it. Chart clicks cannot be driven by
    # keyboard, cannot be exercised by the test suite, and cannot be verified inside
    # Snowflake from here - so the same drill-down needs one route that is certain.
    # ONE control, not two. It searches every campaign in the window - typically 500-1,000,
    # against the ten on the chart - and still accepts an id pasted from elsewhere, so the
    # earlier top-ten dropdown and id text field are both subsumed. Options are the raw
    # CAMPAIGN_IDs because ids are unique where names are not (16 names are shared), and the
    # display string carries both so fuzzy matching finds a campaign by either.
    options = [str(r.CAMPAIGN_ID) for r in top_campaigns.itertuples()]
    shown = {}
    for row in top_campaigns.itertuples():
        cid = str(row.CAMPAIGN_ID)
        name = (row.CAMPAIGN_LABEL or "").strip()
        # Names run to 140 characters - one is a chain of "-clone-<uuid>" suffixes - and 305
        # of 499 exceed 60, so the name is trimmed while the id is left whole. Truncating the
        # id instead would stop a pasted UUID matching the text being filtered.
        short = name if len(name) <= NAME_MAX else name[: NAME_MAX - 1].rstrip() + "…"
        shown[cid] = f"{short}  ·  {cid}" if name and name != cid else cid
    chosen = st.selectbox(PICK_LABEL, options, index=None, format_func=lambda c: shown.get(c, c), placeholder=PICK_HINT, help=PICK_HELP, accept_new_options=True, key="campaign_picker")
    if chosen:
        typed = str(chosen).strip()
        if typed in shown:
            open_campaign(typed)
        # Anything else was typed rather than picked, so it is validated before the view
        # switches. An unknown id would otherwise swap the page for one reading "not found",
        # costing the reader the ranking they were looking at in order to deliver an error.
        found = run_query(campaign_lookup_sql(), [start_date, end_date, typed]).iloc[0]
        outcome = classify_lookup(found)
        if outcome == "open":
            open_campaign(typed)
        elif outcome == "wrong_window":
            # Real id, wrong window. Naming the dates it did run turns a dead end into one
            # adjustment, instead of leaving someone widening the range by trial and error.
            named = f" (`{found['CAMPAIGN_NAME']}`)" if found["CAMPAIGN_NAME"] else ""
            st.warning(ID_WRONG_WINDOW.format(id=typed, named=named, first=found["FIRST_EVER"], last=found["LAST_EVER"], events=int(found["EVENTS_EVER"])))
        else:
            st.error(ID_UNKNOWN.format(id=typed))
    # The same figures as the tooltip, because a tooltip cannot be screenshotted, read on
    # a touch device, or copied into a deck - and these are the numbers people quote.
    with st.expander("View as table"):
        st.dataframe(ranked[TABLE_COLUMNS].rename(columns=TABLE_HEADERS), width="stretch", hide_index=True)

st.caption(f"Showing {start_date} to {end_date}. Change is against {prev_start} to {prev_end}.")
