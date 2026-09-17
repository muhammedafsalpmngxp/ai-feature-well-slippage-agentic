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
    # Which detected column carries each scenario's expected date, actual date and filters.
    # Shape: {scenario_key: {"expected_date": ..., "actual_date": ..., "notes": ...}, ...}
    column_plan: dict[str, Any]
    plan_notes: str                 # anything the Planner could not resolve from the schema
    population_filter: str          # how to keep only wells still in progress
    well_table: str                 # the table the Planner identified as the well record

    # -- Step 3: the SQL Author -----------------------------------------------
    sql: str
    # Normalised SQL already attempted. Re-emitting one is pointless - identical SQL returns
    # identical data - so it short-circuits rather than burning another verify cycle.
    tried_sql: list[str]

    # -- Step 4: Validator and Executor ---------------------------------------
    validation_error: str
    exec_error: str
    columns: list[str]
    rows: list[list[Any]]
    truncated: bool
    row_count: int
    elapsed: float
    # Rewrites spent on a safety or execution failure.
    retry_count: int

    # -- Step 5: the Verifier -------------------------------------------------
    verify_ok: bool
    verify_feedback: str            # fed back to the SQL Author, never to the Synthesizer
    contract_errors: list[str]      # deterministic column-contract mismatches
    # Verifier-triggered rewrites. Deliberately SEPARATE from retry_count: sharing one budget
    # lets unrelated syntax errors earlier in the run leave the Verifier with zero rewrites,
    # so it rejects and is overruled immediately.
    verify_retry_count: int
    # Results the Verifier set aside. Kept rather than deleted: the rows really were retrieved,
    # and an explanation hedged with "this could not be verified" beats explaining nothing.
    rejected: list[dict[str, Any]]

    # -- Step 6: the Synthesizer ----------------------------------------------
    explanation: str
