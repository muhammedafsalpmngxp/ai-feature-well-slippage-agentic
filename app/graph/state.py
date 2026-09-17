"""Shared state passed between the LangGraph nodes."""
from __future__ import annotations

from typing import Any, TypedDict


class SlippageState(TypedDict, total=False):
    # -- Step 1: detect the database ------------------------------------------
    refresh_schema: bool            # ignore the cache and re-introspect
    schema: str                     # rendered schema block
    value_hints: str                # real coded values from the lookup tables
    grounding: str                  # the two above, as one prompt section

    # -- Step 2: the Planner --------------------------------------------------
    # Which detected column carries each scenario's expected date, actual date and filters,
    # plus the task-side bindings the activity-delay query needs.
    # {"scenarios": {...}, "task": {...}, "well_table": ..., "population_filter": ...}
    column_plan: dict[str, Any]
    plan_notes: str                 # anything the Planner could not resolve from the schema
    population_filter: str
    well_table: str

    # -- Query dispatch -------------------------------------------------------
    # This project produces TWO queries (milestone_rules §5.11), authored and verified one at a
    # time through the same loop. `pending_queries` is the worklist, `current_query` the one in
    # flight, `results` the finished ones keyed by spec.
    only: str                       # run just this query key, when the CLI asked for one
    # The well that per-well queries drill into. Taken from --well, or, when none was
    # given, from the top of the activity summary - which is why the summary runs first.
    target_well_id: str
    pending_queries: list[str]
    current_query: str
    results: dict[str, Any]

    # -- Step 3: the SQL Author (per query) -----------------------------------
    sql: str
    # Normalised SQL already attempted for the CURRENT query. Re-emitting one is pointless -
    # identical SQL returns identical data - so it short-circuits rather than burning a cycle.
    tried_sql: list[str]

    # -- Step 4: Validator and Executor (per query) ---------------------------
    validation_error: str
    exec_error: str
    columns: list[str]
    rows: list[list[Any]]
    truncated: bool
    row_count: int
    elapsed: float
    # Rewrites spent on a safety or execution failure, for the current query.
    retry_count: int

    # -- Step 5: the Verifier (per query) -------------------------------------
    verify_ok: bool
    verify_feedback: str            # fed back to the SQL Author, never to the Synthesizer
    contract_errors: list[str]      # deterministic column-contract mismatches
    # Verifier-triggered rewrites. Deliberately SEPARATE from retry_count: sharing one budget
    # lets unrelated syntax errors earlier in a query leave the Verifier with zero rewrites,
    # so it rejects and is overruled immediately.
    verify_retry_count: int
    # Results the Verifier set aside. Kept rather than deleted: the rows really were retrieved,
    # and an explanation hedged with "this could not be verified" beats explaining nothing.
    rejected: list[dict[str, Any]]

    # -- Step 6: the Synthesizer ----------------------------------------------
    explanation: str
