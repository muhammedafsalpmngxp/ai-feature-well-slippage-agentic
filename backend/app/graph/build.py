"""Assemble the detection graph.

    detect ──┬── everything frozen and current ──> reuse ─────────────────┐
             └── something to author ──> planner ──> queries ─────────────┴──> synthesize

`queries` runs the outstanding query pipelines CONCURRENTLY (see graph/parallel.py); each is the
author -> validator -> executor -> verifier loop in graph/query_graph.py, on its own isolated
state. There is no dependency between them: the per-well query filters on a bound parameter, so
it needs only a SAMPLE well to verify against, not one derived from another query's result.

`reuse` is the short path, and on a stable schema it is the only one taken: the verified SQL is
read from sql/ and simply executed, with no agent involved (see app/frozen.py for what makes a
freeze valid). A frozen query that fails to execute falls through to authoring rather than being
reported as broken - the schema can move in ways the fingerprint cannot see, such as a redefined
view or a revoked grant, and a run that repairs itself is worth more than one that complains.

The top level is deliberately thin. Everything that can fail, retry or loop lives inside one
query's graph, so a query that exhausts its budget ends on its own without touching the others.
The Synthesizer then reports what is missing rather than the run collapsing.
"""
from __future__ import annotations

import time

from langgraph.graph import END, START, StateGraph

from app import frozen
from app.config import settings
from app.db.introspect import detect, grounding, live_structure_fingerprint
from app.graph import parallel, queries
from app.graph.nodes.executor import executor_node
from app.graph.nodes.planner import planner_node
from app.graph.nodes.synthesize import synthesize_node
from app.graph.state import SlippageState
from app.observability import get_logger

log = get_logger()


def _requested_keys(state: SlippageState) -> list[str]:
    """The keys this run is about: --only narrows it, otherwise everything."""
    only = str(state.get("only", "") or "").strip()
    if only:
        return [k for k in queries.DEFAULT_KEYS if k == only]
    return list(queries.DEFAULT_KEYS)


def detect_node(state: SlippageState) -> dict:
    """Step 1 - introspect the live database into a schema block and value hints."""
    started = time.perf_counter()
    schema, hints = detect(use_cache=not state.get("refresh_schema", False))

    # The structural fingerprint is what decides whether the frozen SQL can be reused. Read on
    # its own connection rather than threaded out of detect(), which may have answered entirely
    # from cache without ever looking at the live catalogue.
    try:
        fingerprint = live_structure_fingerprint()
    except Exception as exc:  # noqa: BLE001
        # Without it nothing can be reused, which is the safe direction: a query is trusted
        # because it matches a known schema, so an unknown schema means authoring afresh.
        log.warning("detect: could not read the structural fingerprint (%s) - nothing will be "
                    "reused from sql/ this run", exc)
        fingerprint = ""

    log.info("detect: done in %.1fs", time.perf_counter() - started)
    return {
        "schema": schema,
        "value_hints": hints,
        "grounding": grounding(schema, hints),
        "schema_fingerprint": fingerprint,
    }


def _after_detect(state: SlippageState) -> str:
    """Reuse what is frozen and current; author the rest."""
    if state.get("force_regenerate"):
        log.info("frozen: --regenerate given - authoring every query from scratch")
        return "planner"

    keys = _requested_keys(state)
    reusable, stale = frozen.partition(keys, str(state.get("schema_fingerprint", "")))
    if not reusable:
        log.info(
            "frozen: nothing reusable (%s) - the agents will author %s",
            "no frozen query matches the live schema" if state.get("schema_fingerprint")
            else "the live fingerprint is unknown",
            ", ".join(stale),
        )
        return "planner"

    log.info(
        "frozen: reusing %s from sql/%s",
        ", ".join(reusable),
        ("; authoring " + ", ".join(stale)) if stale else " (no agent needed this run)",
    )
    return "reuse"


def reuse_node(state: SlippageState) -> dict:
    """Execute the frozen SQL for every key that is still current.

    No LLM and no authoring: the query was proved correct against this exact schema, so the only
    work left is running it. The Verifier's approval is carried forward from the freeze - that is
    what freezing means - but the deterministic column contract is re-checked, because it costs
    nothing and catches a hand edit that changed the output shape.
    """
    keys = _requested_keys(state)
    fingerprint = str(state.get("schema_fingerprint", ""))
    well = str(state.get("target_well_id", "")).strip() or settings.sample_well_id

    results: dict[str, dict] = {}

    # ONE load pass. frozen.load() reads and hashes each file, so calling partition() here and
    # then load() per key read every file three times a run.
    available = frozen.load_all(keys)

    for key in keys:
        query = available.get(key)
        if query is None or not query.is_current(fingerprint):
            continue
        if query.hand_edited:
            log.warning(
                "frozen: %s.sql has been edited since it was frozen - it will still be run, but "
                "it is no longer the text the Verifier approved, so it is reported unverified",
                key,
            )
        spec = queries.QUERIES_BY_KEY[key]
        started = time.perf_counter()

        # The executor node, reused as-is rather than reimplemented: it already binds the well
        # as a parameter, caps the rows, and records an execution failure to the rework corpus -
        # and a frozen query that stops running is exactly the kind of failure worth recording.
        outcome = executor_node({
            "sql": query.sql,
            "current_query": key,
            "target_well_id": well if spec.needs_well_id else "",
            "retry_count": 0,
        })

        if outcome.get("exec_error"):
            log.warning(
                "frozen: %s no longer executes (%s) - falling back to authoring it. The "
                "structural fingerprint matched, so the cause is something it cannot see: a "
                "redefined view, a revoked grant, or a hand edit.",
                key, outcome["exec_error"],
            )
            continue

        columns = outcome.get("columns") or []
        contract_errors = sqlcheck_contract(columns, list(spec.contract))
        if contract_errors:
            log.warning(
                "frozen: %s returned the wrong columns (%s) - falling back to authoring it",
                key, "; ".join(contract_errors),
            )
            continue

        elapsed = time.perf_counter() - started
        log.info(
            "[%s] REUSED in %.1fs - %d rows, frozen %s%s",
            key, elapsed, len(outcome.get("rows") or []), query.frozen_at or "(unknown)",
            " [HAND-EDITED]" if query.hand_edited else "",
        )
        results[key] = {
            "sql": query.sql,
            "columns": columns,
            "rows": outcome.get("rows") or [],
            "row_count": outcome.get("row_count") or 0,
            "truncated": bool(outcome.get("truncated")),
            # Carried from the freeze. A hand-edited body is NOT claimed as verified: the text
            # that ran is not the text that was approved, and saying otherwise in the brief
            # would be the one lie this pipeline must never tell.
            "verified": not query.hand_edited,
            "feedback": (
                "This query was edited by hand after it was verified, so its approval no "
                "longer covers the text that ran." if query.hand_edited else ""
            ),
            "error": "",
            "elapsed": round(elapsed, 1),
            "reused": True,
        }

    outstanding = [k for k in keys if k not in results]
    return {
        "results": results,
        "reused_queries": sorted(results),
        # What the planner and the query stage still have to do. Empty means the whole run was
        # served from sql/ and no agent ran at all.
        "needs_authoring": outstanding,
        "target_well_id": well,
    }


def sqlcheck_contract(columns: list[str], expected: list[str]) -> list[str]:
    """Thin wrapper so the import sits next to its one use."""
    from app.graph import sqlcheck

    return sqlcheck.check_contract(columns, expected)


def _after_reuse(state: SlippageState) -> str:
    """Author whatever the freeze could not serve; otherwise go straight to the brief."""
    outstanding = list(state.get("needs_authoring") or [])
    if outstanding:
        log.info("frozen: %s still to author", ", ".join(outstanding))
        return "planner"
    log.info("frozen: every query served from sql/ - skipping the planner and the agents")
    return "synthesize"


def queries_node(state: SlippageState) -> dict:
    """Step 3 - run every planned query, concurrently where the dependency allows."""
    keys = list(state.get("pending_queries") or queries.DEFAULT_KEYS)
    fresh = parallel.run_queries(
        dict(state), keys, target_well=str(state.get("target_well_id", "")).strip()
    )

    # MERGE, never replace: anything the reuse step already served is in state["results"] and
    # was not re-run. Returning `fresh` alone would silently drop those queries from the brief.
    results = dict(state.get("results") or {})
    results.update(fresh)

    fingerprint = str(state.get("schema_fingerprint", ""))
    for key, result in fresh.items():
        if not result.get("verified") or not result.get("sql"):
            continue
        if not fingerprint:
            log.warning(
                "frozen: %s was verified but the live fingerprint is unknown, so it cannot be "
                "frozen - nothing would be able to judge later whether it is still valid", key
            )
            continue
        try:
            frozen.freeze(
                key=key,
                label=queries.QUERIES_BY_KEY[key].label,
                sql=result["sql"],
                fingerprint=fingerprint,
                columns=result.get("columns") or [],
                row_count=result.get("row_count") or 0,
            )
        except Exception as exc:  # noqa: BLE001 - a failed freeze must not lose a good run
            log.warning("frozen: could not freeze %s (%s) - this run's result still stands", key, exc)

    # The well the detail query was generated against, so the Synthesizer can name it in the
    # heading. Known up front - either --well or the configured sample.
    well = str(state.get("target_well_id", "")).strip() or settings.sample_well_id
    return {"results": results, "target_well_id": well}


def build_graph():
    graph = StateGraph(SlippageState)
    graph.add_node("detect", detect_node)
    graph.add_node("reuse", reuse_node)
    graph.add_node("planner", planner_node)
    graph.add_node("queries", queries_node)
    graph.add_node("synthesize", synthesize_node)

    graph.add_edge(START, "detect")
    graph.add_conditional_edges("detect", _after_detect, {"reuse": "reuse", "planner": "planner"})
    graph.add_conditional_edges(
        "reuse", _after_reuse, {"planner": "planner", "synthesize": "synthesize"}
    )
    graph.add_edge("planner", "queries")
    graph.add_edge("queries", "synthesize")
    graph.add_edge("synthesize", END)
    return graph.compile()
