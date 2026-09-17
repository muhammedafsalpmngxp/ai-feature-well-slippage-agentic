"""Assemble the detection graph.

    detect -> planner -> queries -> synthesize

`queries` runs all three query pipelines CONCURRENTLY (see graph/parallel.py); each is the
author -> validator -> executor -> verifier loop in graph/query_graph.py, on its own isolated
state. There is no dependency between them: the per-well query filters on a bound parameter, so
it needs only a SAMPLE well to verify against, not one derived from another query's result.

The top level is deliberately thin. Everything that can fail, retry or loop lives inside one
query's graph, so a query that exhausts its budget ends on its own without touching the others.
The Synthesizer then reports what is missing rather than the run collapsing.
"""
from __future__ import annotations

import time

from langgraph.graph import END, START, StateGraph

from app.db.introspect import detect, grounding
from app.graph import parallel, queries
from app.graph.nodes.planner import planner_node
from app.graph.nodes.synthesize import synthesize_node
from app.graph.state import SlippageState
from app.observability import get_logger

log = get_logger()


def detect_node(state: SlippageState) -> dict:
    """Step 1 - introspect the live database into a schema block and value hints."""
    started = time.perf_counter()
    schema, hints = detect(use_cache=not state.get("refresh_schema", False))
    log.info("detect: done in %.1fs", time.perf_counter() - started)
    return {"schema": schema, "value_hints": hints, "grounding": grounding(schema, hints)}


def queries_node(state: SlippageState) -> dict:
    """Step 3 - run every planned query, concurrently where the dependency allows."""
    keys = list(state.get("pending_queries") or queries.DEFAULT_KEYS)
    results = parallel.run_queries(
        dict(state), keys, target_well=str(state.get("target_well_id", "")).strip()
    )
    # The well the detail query was generated against, so the Synthesizer can name it in the
    # heading. Known up front now - either --well or the configured sample - rather than
    # discovered from another query's output.
    from app.config import settings

    well = str(state.get("target_well_id", "")).strip() or settings.sample_well_id
    return {"results": results, "target_well_id": well}


def build_graph():
    graph = StateGraph(SlippageState)
    graph.add_node("detect", detect_node)
    graph.add_node("planner", planner_node)
    graph.add_node("queries", queries_node)
    graph.add_node("synthesize", synthesize_node)

    graph.add_edge(START, "detect")
    graph.add_edge("detect", "planner")
    graph.add_edge("planner", "queries")
    graph.add_edge("queries", "synthesize")
    graph.add_edge("synthesize", END)
    return graph.compile()
