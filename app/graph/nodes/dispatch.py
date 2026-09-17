"""Dispatch - the controller that runs each query through the author/verify loop in turn.

This project produces TWO queries (milestone_rules §5.11). They are authored and verified ONE AT
A TIME through the same nodes rather than by two parallel branches, for one reason: every
correctness guarantee in the loop - the safety gate, the contract check, the fail-closed verdict,
the two retry budgets - then applies to both by construction. A second branch would be a second
copy of that logic, and the copy that drifts is always the one nobody is looking at.

This node runs at both ends of the loop. On the way out it files the finished query's result; on
the way in it takes the next one off the worklist and resets the per-query working fields. Every
give-up path in build.py routes here too, so a query that exhausts its budget does not take the
other one down with it.
"""
from __future__ import annotations

from app.graph import queries
from app.graph.state import SlippageState
from app.observability import get_logger

log = get_logger()


def dispatch_node(state: SlippageState) -> dict:
    results = dict(state.get("results") or {})

    # -- File the query that just finished, if any -----------------------------
    finished = state.get("current_query", "")
    if finished:
        verified = bool(state.get("verify_ok"))
        results[finished] = {
            "sql": state.get("sql", ""),
            "columns": state.get("columns", []),
            "rows": state.get("rows", []),
            "row_count": len(state.get("rows") or []),
            "truncated": bool(state.get("truncated")),
            "verified": verified,
            # Why it was not approved. Carried so the Synthesizer can caveat specifically
            # rather than vaguely, and so a failed query is distinguishable from an empty one.
            "feedback": state.get("verify_feedback", ""),
            "error": state.get("exec_error", "") or state.get("validation_error", ""),
        }
        log.info(
            "dispatch: %s finished - %d rows, verified=%s",
            finished, len(state.get("rows") or []), verified,
        )

    # -- Take the next one off the worklist ------------------------------------
    pending = list(state.get("pending_queries") or [])
    if not pending:
        log.info("dispatch: all queries done (%s)", ", ".join(results) or "none")
        return {"results": results, "current_query": "", "pending_queries": []}

    nxt = pending.pop(0)

    # A per-well query needs a well before it can be written. Prefer the one the caller asked
    # for; otherwise drill into the worst well the summary found, which is why the summary is
    # ordered ahead of it. With neither, the query is skipped rather than written unscoped -
    # an unscoped "detail" query silently returns the whole fleet.
    target = state.get("target_well_id", "")
    while queries.QUERIES_BY_KEY[nxt].needs_well_id and not target:
        target = _worst_well(results)
        if target:
            log.info("dispatch: no --well given - drilling into worst well %s", target)
            break
        log.warning("dispatch: %s needs a well id and none is available - skipping it", nxt)
        if not pending:
            return {"results": results, "current_query": "", "pending_queries": []}
        nxt = pending.pop(0)

    log.info("dispatch: starting %s (%d left after this)", nxt, len(pending))

    return {
        "results": results,
        "pending_queries": pending,
        "current_query": nxt,
        # Reset every per-query working field. Without this the next query inherits the
        # previous one's retry budgets, its rejection feedback and its tried-SQL list - so a
        # rewrite instruction meant for the slippage query would arrive as criticism of an
        # activity query that had not been written yet.
        "sql": "",
        "columns": [],
        "rows": [],
        "truncated": False,
        "validation_error": "",
        "exec_error": "",
        "contract_errors": [],
        "verify_ok": False,
        "verify_feedback": "",
        "tried_sql": [],
        "retry_count": 0,
        "verify_retry_count": 0,
        "target_well_id": target,
    }


def _worst_well(results: dict) -> str:
    """The well carrying the most delayed activity codes, from the summary's first row.

    The summary is ordered worst-first by the query itself, so this is a read of row 0 rather
    than a ranking computed here - no second opinion about what "worst" means.
    """
    summary = results.get("activity_summary") or {}
    rows, columns = summary.get("rows") or [], summary.get("columns") or []
    if not rows or not columns:
        return ""
    names = [str(c).lower() for c in columns]
    if "well_id" not in names:
        return ""
    value = rows[0][names.index("well_id")]
    return "" if value is None else str(value).strip()


def route_after_dispatch(state: SlippageState) -> str:
    return "sql_author" if state.get("current_query") else "synthesize"
