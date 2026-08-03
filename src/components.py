import datetime as dt

import streamlit as st


def kpi_row(items: list[tuple[str, str]]) -> None:
    cols = st.columns(len(items))
    for col, (label, value) in zip(cols, items):
        col.metric(label, value)


def date_range_filter(default_days: int = 30) -> tuple[dt.date, dt.date]:
    today = dt.date.today()
    start = today - dt.timedelta(days=default_days)
    selected = st.sidebar.date_input("Date range", value=(start, today), max_value=today)
    if isinstance(selected, tuple) and len(selected) == 2:
        return selected
    return start, today
