"""Run all three queries concurrently.

WHY. Measured sequentially, a three-query run was ~340-590s of which 96% was LLM latency and
almost none was the database. The three queries do not interact, so most of that was one agent
working while two others waited.

THERE IS NO DEPENDENCY BETWEEN THEM. An earlier version made activity_delay wait for
activity_summary, on the reasoning that a per-well query needs a well. That was wrong: the query
filters on a BOUND `?` PARAMETER, so it does not care which well - it only needs *a* well to
execute against so the Verifier has rows to judge. Any well serves, so a fixed sample
(SAMPLE_WELL_ID) removes the wait entirely and all three generate in one wave. The well a reader
actually asks about is bound later, by the API, against this same verified SQL.

One consequence worth knowing: the sample well decides what the Verifier SEES. Against a well
with no late tasks it can only check the SQL text, never its output - so a sample with real
delayed work makes the verification meaningfully stronger.

Threads rather than asyncio: every expensive call here is a blocking library (the OpenAI client,
pyodbc). Threads wait on those without the whole stack becoming async, and the GIL is irrelevant
when the work is entirely I/O.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from app.config import settings
from app.graph import queries
from app.graph.query_graph import build_query_graph
from app.observability import get_logger

log = get_logger()

# Bounds concurrency to the number of queries. Not a performance knob - it exists so that adding
# a fourth query later does not silently become a four-way burst against the model provider.
MAX_WORKERS = 3


def _empty_result(error: str) -> dict:
    return {
        "sql": "", "columns": [], "rows": [], "row_count": 0, "truncated": False,
        "verified": False, "feedback": "", "error": error, "elapsed": 0.0,
    }


def _run_one(base: dict, key: str, well: str) -> dict:
    """Author, validate, execute and verify ONE query, on its own isolated state."""
    spec = queries.QUERIES_BY_KEY[key]
    state: dict[str, Any] = {
        # Read-only inputs, shared by value. Nothing below mutates them.
        "schema": base.get("schema", ""),
        "grounding": base.get("grounding", ""),
        "column_plan": base.get("column_plan", {}),
        "plan_notes": base.get("plan_notes", ""),
        # Per-query working state, fresh. This isolation is what makes concurrency safe: no two
        # queries can see each other's SQL, retries or rejection feedback.
        "current_query": key,
        "target_well_id": well if spec.needs_well_id else "",
        "sql": "",
        "tried_sql": [],
        "columns": [],
        "rows": [],
        "validation_error": "",
        "exec_error": "",
        "contract_errors": [],
        "verify_ok": False,
        "verify_feedback": "",
        "retry_count": 0,
        "verify_retry_count": 0,
        "rejected": [],
    }

    started = time.perf_counter()
    try:
        # Each query gets its OWN compiled graph. Compiling is cheap, and sharing one across
        # threads would mean sharing whatever mutable state LangGraph keeps inside it.
        final = build_query_graph().invoke(state, {"recursion_limit": 40})
    except Exception as exc:  # noqa: BLE001 - one query must never take the others down
        log.exception("[%s] failed outright", key)
        return _empty_result(str(exc))

    elapsed = time.perf_counter() - started
    verified = bool(final.get("verify_ok"))
    rows = final.get("rows") or []
    log.info("[%s] DONE in %.1fs - %d rows, verified=%s", key, elapsed, len(rows), verified)

    # A per-well query verified against an EMPTY sample is only half-checked: the Verifier could
    # read the SQL but never its output, so nothing confirmed the statuses, variances, grain or
    # WBS resolution. That is not hypothetical - on the run where the sample DID return rows,
    # the Verifier caught two row-level defects it could not otherwise have seen.
    #
    # The usual cause is that SAMPLE_WELL_ID has gone stale: a well that completes drops out of
    # the in-progress population and silently starts returning nothing.
    if spec.needs_well_id and verified and not rows:
        log.warning(
            "[%s] VERIFIED AGAINST AN EMPTY RESULT - sample well %s returned no rows, so only "
            "the SQL text was checked, never its output. Set SAMPLE_WELL_ID in .env to a well "
            "that still has late tasks.",
            key, well or "(unset)",
        )
    return {
        "sql": final.get("sql", ""),
        "columns": final.get("columns", []),
        "rows": rows,
        "row_count": len(rows),
        "truncated": bool(final.get("truncated")),
        "verified": verified,
        "feedback": final.get("verify_feedback", ""),
        "error": final.get("exec_error", "") or final.get("validation_error", ""),
        "elapsed": round(elapsed, 1),
    }


def run_queries(base: dict, keys: list[str], target_well: str = "") -> dict[str, dict]:
    """Run every requested query at once. Returns {key: result}."""
    if not keys:
        return {}

    # --well, when given, is what the per-well query is generated against; otherwise the
    # configured sample. Either way it is known UP FRONT, so nothing has to wait.
    well = (target_well or settings.sample_well_id or "").strip()
    per_well = [k for k in keys if queries.QUERIES_BY_KEY[k].needs_well_id]
    if per_well and not well:
        log.warning(
            "parallel: %s needs a well to verify against and SAMPLE_WELL_ID is empty - skipping",
            ", ".join(per_well),
        )

    log.info(
        "parallel: starting %d queries at once - %s%s",
        len(keys), ", ".join(keys), ("  (sample well " + well + ")" if well and per_well else ""),
    )

    results: dict[str, dict] = {}
    runnable = [k for k in keys if well or not queries.QUERIES_BY_KEY[k].needs_well_id]
    for key in keys:
        if key not in runnable:
            results[key] = _empty_result("No well id was available to verify this query against.")

    overall = time.perf_counter()
    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(runnable) or 1)) as pool:
        futures = {pool.submit(_run_one, base, k, well): k for k in runnable}
        for future, key in futures.items():
            results[key] = future.result()

    total = time.perf_counter() - overall
    serial = sum(r.get("elapsed", 0.0) for r in results.values())
    log.info(
        "parallel: all %d queries done in %.1fs (serial would have been %.1fs, saved %.1fs)",
        len(runnable), total, serial, max(0.0, serial - total),
    )
    return results
