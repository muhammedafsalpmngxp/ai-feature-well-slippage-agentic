"""Assemble the slippage detection graph.

    detect -> planner -> sql_author -> validator -> executor -> verifier -> synthesize
                              ^            |            |           |
                              +------------+------------+-----------+
                                    (rewrite on rejection or failure)

Every loop back to the SQL Author carries a specific instruction saying what to change. Two
budgets govern them, deliberately separate: MAX_SQL_RETRIES for a query that was unsafe or
failed to run, and VERIFY_RETRIES for one the reviewer rejected. Sharing a single counter lets
a couple of unrelated syntax errors early in a run leave the Verifier with zero rewrites - it
would reject, and be overruled immediately.

Every exhausted path leads to the Synthesizer rather than to an exception. A run that cannot
produce a trusted listing still has to say so, and say why.
"""
from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from app.config import settings
from app.db.introspect import detect, grounding
from app.graph.nodes.executor import executor_node
from app.graph.nodes.planner import planner_node
from app.graph.nodes.sql_author import sql_author_node
from app.graph.nodes.synthesize import synthesize_node
from app.graph.nodes.validator import validator_node
from app.graph.nodes.verifier import verifier_node
from app.graph.state import SlippageState
from app.observability import get_logger

log = get_logger()


def detect_node(state: SlippageState) -> dict:
    """Step 1 - introspect the live database into a schema block and value hints."""
    schema, hints = detect(use_cache=not state.get("refresh_schema", False))
    return {"schema": schema, "value_hints": hints, "grounding": grounding(schema, hints)}


def _repeated_sql(state: SlippageState) -> bool:
    """True when the author re-emitted a query already tried, AFTER a rejection.

    Identical SQL returns identical data, so re-verifying it cannot produce a different verdict -
    it only burns time.

    Gated on verify_retry_count deliberately: a repeat following a TRANSIENT database error
    (a deadlock, a dropped connection) is a legitimate retry that may well succeed the second
    time, and must not be short-circuited.
    """
    if state.get("verify_retry_count", 0) < 1:
        return False
    tried = state.get("tried_sql") or []
    return len(tried) > 1 and tried[-1] in tried[:-1]


def _route_after_validator(state: SlippageState) -> str:
    if state.get("validation_error"):
        if state.get("retry_count", 0) <= settings.max_sql_retries:
            return "sql_author"
        log.warning("route: safety retries exhausted - explaining without a result")
        return "synthesize"
    if _repeated_sql(state):
        # The cheapest possible exit from a loop that cannot converge: no LLM call, no round trip.
        log.warning("route: the author re-emitted a query already tried - stopping the loop")
        return "synthesize"
    return "executor"


def _route_after_executor(state: SlippageState) -> str:
    if state.get("exec_error"):
        if state.get("retry_count", 0) <= settings.max_sql_retries:
            return "sql_author"
        log.warning("route: execution retries exhausted - explaining without a result")
        return "synthesize"
    return "verifier"


def _route_after_verifier(state: SlippageState) -> str:
    if not state.get("verify_ok") and state.get("verify_retry_count", 0) <= settings.verify_retries:
        return "sql_author"
    return "synthesize"


def build_graph():
    graph = StateGraph(SlippageState)
    graph.add_node("detect", detect_node)
    graph.add_node("planner", planner_node)
    graph.add_node("sql_author", sql_author_node)
    graph.add_node("validator", validator_node)
    graph.add_node("executor", executor_node)
    graph.add_node("verifier", verifier_node)
    graph.add_node("synthesize", synthesize_node)

    graph.add_edge(START, "detect")
    graph.add_edge("detect", "planner")
    graph.add_edge("planner", "sql_author")
    graph.add_edge("sql_author", "validator")
    graph.add_conditional_edges(
        "validator",
        _route_after_validator,
        {"sql_author": "sql_author", "executor": "executor", "synthesize": "synthesize"},
    )
    graph.add_conditional_edges(
        "executor",
        _route_after_executor,
        {"sql_author": "sql_author", "verifier": "verifier", "synthesize": "synthesize"},
    )
    graph.add_conditional_edges(
        "verifier",
        _route_after_verifier,
        {"sql_author": "sql_author", "synthesize": "synthesize"},
    )
    graph.add_edge("synthesize", END)
    return graph.compile()
