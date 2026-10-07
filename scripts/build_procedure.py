"""Build CIT_DATA_CORE.TRACKING.CAMPAIGN_ANALYTICS_REPORT_DAILY - the daily Campaign Overview email -
from the project source.

    python scripts/build_procedure.py OUTPUT.sql [--body BODY.py] [--version v19]

The procedure body is: a stand-in for the three things src/db.py takes from Streamlit, then
src/db.py, src/queries.py, src/minipdf.py and src/campaign_report.py embedded UNCHANGED (only their
import lines removed - everything shares one namespace there), then scripts/procedure_overview.py.
Built rather than hand-copied so the email cannot drift from the dashboard: change src/, rebuild,
paste the output into a Snowsight worksheet and run it.

--body also writes the Python alone, which is what a local dry run executes.

Checks before anything is written: every edit to the sources applied exactly once; no Streamlit or
src import left; no $$ (it would end the dollar-quoted body); the body parses as Python 3.11 - the
procedure's runtime - and has none of the f-string forms ast's feature_version still accepts but
3.11 rejects (a nested quote reusing the f-string's own, or a backslash in an expression).

Snowflake allows a procedure definition up to 1 MB; the build is ~250 KB.
"""
import argparse
import ast
import datetime as dt
import io
import re
import sys
import tokenize
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def remove_once(text: str, old: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        sys.exit(f"{label}: expected exactly one match, found {n} - has the source changed?")
    return text.replace(old, "")


def py311_fstring_problems(text: str) -> list:
    """What ast's feature_version misses: in 3.11 an f-string's {expressions} may not reuse the
    f-string's own quote character, nor contain a backslash. Run on 3.11 itself, ast.parse already
    enforces both, and the tokenizer has no f-string tokens to inspect - so there is nothing to add."""
    if not hasattr(tokenize, "FSTRING_START"):
        return []
    problems, stack = [], []
    for tok in tokenize.generate_tokens(io.StringIO(text).readline):
        if tok.type == tokenize.FSTRING_START:
            if stack and tok.string.lstrip("rRfFbB")[0] in [q for q, _ in stack]:
                problems.append((tok.start[0], "nested f-string reuses an enclosing quote"))
            quote = tok.string.lstrip("rRfFbBuU")
            stack.append((quote[0], quote))
        elif tok.type == tokenize.FSTRING_END:
            stack.pop()
        elif stack and tok.type == tokenize.STRING:
            if tok.string.lstrip("rRbBuU")[0] in [q for q, _ in stack]:
                problems.append((tok.start[0], f"string {tok.string!r} reuses the f-string's quote"))
            if "\\" in tok.string:
                problems.append((tok.start[0], f"backslash in f-string expression {tok.string!r}"))
    return problems


PRELUDE = '''from __future__ import annotations

# ============================================================================
# CAMPAIGN_ANALYTICS_REPORT_DAILY - the daily Campaign Overview email.
# Built by scripts/build_procedure.py; sections 1-4 are the project's own files,
# embedded unchanged apart from their import lines.
# ============================================================================
import datetime as dt
import html
import math
import re
import zlib
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import boto3
import pandas as pd
from snowflake.snowpark import Session
import _snowflake


class _StreamlitShim:
    """Stands in for the three things src/db.py takes from Streamlit, which a stored procedure does not
    have. Internal traffic is always excluded; results are never cached; and there is no Streamlit
    connection - run_report() passes Snowpark's session to every query instead."""
    session_state = {"exclude_internal": True}

    @staticmethod
    def cache_data(*args, **kwargs):
        def wrap(func):
            return func
        return wrap

    @staticmethod
    def connection(*args, **kwargs):
        raise RuntimeError("st.connection is not available inside the stored procedure")


st = _StreamlitShim()
'''

HEADER = '''-- =====================================================================================
-- CAMPAIGN_ANALYTICS_REPORT_DAILY - {version}, built from the project source on {today}
-- by scripts/build_procedure.py.
--
-- The daily Campaign Overview email: one email with a short summary (sessions per day, per week and
-- per month for each campaign) and one PDF per campaign attached. The campaigns and recipients are
-- set in scripts/procedure_overview.py. Same name, arguments, packages, integration and secret as
-- before, so CREATE OR REPLACE replaces it in place and the daily task calls it unchanged.
--
--     CALL CIT_DATA_CORE.TRACKING.CAMPAIGN_ANALYTICS_REPORT_DAILY('TEST');  -- test: test recipient only, [TEST] subject
--     anything else, including the task's call                               -- all recipients
--   WINDOW_DAYS and EXCLUDE_INTERNAL are accepted and ignored.
--
-- HOW TO RUN: paste this file into a Snowsight worksheet and run it with R_CIT_DATA_CORE_ADMIN_DEV
-- (the procedure's owner). Creating the procedure sends no email.
-- =====================================================================================
CREATE OR REPLACE PROCEDURE CIT_DATA_CORE.TRACKING.CAMPAIGN_ANALYTICS_REPORT_DAILY("CAMPAIGN_ID" VARCHAR DEFAULT 'demand_ai_internal_website_track', "WINDOW_DAYS" NUMBER(38,0) DEFAULT 30, "EXCLUDE_INTERNAL" BOOLEAN DEFAULT TRUE)
RETURNS VARCHAR
LANGUAGE PYTHON
RUNTIME_VERSION = '3.11'
PACKAGES = ('snowflake-snowpark-python','boto3','pandas','tzdata','matplotlib')
HANDLER = 'run_report'
EXTERNAL_ACCESS_INTEGRATIONS = (SES_INTEGRATION_MDLH_DAILY_REPORT)
SECRETS = ('aws_cred'=DEV_MDLH.TARGET.SES_SECRET_MDLH_DAILY_REPORT)
EXECUTE AS OWNER
AS $$
'''


def section(title: str, text: str) -> str:
    bar = "# " + "=" * 76
    return f"\n\n{bar}\n# {title}\n{bar}\n{text}\n"


def build() -> str:
    src = REPO / "src"
    db = read(src / "db.py")
    db = remove_once(db, "import pandas as pd\n", "src/db.py pandas import")
    db = remove_once(db, "import streamlit as st\n", "src/db.py streamlit import")

    queries = read(src / "queries.py")
    queries = remove_once(queries, "from src.db import PAGE_HOST, TABLE_FQN, campaign_keep_predicate, table_fqn\n", "src/queries.py db import")

    minipdf = read(src / "minipdf.py")
    minipdf = remove_once(minipdf, "from __future__ import annotations\n", "src/minipdf.py future import")

    report = read(src / "campaign_report.py")
    report = remove_once(report, "from __future__ import annotations\n", "src/campaign_report.py future import")
    block = re.search(r"from src\.queries import \(\n(?:    [A-Za-z_]+,\n)+\)\n", report)
    if not block:
        sys.exit("src/campaign_report.py: the queries import block was not found - has the source changed?")
    report = report.replace(block.group(0), "")
    report = remove_once(report, "        from src.minipdf import Canvas\n", "src/campaign_report.py lazy Canvas import")

    embedded = db + queries + minipdf + report
    left = [line for line in embedded.splitlines() if re.match(r"\s*(from src\.\w+ import |import src|import streamlit|from streamlit)", line)]
    if left:
        sys.exit(f"an import would fail inside the procedure: {left}")

    body = (PRELUDE
            + section("1. src/db.py - the internal-traffic filter", db)
            + section("2. src/queries.py - every SQL builder", queries)
            + section("3. src/minipdf.py - the PDF writer (zlib only)", minipdf)
            + section("4. src/campaign_report.py - collect() and the report's drawing helpers", report)
            + "\n\n" + read(REPO / "scripts" / "procedure_overview.py"))

    if "$$" in body:
        sys.exit("the body contains $$, which would end the dollar-quoted definition")
    ast.parse(body, feature_version=(3, 11))
    problems = py311_fstring_problems(body)
    if problems:
        sys.exit(f"f-strings Python 3.11 rejects: {problems[:5]}")
    return body


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("output", help="the .sql file to write")
    ap.add_argument("--body", help="also write the procedure's Python alone, for a local dry run")
    ap.add_argument("--version", default="v19", help="label for the header comment (v18 is the first built version)")
    args = ap.parse_args()

    body = build()
    sql = HEADER.format(version=args.version, today=dt.date.today()) + body + "\n$$;\n"
    Path(args.output).write_text(sql, encoding="utf-8", newline="\n")
    if args.body:
        Path(args.body).write_text(body, encoding="utf-8", newline="\n")
    print(f"Wrote {args.output}: {len(sql.encode()) / 1024:.0f} KB (limit 1 MB), {sql.count(chr(10)):,} lines")


if __name__ == "__main__":
    main()
