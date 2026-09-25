"""The author/verify loop for ONE query, as a self-contained graph.

Extracted from build.py so the same loop can be run three times CONCURRENTLY, each with its own
isolated state. That isolation is the whole reason this is a separate graph rather than a fan-out
inside the main one: the working fields - `sql`, `columns`, `rows`, `retry_count`,
`verify_feedback` - are single-valued per query. Three branches sharing one state object would
overwrite each other's SQL and feed one query's rejection back to another's author.

The routing here is identical to the sequential version it replaces. A query that exhausts its
budget ends rather than raising, so one failure cannot take the other two down with it.
"""
from __future__ import annotations

import time

from langgraph.graph import END, START, StateGraph

from app.config import settings
from app.graph.nodes.executor import executor_node
from app.graph.nodes.sql_author import sql_author_node
from app.graph.nodes.validator import validator_node
from app.graph.nodes.verifier import verifier_node
from app.graph.state import SlippageState
from app.observability import get_logger

log = get_logger()


def _repeated_sql(state: SlippageState) -> bool:
    """True when the author re-emitted a query already tried, AFTER a rejection.

    Identical SQL returns identical data, so re-verifying it cannot produce a different verdict.
    Gated on verify_retry_count deliberately: a repeat after a TRANSIENT database error is a
    legitimate retry that may well succeed, and must not be short-circuited.
    """
    if state.get("verify_retry_count", 0) < 1:
        return False
    tried = state.get("tried_sql") or []
    return len(tried) > 1 and tried[-1] in tried[:-1]


def _after_validator(state: SlippageState) -> str:
    key = state.get("current_query", "?")
    if state.get("validation_error"):
        if state.get("retry_count", 0) <= settings.max_sql_retries:
            return "sql_author"
        log.warning("[%s] safety retries exhausted - giving up on this query", key)
        return END
    if _repeated_sql(state):
        log.warning("[%s] author re-emitted a query already tried - stopping the loop", key)
        return END
    return "executor"


def _after_executor(state: SlippageState) -> str:
    key = state.get("current_query", "?")
    if state.get("exec_error"):
        if state.get("retry_count", 0) <= settings.max_sql_retries:
            return "sql_author"
        log.warning("[%s] execution retries exhausted - giving up on this query", key)
        return END
    return "verifier"


def _after_verifier(state: SlippageState) -> str:
    if not state.get("verify_ok") and state.get("verify_retry_count", 0) <= settings.verify_retries:
        return "sql_author"
    return END


def _traced(name: str, fn):
    """Label every node line with its query, since three runs interleave in one log.

    The sequential version numbered steps globally. With three queries in flight that number
    means nothing - the query key is what tells you which stream a line belongs to.
    """

    def wrapper(state: SlippageState) -> dict:
        key = state.get("current_query", "?")
        log.info("[%s] >>> %s", key, name)
        started = time.perf_counter()
        try:
            return fn(state)
        finally:
            # finally, so a node that RAISES is still timed and still closes its bracket.
            log.info("[%s] <<< %-11s %6.1fs", key, name, time.perf_counter() - started)

    wrapper.__name__ = name
    return wrapper


def build_query_graph():
    """author -> validator -> executor -> verifier, looping back on any rejection."""
    graph = StateGraph(SlippageState)
    graph.add_node("sql_author", _traced("sql_author", sql_author_node))
    graph.add_node("validator", _traced("validator", validator_node))
    graph.add_node("executor", _traced("executor", executor_node))
    graph.add_node("verifier", _traced("verifier", verifier_node))

    graph.add_edge(START, "sql_author")
    graph.add_edge("sql_author", "validator")
    graph.add_conditional_edges(
        "validator", _after_validator,
        {"sql_author": "sql_author", "executor": "executor", END: END},
    )
    graph.add_conditional_edges(
        "executor", _after_executor,
        {"sql_author": "sql_author", "verifier": "verifier", END: END},
    )
    graph.add_conditional_edges(
        "verifier", _after_verifier, {"sql_author": "sql_author", END: END},
    )
    return graph.compile()
