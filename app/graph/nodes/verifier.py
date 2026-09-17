"""Verifier - checks the query and its rows BEFORE anything is explained.

Step 5. Running before the Synthesizer rather than after is deliberate: a rejection here sends a
real fix back to the SQL Author (new query, new rows), instead of asking the Synthesizer to
reword an explanation built on data that was already wrong - which it has no way to fix.

Two layers. The deterministic findings from sqlcheck are handed to the LLM as evidence to
ADJUDICATE, not as orders, because that module is conservative and can flag a query that is fine.
The contract check is different: a column-name mismatch is a fact, not a judgement, and it is
enforced here without asking.
"""
from __future__ import annotations

from app.config import settings
from app.graph import sqlcheck
from app.graph.nodes.planner import render_plan
from app.graph.prompts import VERIFIER_SYSTEM
from app.graph.state import SlippageState
from app.llm import chat
from app.observability import get_logger
from app.utils import extract_json

log = get_logger()

# Sentinel: extract_json hands this back when it cannot parse a verdict, so a parse failure stays
# distinguishable from a genuine {"ok": true}.
_UNREADABLE: dict = {"__unreadable__": True}

# Feedback is re-sent on every rewrite, so it needs a bound. A hard slice is dangerous though: it
# can cut mid-word, and the cut can land inside the one instruction the author is meant to act
# on. The cap is generous enough to rarely bind, and always cuts at a sentence or line boundary.
MAX_FEEDBACK_CHARS = 1500

PREVIEW_ROWS = 15


def _trim_feedback(text: str) -> str:
    text = (text or "").strip()
    if len(text) <= MAX_FEEDBACK_CHARS:
        return text
    window = text[:MAX_FEEDBACK_CHARS]
    cut = max(window.rfind(". "), window.rfind(".\n"), window.rfind("\n"), window.rfind("; "))
    if cut < MAX_FEEDBACK_CHARS // 2:  # no usable boundary late enough - fall back to a word break
        cut = window.rfind(" ")
    if cut <= 0:
        cut = MAX_FEEDBACK_CHARS
    return window[: cut + 1].rstrip() + " [...truncated]"


def render_rows(columns, rows, limit: int = PREVIEW_ROWS) -> str:
    """A compact markdown table of the result, with an explicit truncation note."""
    columns = [str(c) for c in (columns or [])]
    rows = rows or []
    if not columns:
        return "(no columns)"
    if not rows:
        return "(no rows returned)"

    shown = rows[:limit]
    header = "| " + " | ".join(columns) + " |"
    separator = "| " + " | ".join("---" for _ in columns) + " |"
    body = [
        "| " + " | ".join("" if v is None else str(v) for v in row) + " |"
        for row in shown
    ]
    table = "\n".join([header, separator] + body)
    if len(rows) > limit:
        table += "\n\n(showing " + str(limit) + " of " + str(len(rows)) + " rows)"
    return table


def _reject(state: SlippageState, feedback: str, contract_errors: list[str] | None = None) -> dict:
    """Set the result aside; never destroy it.

    Keeping the rejected rows means that if the budget runs out, the Synthesizer can still
    explain them WITH a caveat rather than explaining nothing - and the run has a record that
    this query was tried, which is what stops the author re-emitting it.
    """
    rejected = state.get("rejected", []) + [
        {
            "sql": state.get("sql", ""),
            "columns": state.get("columns", []),
            "rows": state.get("rows", []),
            "feedback": feedback,
        }
    ]
    return {
        "verify_ok": False,
        "verify_feedback": _trim_feedback(feedback),
        "contract_errors": contract_errors or [],
        "rejected": rejected,
        "verify_retry_count": state.get("verify_retry_count", 0) + 1,
        # Reset because this rejection DISCARDS the result above: the work has to be redone, so
        # it must be funded. Unrelated syntax errors earlier in the run could otherwise have
        # spent the budget already, leaving a fixable problem with no attempts left. Not
        # unbounded - verify_retry_count still caps how often this path can be taken at all.
        "retry_count": 0,
    }


def verifier_node(state: SlippageState) -> dict:
    columns = state.get("columns", [])
    rows = state.get("rows", [])
    sql = state.get("sql", "")
    schema = state.get("schema", "")

    # -- Deterministic first. A contract mismatch needs no LLM opinion. -------------
    contract_errors = sqlcheck.check_contract(columns)
    if contract_errors:
        log.warning("verify: contract mismatch - %s", "; ".join(contract_errors))
        return _reject(
            state,
            "The returned columns do not match the output contract: "
            + "; ".join(contract_errors)
            + ". Return exactly the contract columns, spelled exactly, each with an explicit alias.",
            contract_errors=contract_errors,
        )

    findings = sqlcheck.findings(sql, schema, columns=None)

    # -- Then the independent review ------------------------------------------------
    # The schema block is large and byte-identical on every call, so it goes FIRST where a
    # provider can prefix-cache it. Everything below varies per attempt and would defeat that.
    parts = [
        "DETECTED DATABASE (the ONLY tables and columns that exist - judge the SQL against this):",
        schema,
    ]
    plan = render_plan(state)
    if plan:
        parts.append(plan)
    parts.append("EXECUTED SQL:\n" + sql)
    parts.append(
        "RETRIEVED DATA (" + str(len(rows)) + " rows"
        + (", CAPPED" if state.get("truncated") else "") + "):\n"
        + render_rows(columns, rows)
    )
    if findings:
        parts.append(
            "DETERMINISTIC FINDINGS TO ADJUDICATE (derived from the schema's own markers and from "
            "milestone_rules, NOT from an opinion - decide whether each is real for this query, "
            "and reject only if it genuinely affects the result):\n"
            + "\n".join("- " + f for f in findings)
        )
        log.info("verify: %d deterministic finding(s) handed to the reviewer", len(findings))
    parts.append("Is this a correct slippage listing? Reply with the JSON verdict.")

    raw = chat(VERIFIER_SYSTEM, "\n\n".join(parts), temperature=0.0)
    verdict = extract_json(raw, default=_UNREADABLE)

    # Fail CLOSED. Defaulting an unreadable verdict to ok=True lets a malformed reply - a
    # trailing comma is enough - silently turn a rejection into an approval, with nothing logged.
    if verdict is _UNREADABLE or "ok" not in verdict:
        log.warning("verify: verdict unreadable (%d chars) - failing closed", len(raw or ""))
        return _reject(
            state,
            "The automated review could not be completed. Re-check the query against the schema "
            "and the milestone rules, and rewrite it if anything looks wrong.",
        )

    if bool(verdict.get("ok")):
        log.info("verify: ok")
        # Cleared HERE, not in the SQL Author - that is what lets the feedback survive a failed
        # rewrite. This is the point at which it is genuinely spent: the rewrite has been judged.
        return {"verify_ok": True, "verify_feedback": "", "contract_errors": []}

    feedback = str(verdict.get("feedback", ""))
    log.info("verify: rejected - %s", feedback)
    return _reject(state, feedback)
