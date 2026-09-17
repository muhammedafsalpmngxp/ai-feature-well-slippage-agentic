"""Planner - binds each slippage scenario to real columns of the detected schema.

Step 2 of the pipeline. The rules talk about "the expected rig-on date"; the database has a
column with some particular name and type. This node is where those two meet, and it is the only
node that has to. Everything after it works from the binding rather than from the prose.

Keeping this separate from the SQL Author is what lets an unresolvable scenario be REPORTED
rather than guessed. A single agent doing both has every incentive to invent a plausible column
name to finish the query - which produces SQL that runs, looks right, and answers about the
wrong thing, with nothing anywhere to indicate it.
"""
from __future__ import annotations

from app.graph import queries, scenarios
from app.graph.prompts import PLANNER_SYSTEM
from app.graph.state import SlippageState
from app.llm import chat
from app.observability import get_logger
from app.utils import extract_json

log = get_logger()


def _resolved_count(plan: dict) -> tuple[int, int]:
    bound = plan.get("scenarios") or {}
    total = len(scenarios.SCENARIOS)
    done = sum(
        1
        for s in scenarios.SCENARIOS
        if (bound.get(s.key) or {}).get("expected_date")
    )
    return done, total


def render_plan(state: SlippageState) -> str:
    """The column plan as a prompt block for the SQL Author and the Verifier."""
    plan = state.get("column_plan") or {}
    bound = plan.get("scenarios") or {}
    if not bound:
        return ""

    lines = ["COLUMN PLAN (the Planner bound each scenario to these detected columns):"]
    lines.append("  well table:        " + str(plan.get("well_table", "?")))
    lines.append("  well key:          " + str(plan.get("well_key", "?")))
    lines.append("  population filter: " + str(plan.get("population_filter", "?")))
    lines.append("")
    for scenario in scenarios.SCENARIOS:
        entry = bound.get(scenario.key) or {}
        lines.append("  " + scenario.key + ":")
        lines.append("    table:         " + str(entry.get("table") or "-"))
        lines.append("    expected date: " + str(entry.get("expected_date") or "NOT RESOLVED"))
        lines.append("    actual date:   " + str(entry.get("actual_date") or "-"))
        if entry.get("notes"):
            lines.append("    notes:         " + str(entry["notes"]))
    task = plan.get("task") or {}
    if task:
        lines.append("")
        lines.append("  task side (for the activity delay query):")
        for field in (
            "task_table", "task_code", "task_well_key", "task_well_key_type", "well_key_type",
            "action_on", "tie_breaker",
            "target_start", "target_end", "actual_start", "actual_end", "progress",
            "mapping_table", "mapping_activity_id", "mapping_activity_code", "mapping_crew_code",
            "description_table", "description_activity_code", "wbs_description",
        ):
            lines.append("    " + field.ljust(26) + str(task.get(field) or "NOT RESOLVED"))
        left, right = task.get("task_well_key_type"), task.get("well_key_type")
        if left and right and str(left).split("(")[0].strip().lower() != str(right).split("(")[0].strip().lower():
            lines.append(
                "    ⚠ WELL KEY TYPES DIFFER: task side is " + str(left) + ", well side is "
                + str(right) + " - CAST BOTH SIDES explicitly or the join fails on a "
                "non-numeric id."
            )
        if task.get("notes"):
            lines.append("    notes:                    " + str(task["notes"]))

    if plan.get("unresolved"):
        lines.append("")
        lines.append("  UNRESOLVED: " + str(plan["unresolved"]))
    return "\n".join(lines)


def planner_node(state: SlippageState) -> dict:
    user = "\n\n".join(
        [
            "DETECTED DATABASE (the ONLY tables and columns that exist):",
            state.get("grounding", ""),
            "Bind BOTH parts to real columns of this schema: every milestone scenario, and "
            "the task side the activity delay query needs. Reply with the JSON only.",
        ]
    )

    raw = chat(PLANNER_SYSTEM, user, agent="planner")
    plan = extract_json(raw, default={})

    if not plan.get("scenarios"):
        # Not fatal. The SQL Author still has the schema block and the rules, so it can proceed
        # unbound - it simply loses the Planner's type warnings and has to resolve names itself.
        log.warning("plan: unreadable (%d chars) - the SQL Author will work from the schema alone",
                    len(raw or ""))
        return {
            "column_plan": {},
            "plan_notes": "The column plan could not be parsed.",
            # Only the well query can proceed unbound; the activity query needs the task
            # table it would have named.
            "pending_queries": ["well_slippage"],
        }

    done, total = _resolved_count(plan)
    task = plan.get("task") or {}
    log.info(
        "plan: %d/%d milestone scenarios bound, task side %s, well table %s",
        done, total,
        "bound (" + str(task.get("task_table")) + ")" if task.get("task_table") else "NOT BOUND",
        plan.get("well_table", "?"),
    )
    if plan.get("unresolved"):
        log.warning("plan: unresolved - %s", plan["unresolved"])

    # The activity delay query cannot be written without the task table. Dropping it from the
    # worklist is better than authoring against an unbound plan, which produces a query that
    # runs and answers about nothing.
    pending = list(queries.DEFAULT_KEYS)
    if not task.get("task_table"):
        pending = [k for k in pending if k != "activity_delay"]
        log.warning("plan: no task table bound - skipping the activity delay query")

    # --only narrows the worklist, but never widens it past what the plan can support.
    only = state.get("only")
    if only:
        pending = [k for k in pending if k == only]
        log.info("plan: --only %s -> worklist %s", only, pending or "empty")

    return {
        "column_plan": plan,
        "well_table": str(plan.get("well_table", "")),
        "population_filter": str(plan.get("population_filter", "")),
        "plan_notes": str(plan.get("unresolved", "")),
        "pending_queries": pending,
    }
