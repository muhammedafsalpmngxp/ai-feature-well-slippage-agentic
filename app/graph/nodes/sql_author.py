"""SQL Author - writes one read-only T-SQL SELECT for the slippage listing.

Step 3. Every reason a previous attempt failed is appended to the prompt as a specific
instruction, so a rewrite is told what to change rather than asked to try again.
"""
from __future__ import annotations

import re

from app.graph.nodes.planner import render_plan
from app.graph.prompts import sql_author_system
from app.graph.state import SlippageState
from app.llm import chat
from app.observability import get_logger
from app.utils import extract_sql

log = get_logger()

_WS = re.compile(r"\s+")


def normalize_sql(sql: str) -> str:
    """Whitespace- and case-insensitive form, for recognising a query already tried.

    Only ever used to detect repeats - never to alter what is executed. Whitespace is collapsed
    BEFORE the trailing semicolon is trimmed, or "SELECT 1 ;" keeps a trailing space and compares
    unequal to "SELECT 1".
    """
    return _WS.sub(" ", (sql or "")).strip().rstrip(";").strip().lower()


def sql_author_node(state: SlippageState) -> dict:
    parts = [
        "DETECTED DATABASE (the ONLY tables and columns that exist - never use others):",
        state.get("grounding", ""),
        "",
        render_plan(state),
        "",
        "Write the slippage listing query now.",
    ]

    if state.get("plan_notes"):
        parts.append(
            "\nTHE PLANNER COULD NOT BIND EVERYTHING:\n" + state["plan_notes"]
            + "\nResolve what you can from the schema block. For a scenario you genuinely cannot "
            "source, return its status as the data-quality value rather than inventing a column."
        )

    if state.get("validation_error"):
        parts.append(
            "\nYOUR PREVIOUS SQL WAS REJECTED BY THE SAFETY VALIDATOR:\n"
            + state["validation_error"] + "\nRewrite it as a single safe SELECT."
        )

    if state.get("exec_error"):
        parts.append(
            "\nYOUR PREVIOUS SQL FAILED WHEN RUN. Database error:\n"
            + state["exec_error"] + "\nFix the query - check table and column names, joins, types."
        )

    if state.get("contract_errors"):
        parts.append(
            "\nYOUR PREVIOUS SQL RETURNED THE WRONG COLUMNS:\n"
            + "\n".join("- " + e for e in state["contract_errors"])
            + "\nReturn exactly the contract columns, spelled exactly, each with an explicit alias."
        )

    if state.get("verify_feedback"):
        # Show the exact SQL being criticised. Without it the writer has to infer which query the
        # feedback refers to, and frequently re-emits the same one.
        previous = state.get("sql", "")
        parts.append(
            "\nAN INDEPENDENT VERIFIER REVIEWED YOUR PREVIOUS SQL AND REJECTED IT."
            + ("\n\nYOUR PREVIOUS SQL WAS:\n" + previous if previous else "")
            + "\n\nWHAT TO FIX:\n" + state["verify_feedback"]
            + "\nWrite a DIFFERENT query that addresses this. Do NOT resubmit the same SQL - it "
            "would return the same data and be rejected again. If you believe the previous query "
            "was already correct, make the smallest change that satisfies the feedback."
        )

    if state.get("tried_sql"):
        parts.append(
            "\nYou have already tried " + str(len(state["tried_sql"])) + " quer(y/ies) this run. "
            "Each new attempt must be materially different from the previous ones."
        )

    sql = extract_sql(chat(sql_author_system(), "\n".join(p for p in parts if p), temperature=0.0))
    log.info("sql: attempt %d written (%d chars)", state.get("retry_count", 0) + 1, len(sql))

    # `verify_feedback` is deliberately NOT cleared here. It is the Verifier's semantic
    # instruction and stays valid until the Verifier next rules on the rewrite. Clearing it now
    # would drop it the moment the rewrite tripped the validator or the database: the NEXT
    # attempt would see only the syntax error, lose the reason the query was rejected, and be
    # free to drift back to the SQL that had already been rejected. The Verifier clears it itself
    # once it has judged the new query.
    return {
        "sql": sql,
        "validation_error": "",
        "exec_error": "",
        "contract_errors": [],
        "tried_sql": state.get("tried_sql", []) + [normalize_sql(sql)],
    }
