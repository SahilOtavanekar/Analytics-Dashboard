import streamlit as st

st.set_page_config(page_title="Analytics Dashboard", page_icon="📊", layout="wide")

st.title("Analytics Dashboard")
st.write(
    "Interactive analytics over the `CIT_DATA_CORE.TRACKING.INSYTE_TRAKING_EVENTS` "
    "tracking events table. Use the sidebar to navigate between pages."
)

st.page_link("pages/1_Executive_Dashboard.py", label="Executive Dashboard", icon="📊")
st.page_link("pages/2_Event_Analytics.py", label="Event Analytics", icon="📈")
st.page_link("pages/3_Session_Analytics.py", label="Session Analytics", icon="🕒")
st.page_link("pages/4_User_Activity.py", label="User Activity", icon="👥")
st.page_link("pages/5_Campaign_Analytics.py", label="Campaign Analytics", icon="🎯")
st.page_link("pages/6_Conversion.py", label="Conversion", icon="🔻")
st.page_link("pages/7_Pages_and_Sources.py", label="Pages & Sources", icon="🧭")
