"""The slippage scenarios, and the one status vocabulary they all report in.

This module is the single place where a business decision about slippage lives. It is data, not
logic: the Planner is handed these scenarios and works out which COLUMNS of the detected schema
carry each one, the SQL Author writes the arithmetic, and the Verifier checks the result against
the same list. Nothing else enumerates milestones.

Source: milestone_rules.md §1 (the milestones and their deadlines), §2 (the status vocabulary),
§4 (the headline verdict and its priority order).

⚠ FOUR MILESTONES, NOT THE SIX §1 DEFINES. Construction and hook-up were removed deliberately.
This is a divergence from an authoritative document, so it is recorded here rather than left to
be discovered - and it is NOT a bug to be "fixed" by adding them back:

  * construction could only ever fail as MISSED, because a rig that had arrived was terminal and
    never a failure (business_rules §4). Across the live fleet it was the headline failure for
    ZERO wells - it never once decided anything.
  * hook-up completion IS well completion (business_rules §8), and the population keeps only
    wells that are not complete, so its actual date was empty for every row by construction.

Removing them costs four wells that failed hook-up and nothing else; they leave the listing.
Restoring either means restoring its Scenario here and nothing else - the prompts, the checks and
the output contract are all generated from this list.
"""
from __future__ import annotations

from dataclasses import dataclass


# -- Status vocabulary ---------------------------------------------------------
# milestone_rules.md §2 requires ONE vocabulary across every milestone: "A reader comparing
# two milestones must not have to know which one they are looking at to interpret the value."
#
# ⚠ ASSUMPTION, NOT A RECORDED DECISION. §2 defines the six OUTCOMES but never gives the literal
# strings, and the deleted sql_examples.md left them "NOT YET SPECIFIED". The values below are
# recovered from the sibling app's shipped slipped_wells.sql, normalised to one set - that query
# actually uses three (AHEAD vs AHEAD_OF_SCHEDULE, MISSED vs DELAYED, PENDING vs NOT_YET_DUE),
# which is the drift §2 forbids.
#
# Normalising is the assumption. If the existing React dashboard must keep working unchanged,
# change these six strings and nothing else - every prompt and check reads them from here.
STATUS_DATA_QUALITY = "DATA_QUALITY_ISSUE"   # the expected date the deadline needs is absent
STATUS_MISSED = "MISSED"                     # not completed, deadline has passed
STATUS_PENDING = "PENDING"                   # not completed, deadline still ahead
STATUS_AHEAD = "AHEAD_OF_SCHEDULE"           # completed before the deadline
STATUS_ON_SCHEDULE = "ON_SCHEDULE"           # completed exactly on the deadline
STATUS_DELAYED = "DELAYED"                   # completed after the deadline


# Evaluation order is itself a rule (§2): the missing-data guard must be tested FIRST, or an
# absent expected date silently reads as on time - "the most dangerous wrong answer this system
# can produce". MISSED must precede PENDING: both have no completion date, and only the deadline
# comparison separates "late and still not done" from "not yet due".
STATUS_ORDER = (
    STATUS_DATA_QUALITY,
    STATUS_MISSED,
    STATUS_PENDING,
    STATUS_AHEAD,
    STATUS_ON_SCHEDULE,
    STATUS_DELAYED,
)


@dataclass(frozen=True)
class Scenario:
    """One milestone: what it means, when it is due, and what settles it."""

    key: str
    label: str
    owner: str
    # Deadline in business terms. The Planner maps "the expected rig-on date" onto a real column.
    deadline: str
    # What makes the milestone complete.
    completed_when: str
    # Column prefix for this scenario's outputs, e.g. flaf -> flaf_status, flaf_deadline.
    prefix: str
    note: str = ""

    @property
    def statuses(self) -> tuple[str, ...]:
        """Every milestone reports the same six outcomes.

        There used to be per-scenario additions and exclusions here, for construction's
        RIG_ARRIVED. Construction was removed, and with it the only reason any milestone differed
        from the shared vocabulary - which is what milestone_rules §2 wanted in the first place.
        """
        return STATUS_ORDER


# In the real order of the work (milestone_rules.md §1). This is NOT the reporting priority -
# see SLIPPAGE_PRIORITY below.
SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        key="flaf",
        label="FLAF issued",
        owner="PDO",
        deadline="90 days before the expected rig-on date",
        completed_when="the FLAF was issued",
        prefix="flaf",
    ),
    Scenario(
        key="pegging",
        label="Pegging sheet issued",
        owner="PDO",
        deadline="60 days before the expected rig-on date",
        completed_when="the well was pegged",
        prefix="pegging",
    ),
    Scenario(
        key="rig_on",
        label="Rig on",
        owner="PDO",
        deadline="the expected rig-on date itself",
        completed_when="the rig arrived",
        prefix="rig_on",
    ),
    Scenario(
        key="rig_off",
        label="Rig off",
        owner="PDO",
        deadline="the expected rig-off date itself",
        completed_when="the rig left",
        prefix="rig_off",
    ),
)

SCENARIOS_BY_KEY = {s.key: s for s in SCENARIOS}

# -- Headline verdict ----------------------------------------------------------
# §4: test the milestones in a fixed priority order and take the first that FAILED.
#
# ⚠ ASSUMPTION, NOT A RECORDED DECISION. §4 states plainly that this order is a business
# decision - "keep the order the contract specifies rather than choosing your own" - and the
# contract that would specify it was deleted. This is the order the sibling's slipped_wells.sql
# actually uses, minus the two milestones removed here (see the module docstring).
SLIPPAGE_PRIORITY = ("rig_on", "flaf", "pegging", "rig_off")

SLIPPAGE_STATUS = {
    "rig_on": "SLIPPED - RIG ON",
    "flaf": "SLIPPED - FLAF",
    "pegging": "SLIPPED - PEGGING",
    "rig_off": "SLIPPED - RIG OFF",
}
NOT_SLIPPED = "NOT SLIPPED"


def ordered_scenarios() -> list[Scenario]:
    """The scenarios in reporting-priority order."""
    return [SCENARIOS_BY_KEY[key] for key in SLIPPAGE_PRIORITY]


def describe() -> str:
    """The scenarios as a prompt block, for the Planner, SQL Author and Verifier alike.

    One rendering, shared by all three, so they cannot form different ideas of what a milestone
    is - the failure mode that lets an author satisfy a verifier about the wrong thing.
    """
    lines = [
        "SLIPPAGE SCENARIOS (from milestone_rules.md §1, in the real order of the work). These "
        "are the ONLY milestones this query reports - do not add construction or hook-up:",
        "",
    ]
    for index, scenario in enumerate(SCENARIOS, 1):
        lines.append(
            str(index) + ". " + scenario.key + " - " + scenario.label + "  [owner: " + scenario.owner + "]"
        )
        lines.append("   deadline:       " + scenario.deadline)
        lines.append("   completed when: " + scenario.completed_when)
        lines.append("   output columns: " + ", ".join(output_columns(scenario)))
        lines.append("   statuses:       " + " | ".join(scenario.statuses))
        lines.append("   fails when:     " + failure_rule(scenario))
        if scenario.note:
            lines.append("   note:           " + scenario.note)
        lines.append("")

    lines += [
        "HEADLINE VERDICT (well_slippage_status) - test in this order, first FAILURE wins:",
        "  " + " -> ".join(SLIPPAGE_PRIORITY),
        "  values: " + "; ".join(SLIPPAGE_STATUS[k] for k in SLIPPAGE_PRIORITY),
        "",
        "A milestone counts as FAILED when its expected date exists AND either it was completed "
        "after its deadline, or it is not complete and the deadline has already passed. The same "
        "rule for every milestone - there is no exception.",
        "A well that failed nothing is excluded by the listing filter, so '" + NOT_SLIPPED + "' "
        "should never actually reach the output.",
    ]
    return "\n".join(lines)


def failure_rule(scenario: Scenario) -> str:
    """When this scenario counts as a failure, for the headline verdict and the listing filter.

    One rule for every milestone now. It was stated per scenario because construction was a
    genuine exception - a rig that had arrived was terminal and never a failure - and burying
    that in a general sentence left an author with no valid label for it. With construction gone
    there is no exception left to carve out.
    """
    return (
        "status is " + STATUS_MISSED + " or " + STATUS_DELAYED
        + " (the expected date exists, and it is either overdue or was completed late)"
    )


def output_columns(scenario: Scenario) -> list[str]:
    """The four columns every scenario contributes to the listing.

    Ordered as a reader reads them: what was promised, what happened, the verdict, the gap.

    `_actual` is the RAW RECORDED DATE the milestone was completed, as this scenario's
    `completed_when` defines it - not derived, not defaulted. Without it the listing can say a
    milestone was 96 days late but not when it actually landed, which is the first thing anyone
    asks next and the only one of the four that is a fact rather than a judgement.
    """
    return [
        scenario.prefix + "_deadline",
        scenario.prefix + "_actual",
        scenario.prefix + "_status",
        scenario.prefix + "_variance_days",
    ]


def expected_columns() -> list[str]:
    """Every column the listing query must return, in order.

    This is the output contract. The Verifier checks the executed query's columns against it in
    BOTH directions - a missing column and an unexpected one each mean something moved - because
    a query that computes everything correctly and names one column differently does not fail:
    it returns rows, the consumer reads the name it expects, finds nothing, and reports blank,
    with no error anywhere.
    """
    columns = ["well_id"]
    for scenario in SCENARIOS:
        columns += output_columns(scenario)
    columns.append("well_slippage_status")
    return columns
