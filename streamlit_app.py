import streamlit as st

# Router only. Executive Dashboard is the landing page.
#
# st.navigation rather than the pages/ directory's automatic discovery, because
# under v1 the entry script's sidebar label is derived from its FILENAME - see
# source_util.page_icon_and_name - so streamlit_app.py would have shown up as
# "streamlit app" above the real pages. set_page_config(page_title=...) does not
# change it. Declaring pages here gives explicit labels, explicit order, and a
# real default page.
#
# The directory can stay named pages/: st.navigation() sets
# PagesManager.uses_pages_directory = False, which switches off v1 auto-discovery,
# so nothing is registered twice. Each page keeping its own st.set_page_config is
# also fine - verified with streamlit.testing AppTest, no exception raised.
st.set_page_config(page_title="Analytics Dashboard", page_icon="📊", layout="wide")

PAGES = [
    st.Page("pages/1_Executive_Dashboard.py", title="Executive Dashboard", icon=":material/dashboard:", default=True),
    st.Page("pages/2_Event_Analytics.py", title="Event Analytics", icon=":material/timeline:"),
    st.Page("pages/3_Session_Analytics.py", title="Session Analytics", icon=":material/schedule:"),
    st.Page("pages/4_Audience.py", title="Audience", icon=":material/groups:"),
    st.Page("pages/5_Campaign_Analytics.py", title="Campaign Analytics", icon=":material/campaign:"),
    st.Page("pages/6_Conversion.py", title="Conversion", icon=":material/filter_alt:"),
    st.Page("pages/7_Pages_and_Sources.py", title="Pages & Sources", icon=":material/explore:"),
    st.Page("pages/8_Content_Performance.py", title="Content Performance", icon=":material/description:"),
    st.Page("pages/9_AI_Usage.py", title="AI Usage", icon=":material/smart_toy:"),
    st.Page("pages/10_Tenant_Breakdown.py", title="Tenant Breakdown", icon=":material/apartment:"),
]

st.navigation(PAGES).run()
