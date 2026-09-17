"""The slippage scenarios, and the one status vocabulary they all report in.

This module is the single place where a business decision about slippage lives. It is data, not
logic: the Planner is handed these scenarios and works out which COLUMNS of the detected schema
carry each one, the SQL Author writes the arithmetic, and the Verifier checks the result against
the same list. Nothing else enumerates milestones.

Source: milestone_rules.md §1 (the six milestones and their deadlines), §2 (the status
vocabulary), §4 (the headline verdict and its priority order).
"""
from __future__ import annotations

from dataclasses import dataclass


# -- Status vocabulary ---------------------------------------------------------
# milestone_rules.md §2 requires ONE vocabulary across all six milestones: "A reader comparing
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

# Construction only. milestone_rules §1 gives construction FOUR outcomes - "missing-data, missed,
# pending, and rig-arrived" - and no late one, because the rig arriving is only a proxy for
# completion and carries no grade. business_rules §4 says the same from the other side: "If the
# rig has come on, the construction deadline is not treated as missed... it does not detect a
# construction delay on a well whose rig has already arrived."
#
# So this is a terminal, NON-FAILING outcome. Without it there is no valid label for a rig that
# has arrived, and any query is forced to call it PENDING (wrong - the deadline has passed) or
# DELAYED (wrong - business_rules §4 forbids it).
STATUS_RIG_ARRIVED = "RIG_ARRIVED"           # construction only: the rig is on site

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
    # What makes the milestone complete. Construction has no completion date of its own.
    completed_when: str
    # Column prefix for this scenario's outputs, e.g. flaf -> flaf_status, flaf_deadline.
    prefix: str
    # Some milestones cannot reach every status - see `statuses`.
    excluded_statuses: tuple[str, ...] = ()
    # Statuses this milestone has that the shared vocabulary does not (construction only).
    extra_statuses: tuple[str, ...] = ()
    # False when reaching a terminal state cannot make this milestone count as failed.
    can_fail_when_complete: bool = True
    note: str = ""

    @property
    def statuses(self) -> tuple[str, ...]:
        shared = tuple(s for s in STATUS_ORDER if s not in self.excluded_statuses)
        return shared + self.extra_statuses


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
        key="construction",
        label="Construction complete",
        owner="Al Tasnim",
        deadline="1 day before the expected rig-on date",
        completed_when="the rig arrived (there is no construction completion date)",
        prefix="construction",
        # §1: "it has no 'finished early' or 'finished exactly on time' outcomes - only
        # missing-data, missed, pending, and rig-arrived." DELAYED is excluded too: the rig
        # arriving is an ungraded proxy for completion, and business_rules §4 states outright
        # that a rig which has come on is never a construction miss.
        excluded_statuses=(STATUS_AHEAD, STATUS_ON_SCHEDULE, STATUS_DELAYED),
        extra_statuses=(STATUS_RIG_ARRIVED,),
        can_fail_when_complete=False,
        note=(
            "Four outcomes only. The rig having arrived is terminal and NON-FAILING, whenever it "
            "arrived: construction can fail ONLY via MISSED (deadline passed, rig still not on "
            "site). This milestone therefore cannot detect a construction delay on a well whose "
            "rig is already there - which business_rules §4 states explicitly, and is a limit of "
            "the data, not of the query. "
            "⚠ AND IT CARRIES NO VARIANCE WHEN THE RIG ARRIVED. RIG_ARRIVED is ungraded - there "
            "is no early, on-time or late version of it - so construction_variance_days must be "
            "NULL in that case. Only MISSED has a measurable figure: deadline to today. "
            "Reporting a day count against an ungraded outcome states a precision the data does "
            "not have."
        ),
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
    Scenario(
        key="hookup",
        label="Hook-up complete",
        owner="Al Tasnim",
        deadline=(
            "2 days after rig-off - measured from the ACTUAL rig-off date when it is known, "
            "falling back to the expected rig-off date only when it is not"
        ),
        completed_when="engineering completion was recorded",
        prefix="hookup",
        note=(
            "The only deadline counted FORWARD, and the only one with two possible bases. The "
            "actual date always wins: once the rig is genuinely off, the original plan no longer "
            "sets the deadline. Note the population filter keeps only wells with no engineering "
            "completion, so inside these queries this milestone is never complete."
        ),
    ),
)

SCENARIOS_BY_KEY = {s.key: s for s in SCENARIOS}

# -- Headline verdict ----------------------------------------------------------
# §4: test the milestones in a fixed priority order and take the first that FAILED.
#
# ⚠ ASSUMPTION, NOT A RECORDED DECISION. §4 states plainly that this order is a business
# decision - "keep the order the contract specifies rather than choosing your own" - and the
# contract that would specify it was deleted. This is the order the sibling's slipped_wells.sql
# actually uses, with construction inserted: that query tests only five milestones, so a well
# whose ONLY failure is construction is currently excluded from the listing altogether.
SLIPPAGE_PRIORITY = ("rig_on", "flaf", "pegging", "construction", "rig_off", "hookup")

SLIPPAGE_STATUS = {
    "rig_on": "SLIPPED - RIG ON",
    "flaf": "SLIPPED - FLAF",
    "pegging": "SLIPPED - PEGGING",
    "construction": "SLIPPED - CONSTRUCTION",
    "rig_off": "SLIPPED - RIG OFF",
    "hookup": "SLIPPED - HOOK-UP",
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
        "SLIPPAGE SCENARIOS (from milestone_rules.md §1 - the six milestones, in the real order "
        "of the work):",
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
        "after its deadline, or it is not complete and the deadline has already passed - EXCEPT "
        "where the scenario's own 'fails when' line above says otherwise.",
        "A well that failed nothing is excluded by the listing filter, so '" + NOT_SLIPPED + "' "
        "should never actually reach the output.",
    ]
    return "\n".join(lines)


def failure_rule(scenario: Scenario) -> str:
    """When this scenario counts as a failure, for the headline verdict and the listing filter.

    Stated per scenario rather than once for all six, because construction is a genuine
    exception and burying it in a general sentence is what leaves a query author with no valid
    label for a rig that has already arrived.
    """
    if scenario.can_fail_when_complete:
        return (
            "status is " + STATUS_MISSED + " or " + STATUS_DELAYED
            + " (the expected date exists, and it is either overdue or was completed late)"
        )
    return (
        "status is " + STATUS_MISSED + " ONLY. " + STATUS_RIG_ARRIVED + " is terminal and never "
        "a failure, no matter when the rig arrived."
    )


def output_columns(scenario: Scenario) -> list[str]:
    """The three columns every scenario contributes to the listing."""
    return [
        scenario.prefix + "_deadline",
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
