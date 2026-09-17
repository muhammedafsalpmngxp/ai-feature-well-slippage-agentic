"""Assemble the detection graph.

    detect -> planner -> dispatch -> sql_author -> validator -> executor -> verifier -+
                            ^  |         ^            |            |           |      |
                            |  |         +------------+------------+-----------+      |
                            |  |               (rewrite on rejection or failure)      |
                            |  +-> synthesize (when the worklist is empty)            |
                            +------------------------------------------------------- +
                                       (file the result, take the next query)

Two queries run through ONE loop, one at a time - see nodes/dispatch.py for why.

Every loop back to the SQL Author carries a specific instruction saying what to change. Two
budgets govern them, deliberately separate: MAX_SQL_RETRIES for a query that was unsafe or
failed to run, and VERIFY_RETRIES for one the reviewer rejected. Sharing a single counter lets
a couple of unrelated syntax errors early on leave the Verifier with zero rewrites - it would
reject, and be overruled immediately.

Every exhausted path leads back to dispatch, never to an exception, so one query that cannot be
produced does not take the other down with it. The Synthesizer then reports what is missing.
"""
from __future__ import annotations

import time

from langgraph.graph import END, START, StateGraph

from app.config import settings
from app.db.introspect import detect, grounding
from app.graph.nodes.dispatch import dispatch_node, route_after_dispatch
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
        log.warning("route[%s]: safety retries exhausted - giving up on this query",
                    state.get("current_query", "?"))
        return "dispatch"
    if _repeated_sql(state):
        # The cheapest possible exit from a loop that cannot converge: no LLM call, no round trip.
        log.warning("route[%s]: author re-emitted a query already tried - stopping the loop",
                    state.get("current_query", "?"))
        return "dispatch"
    return "executor"


def _route_after_executor(state: SlippageState) -> str:
    if state.get("exec_error"):
        if state.get("retry_count", 0) <= settings.max_sql_retries:
            return "sql_author"
        log.warning("route[%s]: execution retries exhausted - giving up on this query",
                    state.get("current_query", "?"))
        return "dispatch"
    return "verifier"


def _route_after_verifier(state: SlippageState) -> str:
    if not state.get("verify_ok") and state.get("verify_retry_count", 0) <= settings.verify_retries:
        return "sql_author"
    return "dispatch"


def _traced(name: str, fn):
    """Log every node entry, so the agent flow is visible as it happens.

    The per-node logs already say what each step DID ("exec: 215 rows", "verify: ok"), but
    nothing said which node was entered, or in what order - so a run that looped between the
    author and the verifier looked identical in the log to one that went straight through.
    This makes the path itself legible, which is the only way to see a routing mistake.

    A plain wrapper rather than LangGraph's own tracing: it needs no extra dependency, and it
    prints to the same stream as everything else, so one log tells the whole story.
    """

    def wrapper(state: SlippageState) -> dict:
        _STEP["n"] += 1
        step = _STEP["n"]
        current = state.get("current_query") or ""
        label = name + ("  (" + current + ")" if current else "")
        log.info("  [%02d] >>> %s", step, label)
        started = time.perf_counter()
        try:
            return fn(state)
        finally:
            # In a finally block so a node that RAISES is still timed and still closes its
            # bracket. Without that, a failure leaves an unmatched ">>>" and the log stops
            # mid-sentence exactly where it is most worth reading.
            log.info("  [%02d] <<< %-28s %6.1fs", step, label, time.perf_counter() - started)

    wrapper.__name__ = name
    return wrapper


# Step counter for the trace above. One graph invocation per process in the CLI, which is what
# this exists for; it is only a label, so a shared count across concurrent runs would misnumber
# lines but never affect behaviour.
_STEP = {"n": 0}


def build_graph():
    graph = StateGraph(SlippageState)
    graph.add_node("detect", _traced("detect", detect_node))
    graph.add_node("planner", _traced("planner", planner_node))
    graph.add_node("dispatch", _traced("dispatch", dispatch_node))
    graph.add_node("sql_author", _traced("sql_author", sql_author_node))
    graph.add_node("validator", _traced("validator", validator_node))
    graph.add_node("executor", _traced("executor", executor_node))
    graph.add_node("verifier", _traced("verifier", verifier_node))
    graph.add_node("synthesize", _traced("synthesize", synthesize_node))

    graph.add_edge(START, "detect")
    graph.add_edge("detect", "planner")
    graph.add_edge("planner", "dispatch")
    graph.add_conditional_edges(
        "dispatch",
        route_after_dispatch,
        {"sql_author": "sql_author", "synthesize": "synthesize"},
    )
    graph.add_edge("sql_author", "validator")
    graph.add_conditional_edges(
        "validator",
        _route_after_validator,
        {"sql_author": "sql_author", "executor": "executor", "dispatch": "dispatch"},
    )
    graph.add_conditional_edges(
        "executor",
        _route_after_executor,
        {"sql_author": "sql_author", "verifier": "verifier", "dispatch": "dispatch"},
    )
    graph.add_conditional_edges(
        "verifier",
        _route_after_verifier,
        {"sql_author": "sql_author", "dispatch": "dispatch"},
    )
    graph.add_edge("synthesize", END)
    return graph.compile()
