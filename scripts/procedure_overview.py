# NOT A MODULE. scripts/build_procedure.py appends this file to the stored procedure's body, after
# src/db.py, src/queries.py, src/minipdf.py and src/campaign_report.py - so it reads their names
# (collect, _pdf_table, Canvas, ...) directly, and rebinds a few of them on purpose (_pdf_table,
# _pdf_series_chart, _PAGE_W, _PDF_BAND, IDENTITY_FLOOR). Import it on its own and it fails.
#
# ============================================================================
# 5. Campaign Overview - Day / Week / Month in one PDF per campaign, and
#    the email that carries them. Everything above this section is the project
#    source, embedded unchanged; this section only arranges what it produces.
#
#    Agreed layout (2026-10-07):
#      * the report's own title block (campaign, ID, tenant, reporting range);
#      * Day and Week: one page each on a pale panel (Day blue, Week sand) - cards
#        + donut, [Week: Activity over time], Pages viewed, then Where they are +
#        How they arrived | External referrers. No Session duration. Tables, cards
#        and the donut box in the section's own shades;
#      * Month: the report's original layout, every section full width, each with
#        its note, Pages viewed moved above Session duration;
#      * no Accounts reached anywhere - the company query is never run;
#      * each session counted once, on the day it started, with all its events -
#        collect() applies that (src/queries.py, _CAMPAIGN_SCOPE), so nothing here does.
# ============================================================================

OVERVIEW_TITLE = "Campaign Overview"
OVERVIEW_SITE = "Demand AI website"
OVERVIEW_CAMPAIGNS = [
    "demand_ai_internal_website_track",
    "demand_ai_internal_website_demo_request",
    "demand_ai_internal_website_research",
]

# Accounts reached is not part of the overview. Raising the floor means collect() never fetches it,
# so no company figure can reach the PDF or the email however either is written.
IDENTITY_FLOOR = 10 ** 9


# ---------------------------------------------------------------- the three periods
def _overview_collect(run, campaign_id, end):
    """[(tag, heading, data)] for Day, Week and Month ending on `end`."""
    week_start, month_start = end - dt.timedelta(days=6), end - dt.timedelta(days=29)
    periods = [("DAY", f"{end.strftime('%A')} {_fmt_date(end, year=True)}", end, end),
               ("WEEK", _fmt_range(week_start, end), week_start, end),
               ("MONTH", _fmt_range(month_start, end), month_start, end)]
    return [(tag, heading, collect(run, campaign_id, start, stop)) for tag, heading, start, stop in periods]


# ---------------------------------------------------------------- shared drawing helpers
_OV_FULL_W = _PAGE_W
_OV_GAP = 6.0
_OV_HALF_W = (_OV_FULL_W - _OV_GAP) / 2
_OV_LEFT = 15.0
_OV_WHITE = (255, 255, 255)
_OV_PANEL_BOTTOM = 281.0  # just above the running footer
_OV_THEME = {   # page tint, band tint
    "DAY": ((236, 244, 251), (212, 230, 246)),
    "WEEK": ((251, 246, 235), (242, 228, 200)),
    "MONTH": ((236, 247, 244), (208, 236, 229)),
}
_OV_TABLE_THEME = {"DAY": ((212, 230, 246), (225, 237, 248)), "WEEK": ((242, 228, 200), (246, 237, 217))}  # header, stripe
_OV_CARD_SHADE = {"DAY": (225, 237, 248), "WEEK": (246, 237, 217)}
_OV_SECTIONS = []          # (first page, label, starts at top of page) - read by the footer
_OV_TABLE_ON = [None]      # the current section's table theme, or None for the report's own colours


def _ov_in_column(pdf, col, draw):
    """Run `draw` with the margin and width narrowed to one column, then restore them."""
    global _PAGE_W
    saved = pdf.l_margin
    pdf.l_margin = _OV_LEFT + col * (_OV_HALF_W + _OV_GAP)
    _PAGE_W = _OV_HALF_W
    pdf.set_x(pdf.l_margin)
    try:
        draw()
    finally:
        pdf.l_margin = saved
        _PAGE_W = _OV_FULL_W


def _ov_head(frame, n):
    return None if frame is None else frame.head(n)


def _ov_keep_together(pdf, frame):
    """Start a short table on a fresh page rather than split it - measuring wrapped rows, since a long
    preview-site address takes three lines."""
    if frame is None or not len(frame) or len(frame) > 12:
        return
    h = 12 + 5.4
    multi_host = "HOST" in frame.columns and frame["HOST"].nunique() > 1
    for r in frame.itertuples():
        lines = max(1, math.ceil(len(str(r[1])) / 46), math.ceil(len(str(getattr(r, "HOST", ""))) / 26) if multi_host else 1)
        h += 5.4 if lines == 1 else 3.6 * lines + 1.8
    if pdf.get_y() + h > pdf.h - pdf.b_margin:
        pdf.add_page()


# The report's table, in the current section's colours when one is set: the header strip in the
# section's band shade and alternate rows in its stripe shade. _pdf_table's stripe colour is fixed
# at 250,252,252, so it is swapped at the canvas for the length of the call.
_ov_pdf_table_original = _pdf_table


def _pdf_table(pdf, *args, **kwargs):
    global _PDF_BAND
    theme = _OV_TABLE_ON[0]
    if theme is None:
        return _ov_pdf_table_original(pdf, *args, **kwargs)
    header, stripe = theme
    saved_band, real_fill = _PDF_BAND, pdf.set_fill_color

    def fill(r, g=None, b=None):
        if (r, g, b) == (250, 252, 252):
            return real_fill(*stripe)
        return real_fill(r, g, b)

    _PDF_BAND, pdf.set_fill_color = header, fill
    try:
        return _ov_pdf_table_original(pdf, *args, **kwargs)
    finally:
        _PDF_BAND = saved_band
        del pdf.set_fill_color


# The report's chart, with its heading kept on the same page as the chart under it.
_ov_series_chart_original = _pdf_series_chart


def _pdf_series_chart(pdf, title, frame, *args, **kwargs):
    if frame is not None and len(frame) >= 2 and pdf.get_y() + 72 > pdf.h - pdf.b_margin:
        pdf.add_page()
    _ov_series_chart_original(pdf, title, frame, *args, **kwargs)


def _ov_empty_message(data):
    """A period with nothing to draw says so in one line, rather than drawing empty cards."""
    if data["status"] == STATUS_OK:
        return None
    window = _fmt_range(data["start"], data["end"])
    if data["status"] == STATUS_NO_SESSIONS:
        return f"Events were recorded between {window}, but no identifiable sessions."
    return f"No sessions started on this campaign between {window}."


def _ov_concentration_note(pdf, k, s):
    conc = concentration(k)
    if conc is not None:
        _pdf_note(pdf, f"{conc:.0f}% of these {s:,} sessions came from a single network address "
                       f"({int(k['DISTINCT_IPS']):,} addresses in total). Likely one organisation or an automated client "
                       f"rather than {s:,} separate visitors - an address is not a person.")


def _ov_band(pdf, tag, title, band_rgb):
    """The opening of every period: a solid teal tag beside its dates, on that period's tint."""
    _OV_SECTIONS.append((pdf.page_no(), f"{tag.title()}: {title}", pdf.get_y() <= pdf.t_margin + 1))
    y = pdf.get_y()
    pdf.set_fill_color(*band_rgb)
    pdf.rect(pdf.l_margin, y, _PAGE_W, 11, style="F")
    pdf.set_fill_color(*_PDF_TEAL)
    pdf.rect(pdf.l_margin, y, 26, 11, style="F")
    pdf.set_xy(pdf.l_margin, y + 2.5)
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_text_color(*_OV_WHITE)
    pdf.cell(26, 6, tag, align="C")
    pdf.set_xy(pdf.l_margin + 31, y + 2.5)
    pdf.set_font("Helvetica", "B", 10.5)
    pdf.set_text_color(*_PDF_INK)
    pdf.cell(_PAGE_W - 36, 6, _safe(title))
    pdf.set_xy(pdf.l_margin, y + 14)
    pdf.set_text_color(*_PDF_INK)


def _ov_footer(pdf):
    here = [lab for first, lab, _ in _OV_SECTIONS if first == pdf.page_no()]
    carried = next((lab for first, lab, _ in reversed(_OV_SECTIONS) if first < pdf.page_no()), None)
    at_top = any(first == pdf.page_no() and top for first, _, top in _OV_SECTIONS)
    label = " + ".join(([carried] if carried and not at_top else []) + here)
    pdf.set_y(-11)
    pdf.set_draw_color(*_PDF_RULE)
    pdf.line(pdf.l_margin, pdf.get_y() - 1.5, pdf.l_margin + _PAGE_W, pdf.get_y() - 1.5)
    pdf.set_font("Helvetica", "B", 6.5)
    pdf.set_text_color(*_PDF_TEAL)
    pdf.cell(13, 4, "Demand AI")
    pdf.set_font("Helvetica", "", 6.5)
    pdf.set_text_color(*_PDF_MUTED)
    pdf.cell(_PAGE_W * 0.75 - 13, 4, _safe(f"-   {OVERVIEW_TITLE}   -   {OVERVIEW_SITE}   -   {label}"))
    pdf.cell(_PAGE_W * 0.25, 4, f"Page {pdf.page_no()} of {pdf.total_pages}", align="R")


# ---------------------------------------------------------------- Day / Week: one tinted page each
def _ov_headline(pdf, data):
    """Cards + donut, on the section's card shade (_PDF_BAND, set by the caller)."""
    k = data["kpis"]
    s = int(k["SESSIONS"])
    left_w = (_PAGE_W - 4.0) / 3.0
    pdf.set_fill_color(*_PDF_BAND)
    pdf.rect(pdf.l_margin + left_w + 4.0, pdf.get_y(), _PAGE_W - left_w - 4.0, 46.0, style="F")
    _pdf_mix_row(pdf, [("Distinct sessions", f"{s:,}"), ("Interactions / session", f"{float(k['INTERACTIONS']) / s:,.1f}")],
                 data.get("event_mix"), int(k["INTERACTIONS"]))
    _ov_concentration_note(pdf, k, s)


def _ov_pages_table(pdf, pages):
    if pages is None or not len(pages):
        return
    hosts = pages["HOST"].dropna().unique()
    if len(hosts) == 1:
        _pdf_table(pdf, f"Pages viewed - {hosts[0]}", pages, "PAGE", "SESSIONS", "Sessions", wrap=True)
    else:
        _pdf_table(pdf, "Pages viewed", pages, "PAGE", "SESSIONS", "Sessions", extra=[("HOST", _fmt_text)], wrap=True)


def _ov_arrivals(pdf, data):
    sources = data.get("sources")
    _pdf_table(pdf, "How they arrived", sources, "SOURCE_GROUP", "SESSIONS", "Sessions")
    if sources is not None and not len(sources):
        _pdf_heading(pdf, "How they arrived", keep=14)
        _pdf_note(pdf, ARRIVAL_NONE)


def _ov_short_section(pdf, tag, title, data, rows):
    _ov_band(pdf, tag, title, _OV_THEME[tag][1])
    message = _ov_empty_message(data)
    if message:
        _pdf_note(pdf, message)
        return
    _ov_headline(pdf, data)
    daily = data.get("daily")
    if daily is not None and len(daily) >= 2:
        _pdf_series_chart(pdf, "Activity over time", daily, "EVENT_DATE", "SESSION_COUNT", "Sessions", kind="bar",
                          total=int(data["kpis"]["SESSIONS"]))
    _ov_pages_table(pdf, _ov_head(data.get("pages"), rows["pages"]))
    regions = _ov_head(data.get("regions"), rows["regions"])
    refs = _ov_head(data.get("referrers"), rows["refs"])
    y0 = pdf.get_y()

    def left():
        _pdf_table(pdf, "Where they are", regions, "REGION", "SESSIONS", "Sessions")
        _ov_arrivals(pdf, data)

    _ov_in_column(pdf, 0, left)
    y_left = pdf.get_y()
    pdf.set_xy(_OV_LEFT, y0)
    _ov_in_column(pdf, 1, lambda: _pdf_table(pdf, "External referrers", refs, "REFERRER", "SESSIONS", "Sessions"))
    pdf.set_xy(_OV_LEFT, max(y_left, pdf.get_y()))


# If a section would not fit its page: fewer pages, then fewer referrers, then fewer regions.
_OV_FIT_STEPS = [dict(pages=5, refs=5, regions=9), dict(pages=4, refs=5, regions=9), dict(pages=4, refs=4, regions=6),
                 dict(pages=3, refs=4, regions=6), dict(pages=3, refs=3, regions=5)]


def _ov_draw_short(pdf, tag, title, data, rows):
    """The section in its own colours: cards and donut box in the card shade, tables in the table theme."""
    global _PDF_BAND
    saved_band = _PDF_BAND
    try:
        _PDF_BAND = _OV_CARD_SHADE[tag]
        _OV_TABLE_ON[0] = _OV_TABLE_THEME[tag]
        _ov_short_section(pdf, tag, title, data, rows)
    finally:
        _PDF_BAND = saved_band
        _OV_TABLE_ON[0] = None


def _ov_fits(tag, title, data, rows, start_y):
    """Draw the section on a throwaway canvas from the same point; it fits if no page was added."""
    probe = Canvas()
    probe.set_auto_page_break(auto=True, margin=18)
    probe.set_margins(15, 14, 15)
    probe.add_page()
    probe.set_y(start_y)
    saved_sections = list(_OV_SECTIONS)
    try:
        _ov_draw_short(probe, tag, title, data, rows)
    finally:
        _OV_SECTIONS[:] = saved_sections
    return probe.page_no() == 1 and probe.get_y() <= _OV_PANEL_BOTTOM - 2


def _ov_tinted_section(pdf, tag, title, data):
    start_y = pdf.get_y()
    rows = next((r for r in _OV_FIT_STEPS if _ov_fits(tag, title, data, r, start_y)), _OV_FIT_STEPS[-1])
    pdf.set_fill_color(*_OV_THEME[tag][0])
    pdf.set_draw_color(*_OV_THEME[tag][1])
    pdf.rect(pdf.l_margin - 3, start_y - 3, _PAGE_W + 6, _OV_PANEL_BOTTOM - start_y + 3, style="DF")
    _ov_draw_short(pdf, tag, title, data, rows)


# ---------------------------------------------------------------- Month: the report's original layout
def _ov_month_body(pdf, data):
    message = _ov_empty_message(data)
    if message:
        _pdf_note(pdf, message)
        return
    k = data["kpis"]
    s = int(k["SESSIONS"])
    _pdf_mix_row(pdf, [("Distinct sessions", f"{s:,}"), ("Interactions / session", f"{float(k['INTERACTIONS']) / s:,.1f}")],
                 data.get("event_mix"), int(k["INTERACTIONS"]))
    _ov_concentration_note(pdf, k, s)
    daily, part = data.get("daily"), data.get("partial_day")
    part_note = partial_day_note(part) if part else None
    if daily is not None and len(daily) >= 2:
        _pdf_series_chart(pdf, "Activity over time", daily, "EVENT_DATE", "SESSION_COUNT", "Sessions", kind="bar", note=part_note, partial=part, total=s)
    else:
        _pdf_table(pdf, "Activity by day", daily, "EVENT_DATE", "SESSION_COUNT", "Sessions", note=part_note)
    # Pages viewed above Session duration, by request. Long, so it may continue overleaf with its header.
    _pdf_table(pdf, "Pages viewed", data.get("pages"), "PAGE", "SESSIONS", "Sessions", extra=[("HOST", _fmt_text)], note=PAGES_REPORT_NOTE, wrap=True)
    _ov_keep_together(pdf, data.get("duration"))
    _pdf_table(pdf, "Session duration", data.get("duration"), "BAND", "SESSIONS", "Sessions",
               note=DURATION_REPORT_NOTE.format(single=int(k["SINGLE_INTERACTION_SESSIONS"]), sessions=s))
    _ov_keep_together(pdf, data.get("regions"))
    _pdf_table(pdf, "Where they are", data.get("regions"), "REGION", "SESSIONS", "Sessions",
               note="From the browser timezone, the only location signal in the data.")
    _ov_keep_together(pdf, data.get("sources"))
    _pdf_table(pdf, "How they arrived", data.get("sources"), "SOURCE_GROUP", "SESSIONS", "Sessions", note=ARRIVAL_NOTE)
    if data.get("sources") is not None and not len(data["sources"]):
        _pdf_heading(pdf, "How they arrived")
        _pdf_note(pdf, ARRIVAL_NONE)
    _ov_keep_together(pdf, data.get("referrers"))
    _pdf_table(pdf, "External referrers", data.get("referrers"), "REFERRER", "SESSIONS", "Sessions")
    _pdf_table(pdf, "AI model mix", data.get("ai"), "MODEL", "SESSIONS", "Sessions", extra=[("INTERACTIONS", _fmt_count)], note=AI_MIX_NOTE)
    _pdf_table(pdf, "Content by reach", data.get("assets"), "ASSET", "SESSIONS", "Sessions", extra=[("INTERACTIONS_PER_SESSION", _fmt_tenths)], note=CONTENT_REACH_NOTE)
    _pdf_table(pdf, "PDF read depth", data.get("depth"), "ASSET", "AVG_PAGE_REACHED", "Avg deepest", extra=[("DEEPEST_PAGE", _fmt_count)], note=READ_DEPTH_NOTE)


# ---------------------------------------------------------------- one campaign's PDF
def _overview_pdf(collected, campaign_id, end) -> bytes:
    _OV_SECTIONS.clear()
    month = collected[-1][2]
    label = month["label"]
    pdf = Canvas(footer=_ov_footer, title=_safe(f"Demand AI - {OVERVIEW_TITLE} - {label} - to {end}"))
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.set_margins(15, 14, 15)
    pdf.add_page()
    _pdf_title_block(pdf, month)
    pdf.ln(2)
    for tag, title, data in collected:
        if tag == "MONTH":
            pdf.add_page()
            _ov_band(pdf, tag, title, _OV_THEME[tag][1])
            _ov_month_body(pdf, data)
        else:
            if tag == "WEEK":
                pdf.add_page()
            _ov_tinted_section(pdf, tag, title, data)
    return bytes(pdf.output())


def _overview_filename(campaign_id, end) -> str:
    safe = "".join(c if (c.isalnum() or c in "-_") else "_" for c in str(campaign_id))[:60]
    return f"campaign_overview_{safe}_{end}.pdf"


# ---------------------------------------------------------------- the email body
_OV_MAIL_TEAL, _OV_MAIL_INK, _OV_MAIL_MUTED, _OV_MAIL_RULE = "#056e6e", "#0b1a1a", "#546a6a", "#e6ecec"
_OV_MAIL_TINT = {"DAY": "#ecf4fb", "WEEK": "#fbf6eb", "MONTH": "#e8f6f2"}


def _ov_mail_range(a, b):
    return f"{a.day} {a.strftime('%b')} - {b.day} {b.strftime('%b %Y')}"


def _overview_email_html(reports, end, note=None) -> str:
    """A short summary - sessions per day, week and month for each campaign - with the PDFs attached.
    `note`, when given, is one more line under the table (the form report's, from _form_report_note).
    Tables and inline styles throughout: Outlook ignores most CSS and some clients drop <style>."""
    T, INK, MUTED, RULE, TINT = _OV_MAIL_TEAL, _OV_MAIL_INK, _OV_MAIL_MUTED, _OV_MAIL_RULE, _OV_MAIL_TINT
    rows = []
    for campaign_id, collected in reports:
        periods = {tag: data for tag, _, data in collected}
        cells = "".join(
            f"<td align='right' bgcolor='{TINT[t]}' style='background-color:{TINT[t]};padding:10px 10px;border-bottom:1px solid {RULE};"
            f"font-size:15px;font-weight:600;color:{INK};white-space:nowrap'>{int(periods[t]['kpis']['SESSIONS'] or 0):,}</td>"
            for t in ("DAY", "WEEK", "MONTH"))
        # The ID may break after each underscore (<wbr>), so on a phone it wraps instead of pushing the
        # session columns off-screen.
        wrapped = "_<wbr>".join(html.escape(part) for part in str(campaign_id).split("_"))
        rows.append(f"<tr><td style='padding:10px 10px;border-bottom:1px solid {RULE};font-size:14px;color:{INK}'>"
                    f"<code style='font-size:12px'>{wrapped}</code></td>{cells}</tr>")
    heads = (("DAY", "SESSIONS<br>PER DAY", f"{end.strftime('%a')} {end.day} {end.strftime('%b')}"),
             ("WEEK", "SESSIONS<br>PER WEEK", _ov_mail_range(end - dt.timedelta(days=6), end)),
             ("MONTH", "SESSIONS<br>PER MONTH", _ov_mail_range(end - dt.timedelta(days=29), end)))
    head = "".join(f"<th align='right' bgcolor='{TINT[t]}' style='background-color:{TINT[t]};padding:8px 10px;font-size:11px;"
                   f"letter-spacing:.04em;color:{MUTED};font-weight:600;text-align:right;line-height:1.35'>{label}<br>"
                   f"<span style='font-weight:400;letter-spacing:0'>{dates}</span></th>"
                   for t, label, dates in heads)
    extra = f"<p style='margin:10px 0 0;color:{MUTED};font-size:13px'>{note}</p>" if note else ""
    font = '-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif'  # double quotes: it sits in style='...'
    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'><title>{OVERVIEW_TITLE}</title></head>"
            f"<body style='margin:0;padding:16px;background:#ffffff;color:{INK};font:15px/1.6 {font}'>"
            f"<div style='max-width:760px;margin:0 auto'>"
            f"<table cellpadding='0' cellspacing='0' border='0' style='border-collapse:collapse;margin:0 0 6px'><tr>"
            f"<td width='5' bgcolor='{T}' style='width:5px;background-color:{T}'>&nbsp;</td>"
            f"<td style='padding:0 0 0 12px;font-size:24px;font-weight:700;color:{INK}'>{OVERVIEW_TITLE}</td></tr></table>"
            f"<p style='margin:0 0 20px;color:{MUTED};font-size:14px'>Day, week and month to {end.strftime('%A')} {end.day} "
            f"{end.strftime('%b %Y')} &middot; Internal traffic excluded &middot; Days in UTC</p>"
            f"<p style='margin:0 0 14px'>The full report for each campaign is attached as a PDF.</p>"
            f"<table cellpadding='0' cellspacing='0' border='0' width='100%' style='width:100%;border-collapse:collapse;border:1px solid {RULE}'>"
            f"<thead><tr><th align='left' style='padding:8px 10px;font-size:11px;letter-spacing:.04em;color:{MUTED};font-weight:600;"
            f"text-align:left;background:#f2f7f7'>CAMPAIGN</th>{head}</tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table>"
            f"<p style='margin:16px 0 0;color:{MUTED};font-size:13px'>Each session is counted once, on the day it started.</p>"
            f"{extra}"
            f"<p style='margin:28px 0 0;padding-top:12px;border-top:1px solid {RULE};font-size:13px;color:{MUTED}'>"
            f"<b style='color:{T}'>Demand AI</b> &middot; {OVERVIEW_TITLE}</p>"
            f"</div></body></html>")


# ============================================================================
# 5b. Form submissions - every submission the Day's "Form submit" figure counts, with each form's
#     fields, as a landscape table per form campaign. Added 2026-10-09 from the agreed test report.
#
#     The rows are selected through the email's own scope (_campaign_table + _campaign_scope +
#     campaign_params, _EVENT_BUCKET = 'Form submit', one per MESSAGE_ID), so their count IS the Form
#     submit figure in that campaign's Day section. It is still checked: on a mismatch the PDF is left
#     out and the email says so, rather than sending a number nobody can reconcile.
# ============================================================================

# The report carries personal data (names, emails, phone numbers, messages). While this is False it is
# attached only to CALL ...('TEST') emails. Switched on 2026-10-09 after the TEST email was confirmed:
# the daily email now carries it to ALL_RECIPIENTS every day, "No records registered" days included.
FORM_REPORT_IN_DAILY_EMAIL = True

# (label, form_data key or special, width in mm; None takes the rest). Sized from measured text on a
# landscape A4: the longest report title is 58.4 mm at 7.5pt, email addresses measured up to 35 mm.
FORM_REPORT_CAMPAIGNS = [
    ("DEMO REQUEST", "demand_ai_internal_website_demo_request", [
        ("#", "#", 7), ("Date & time (UTC)", "time", 25), ("First name", "firstName", 22),
        ("Last name", "lastName", 22), ("Email", "email", 46), ("Phone", "phone", 28), ("Company", "company", 30),
        ("Message", "message", None)]),
    ("RESEARCH", "demand_ai_internal_website_research", [
        ("#", "#", 7), ("Date & time (UTC)", "time", 25), ("First name", "firstName", 18),
        ("Last name", "lastName", 18), ("Email", "email", 40), ("Company", "company", 24), ("Job title", "jobTitle", 24),
        ("Country", "country", 16), ("Report", "report", None), ("Edition", "edition", 18), ("Opt-in", "researchOptIn", 11)]),
]
_FR_SPAM_FILL, _FR_SPAM_INK = (253, 236, 234), (160, 40, 30)
_FR_LINE, _FR_PAD = 3.5, 1.6
_FR_LANDSCAPE = (841.89, 595.28)


def _form_rows_sql() -> str:
    return (f"SELECT MESSAGE_ID, MIN(EVENT_TS) AS EVENT_TS, ANY_VALUE(PROPERTIES:form_data) AS FORM_DATA "
            f"FROM {_campaign_table()} {_campaign_scope()} AND ({_EVENT_BUCKET}) = 'Form submit' "
            f"GROUP BY MESSAGE_ID ORDER BY EVENT_TS DESC")


def _form_rows(run, campaign_id, day) -> list:
    rows = run(_form_rows_sql(), campaign_params(day, day, campaign_id)).to_dict("records")
    for r in rows:
        fd = r.get("FORM_DATA")
        r["FORM_DATA"] = json.loads(fd) if isinstance(fd, str) else (fd if isinstance(fd, dict) else {})
    return rows


def _form_internal_count(run, campaign_id, day, kept) -> int:
    """Form submissions the internal-traffic filter removed for this day - a count only."""
    st.session_state["exclude_internal"] = False
    try:
        everything = len(run(_form_rows_sql(), campaign_params(day, day, campaign_id)))
    finally:
        st.session_state["exclude_internal"] = True
    return everything - kept


def _form_email_count(run, campaign_id, day, day_data=None) -> int:
    """The email's own Form submit figure for the Day: the overview's event mix when it was collected,
    else the same query collect() runs."""
    if day_data is not None:
        mix = day_data.get("event_mix") if day_data.get("status") == STATUS_OK else None
    else:
        mix = run(campaign_event_mix_sql(), campaign_params(day, day, campaign_id))
    if mix is None or not len(mix):
        return 0
    return {str(r.BUCKET): int(r.INTERACTIONS or 0) for r in mix.itertuples()}.get("Form submit", 0)


def _form_is_spam(rec) -> bool:
    return str(rec["FORM_DATA"].get("_dai_hp") or "").strip() != ""


def _form_cell(rec, key, n) -> str:
    fd = rec["FORM_DATA"]
    if key == "#":
        return str(n)
    if key == "time":
        # Date and time, always - "22 Sep 23:04". A session counts on the day it started, so a submission
        # just past midnight can sit under the previous day; the date makes that plain.
        ts = rec["EVENT_TS"]
        if not hasattr(ts, "astimezone"):
            return str(ts)
        # EVENT_TS is UTC. A naive value must not go through astimezone(), which would read it as local time.
        utc = ts.replace(tzinfo=dt.timezone.utc) if ts.tzinfo is None else ts.astimezone(dt.timezone.utc)
        return f"{utc.day} {utc.strftime('%b %H:%M')}"
    if key == "phone":
        return " ".join(p for p in (str(fd.get("phoneCountryCode") or "").strip(), str(fd.get("phone") or "").strip()) if p)
    v = fd.get(key)
    return "" if v is None else str(v)


def _form_pdf(day, sections) -> bytes:
    """sections: [(label, campaign_id, cols, rows, email_figure, internal_excluded)]."""
    day_txt = f"{day.strftime('%A')} {_fmt_date(day, year=True)}"
    pdf = Canvas(footer=None, title=_safe(f"Demand AI - Form submissions - {day}"), page_pt=_FR_LANDSCAPE)
    pdf.set_auto_page_break(auto=True, margin=14)
    pdf.set_margins(15, 12, 15)
    width = pdf.w - 30

    def footer(p):
        p.set_y(-9)
        p.set_draw_color(*_PDF_RULE)
        p.line(p.l_margin, p.get_y() - 1.2, p.l_margin + width, p.get_y() - 1.2)
        p.set_font("Helvetica", "B", 6.5)
        p.set_text_color(*_PDF_TEAL)
        p.cell(13, 4, "Demand AI")
        p.set_font("Helvetica", "", 6.5)
        p.set_text_color(*_PDF_MUTED)
        p.cell(width * 0.8 - 13, 4, _safe(f"-   Form submissions on {day_txt} (UTC)   -   Contains personal data: internal use only"))
        p.cell(width * 0.2, 4, f"Page {p.page_no()} of {p.total_pages}", align="R")

    pdf._footer = footer
    pdf.add_page()

    def header_row(cols, ws, continued_label=None):
        if continued_label:
            pdf.set_font("Helvetica", "", 7)
            pdf.set_text_color(*_PDF_MUTED)
            pdf.cell(width, 4, _safe(f"{continued_label} (continued)"), new_x="LMARGIN", new_y="NEXT")
        y = pdf.get_y()
        pdf.set_fill_color(*_PDF_TEAL)
        pdf.rect(pdf.l_margin, y, width, 6, style="F")
        x = pdf.l_margin
        pdf.set_font("Helvetica", "B", 7)
        pdf.set_text_color(255, 255, 255)
        for (name, _, _), w in zip(cols, ws):
            pdf.set_xy(x + _FR_PAD, y + 1)
            pdf.cell(w - 2 * _FR_PAD, 4, name)
            x += w
        pdf.set_xy(pdf.l_margin, y + 6)

    def table(label, cols, rows):
        fixed = sum(w for _, _, w in cols if w)
        ws = [w if w else width - fixed for _, _, w in cols]
        header_row(cols, ws)
        for n, rec in enumerate(rows, start=1):
            pdf.set_font("Helvetica", "", 7.5)
            cells = [pdf._wrap(_safe(_form_cell(rec, key, n)) or "-", w - 2 * _FR_PAD) for (_, key, _), w in zip(cols, ws)]
            row_h = max(len(c) for c in cells) * _FR_LINE + 2 * _FR_PAD
            if pdf.get_y() + row_h > pdf.h - pdf.b_margin:
                pdf.add_page()
                header_row(cols, ws, continued_label=label)
            pdf.set_font("Helvetica", "", 7.5)
            y = pdf.get_y()
            # Every row is counted in the email. Spam-trap hits are marked in red - still counted, but
            # worth knowing before anyone follows one up.
            spam = _form_is_spam(rec)
            fill = _FR_SPAM_FILL if spam else ((250, 252, 252) if n % 2 == 0 else None)
            if fill:
                pdf.set_fill_color(*fill)
                pdf.rect(pdf.l_margin, y, width, row_h, style="F")
            pdf.set_text_color(*(_FR_SPAM_INK if spam else _PDF_INK))
            x = pdf.l_margin
            for lines, w in zip(cells, ws):
                for i, line in enumerate(lines):
                    pdf.set_xy(x + _FR_PAD, y + _FR_PAD + i * _FR_LINE)
                    pdf.cell(w - 2 * _FR_PAD, _FR_LINE, line)
                x += w
            pdf.set_draw_color(*_PDF_RULE)
            pdf.line(pdf.l_margin, y + row_h, pdf.l_margin + width, y + row_h)
            pdf.set_xy(pdf.l_margin, y + row_h)

    y = pdf.get_y()
    pdf.set_fill_color(*_PDF_TEAL)
    pdf.rect(pdf.l_margin, y + 0.8, 1.6, 8.4, style="F")
    pdf.set_xy(pdf.l_margin + 4.2, y)
    pdf.set_font("Helvetica", "B", 16)
    pdf.set_text_color(*_PDF_INK)
    pdf.cell(width - 4.2, 7.4, _safe(f"Form submissions - {day_txt}"), new_x="LMARGIN", new_y="NEXT")
    pdf.set_x(pdf.l_margin + 4.2)
    pdf.set_font("Helvetica", "", 7.5)
    pdf.set_text_color(*_PDF_MUTED)
    pdf.cell(width - 4.2, 4, "Every form submission the Campaign Overview email counts: internal traffic excluded, each session on the day it started (UTC)   -   Newest first   -   Contains personal data: internal use only", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)

    for label, cid, cols, rows, email, gone in sections:
        if pdf.get_y() + 40 > pdf.h - pdf.b_margin:
            pdf.add_page()
        y = pdf.get_y()
        pdf.set_fill_color(212, 230, 246)
        pdf.rect(pdf.l_margin, y, width, 10, style="F")
        pdf.set_fill_color(*_PDF_TEAL)
        pdf.rect(pdf.l_margin, y, 38, 10, style="F")
        pdf.set_xy(pdf.l_margin, y + 2.2)
        pdf.set_font("Helvetica", "B", 10)
        pdf.set_text_color(255, 255, 255)
        pdf.cell(38, 6, label, align="C")
        pdf.set_xy(pdf.l_margin + 43, y + 2.2)
        pdf.set_text_color(*_PDF_INK)
        pdf.cell(width - 48, 6, _safe(cid))
        pdf.set_xy(pdf.l_margin, y + 12)
        gone_txt = f"{gone} internal submission{'s' if gone != 1 else ''} also excluded, as in the campaign report" if gone else ""
        if not rows:
            pdf.set_font("Helvetica", "B", 9.5)
            pdf.set_text_color(*_PDF_INK)
            pdf.cell(width, 7, _safe(f"No records registered for {day_txt}."), new_x="LMARGIN", new_y="NEXT")
            if gone_txt:
                pdf.set_font("Helvetica", "", 8)
                pdf.set_text_color(*_PDF_MUTED)
                pdf.multi_cell(width, 4.2, _safe(gone_txt[0].upper() + gone_txt[1:] + "."), new_x="LMARGIN", new_y="NEXT")
            pdf.ln(6)
            continue
        parts = [f"{len(rows)} form submission{'s' if len(rows) != 1 else ''} - the same as the Form submit figure in the Campaign Overview email for this day ({email})"]
        if gone_txt:
            parts.append(gone_txt)
        spam = sum(_form_is_spam(r) for r in rows)
        if spam:
            parts.append(f"{spam} caught by the spam trap, in red")
        pdf.set_font("Helvetica", "", 8)
        pdf.set_text_color(*_PDF_MUTED)
        pdf.multi_cell(width, 4.2, _safe(" - ".join(parts) + "."), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(1.5)
        table(label, cols, rows)
        pdf.ln(8)
    return bytes(pdf.output())


def _form_report(run, day, overview):
    """(pdf bytes or None, {campaign_id: count}, problem or None). `overview` maps a campaign id to its
    collected periods, so the Day's Form submit figure is read from what the email already fetched."""
    sections, counts = [], {}
    for label, cid, cols in FORM_REPORT_CAMPAIGNS:
        rows = _form_rows(run, cid, day)
        day_data = overview[cid][0][2] if cid in overview else None
        email = _form_email_count(run, cid, day, day_data)
        if len(rows) != email:
            return None, {}, f"{cid} lists {len(rows)} submissions but its Form submit figure is {email}"
        sections.append((label, cid, cols, rows, email, _form_internal_count(run, cid, day, len(rows))))
        counts[cid] = len(rows)
    return _form_pdf(day, sections), counts, None


def _form_report_note(day, counts, problem) -> str:
    """The email body's line about the form report: what is attached, or why it is not."""
    if problem:
        return f"The form submissions report was not attached: {html.escape(problem)}."
    labels = {cid: label.title() for label, cid, _ in FORM_REPORT_CAMPAIGNS}
    listed = ", ".join(f"{labels[cid]} {n}" for cid, n in counts.items())
    return (f"Form submissions on {day.day} {day.strftime('%b')}: {listed}. Every one is listed in the attached "
            f"form report, which contains personal data.")


# ============================================================================
# 6. Glue: Snowpark run() adapter and the SES send.
# ============================================================================

TEST_RECIPIENTS = ["sahil.otavanekar@demandai.co"]
ALL_RECIPIENTS = ["sunil.chandrabhankadam@demandai.co", "sahil.otavanekar@demandai.co", "makarand.prabhu@demandai.co", "prashant.chaudhari@demandai.co",
                  "Marketing@demandai.co"]


def run_report(session: Session, campaign_id: str, window_days: int, exclude_internal_flag: bool) -> str:
    """The daily Campaign Overview email: one PDF per campaign in OVERVIEW_CAMPAIGNS, ending yesterday (UTC).

    The signature is v17's, unchanged, so CREATE OR REPLACE replaces that procedure and the daily task
    calls this one without being altered. The arguments are now:
      campaign_id   'TEST' sends to TEST_RECIPIENTS only, with [TEST] in the subject. Anything else -
                    including the task's 'demand_ai_internal_website_track' - sends to ALL_RECIPIENTS.
      window_days   unused: the periods are always the last 1, 7 and 30 complete days.
      exclude_internal_flag  unused: internal traffic is always excluded, as in v17.
    A 'TEST' call also attaches the form submissions report for the Day (section 5b); other calls do
    only once FORM_REPORT_IN_DAILY_EMAIL is True.
    """
    test = str(campaign_id or "").strip().upper() == "TEST"
    recipients = TEST_RECIPIENTS if test else ALL_RECIPIENTS
    sender = "report@amplifye.org"
    region = "us-east-1"
    end = dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=1)   # the last complete UTC day

    def run(sql, params):
        return session.sql(sql, params=params).to_pandas()

    reports, attachments = [], []
    for cid in OVERVIEW_CAMPAIGNS:
        collected = _overview_collect(run, cid, end)
        reports.append((cid, collected))
        attachments.append((_overview_filename(cid, end), _overview_pdf(collected, cid, end)))

    # The form report holds personal data, so it goes only to TEST until FORM_REPORT_IN_DAILY_EMAIL is set.
    note, forms = None, ""
    if test or FORM_REPORT_IN_DAILY_EMAIL:
        form_pdf, counts, problem = _form_report(run, end, dict(reports))
        note = _form_report_note(end, counts, problem)
        if form_pdf is not None:
            attachments.append((f"form_submissions_{end}.pdf", form_pdf))
            forms = " | form submissions: " + ", ".join(f"{cid}={n}" for cid, n in counts.items())
        else:
            forms = " | form report NOT attached: " + problem

    subject = f"{OVERVIEW_TITLE}: {len(OVERVIEW_CAMPAIGNS)} campaigns - to {end.day} {end.strftime('%b %Y')}"
    if test:
        subject = "[TEST] " + subject

    creds = _snowflake.get_username_password("aws_cred")
    access_key = creds.username
    secret_key = creds.password
    if not access_key or not secret_key:
        raise RuntimeError("AWS creds not found; ensure SECRETS=('aws_cred'=DEV_MDLH.TARGET.SES_SECRET_MDLH_DAILY_REPORT).")

    # multipart/mixed: the HTML summary, then one application/pdf part per campaign (+ the form report).
    msg = MIMEMultipart("mixed")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(_overview_email_html(reports, end, note), "html", "utf-8"))
    for name, pdf_bytes in attachments:
        part = MIMEApplication(pdf_bytes, _subtype="pdf")
        part.add_header("Content-Disposition", "attachment", filename=name)
        msg.attach(part)

    ses = boto3.client("sesv2", region_name=region, aws_access_key_id=access_key, aws_secret_access_key=secret_key)
    ses.send_email(
        FromEmailAddress=sender,
        Destination={"ToAddresses": recipients},
        Content={"Raw": {"Data": msg.as_bytes()}},
    )

    sessions = "; ".join(f"{cid}=" + "/".join(f"{int(d['kpis']['SESSIONS'] or 0):,}" for _, _, d in collected)
                         for cid, collected in reports)
    return (f"SES email sent to: {', '.join(recipients)} | {OVERVIEW_TITLE} to {end} | "
            f"sessions day/week/month: {sessions}{forms}")
