"""The queries this project produces, and the vocabularies they report in.

Well slippage and activity delay are DIFFERENT QUESTIONS at different grains, and
milestone_rules §5.11 is explicit that one query must not answer both:

    well slippage      - which WELLS missed a contractual milestone    - one row per well
    activity summary   - how much delayed activity each well carries   - one row per well
    activity delay     - which WORK is running late, and whose         - one row per task
    crew availability  - which CREWS carry open work, and which do not - one row per crew

The fourth exists for the suggestion agent (api/advisor.py): when a task is late, it is what
says which crews of the same crew type are free. It describes crews, not wells or tasks.

A well can be on milestone track while its tasks slip, and the reverse. Each spec below
carries its own output contract, which the Verifier checks mechanically in both directions.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.graph import scenarios


# -- Task-level vocabularies (milestone_rules §5.3-§5.6) -----------------------
# ⚠ ASSUMPTION, same standing as the milestone vocabulary in scenarios.py. §5.3-§5.6 define
# the OUTCOMES precisely but never give the literal strings, and the deleted sql_examples.md
# left them "NOT YET SPECIFIED" alongside the milestone ones. These follow the milestone
# vocabulary's shape so a reader moving between the two queries is not learning two dialects.
# Change them here and nothing else needs touching.

# §5.3 - actual start against planned start
START_DATA_QUALITY = "DATA_QUALITY_ISSUE"          # no planned start
START_NOT_STARTED_SLIPPING = "NOT_STARTED_SLIPPING"  # not started, planned start has passed
START_NOT_STARTED_ON_SCHEDULE = "NOT_STARTED_ON_SCHEDULE"  # not started, planned start ahead
START_EARLY = "STARTED_EARLY"
START_ON_TIME = "STARTED_ON_TIME"
START_DELAYED = "START_DELAYED"

START_STATUSES = (
    START_DATA_QUALITY,
    START_NOT_STARTED_SLIPPING,
    START_NOT_STARTED_ON_SCHEDULE,
    START_EARLY,
    START_ON_TIME,
    START_DELAYED,
)

# §5.4 - actual end against planned end. Completed cases first, then in-flight.
END_DATA_QUALITY = "DATA_QUALITY_ISSUE"            # no planned end
END_COMPLETED_EARLY = "COMPLETED_EARLY"
END_COMPLETED_ON_TIME = "COMPLETED_ON_TIME"
END_COMPLETED_LATE = "COMPLETED_LATE"
END_IN_PROGRESS_NOT_LATE = "IN_PROGRESS_NOT_LATE"
END_DUE_TODAY = "DUE_TODAY"                        # its own outcome - no slack, not yet late
END_OVERDUE = "OVERDUE"

END_STATUSES = (
    END_DATA_QUALITY,
    END_COMPLETED_EARLY,
    END_COMPLETED_ON_TIME,
    END_COMPLETED_LATE,
    END_IN_PROGRESS_NOT_LATE,
    END_DUE_TODAY,
    END_OVERDUE,
)

# §5.5 - where the task is in its lifecycle, ignoring whether it is on time
EXEC_COMPLETED = "COMPLETED"
EXEC_NOT_STARTED = "NOT_STARTED"
EXEC_NOT_STARTED_LATE = "NOT_STARTED_LATE"
EXEC_IN_PROGRESS = "IN_PROGRESS"

EXECUTION_STATUSES = (EXEC_COMPLETED, EXEC_NOT_STARTED, EXEC_NOT_STARTED_LATE, EXEC_IN_PROGRESS)

# §5.6 - one triage flag, so the reader can sort by severity
RISK_RED = "RED"                                   # the END is missed
RISK_AMBER_START_SLIPPING = "AMBER_START_SLIPPING"  # not started, planned start passed
RISK_AMBER_START_DELAYED = "AMBER_START_DELAYED"    # started, but late
RISK_GREEN = "GREEN"

RISK_STATUSES = (RISK_RED, RISK_AMBER_START_SLIPPING, RISK_AMBER_START_DELAYED, RISK_GREEN)


# -- Crew availability vocabulary ----------------------------------------------
# ⚠ ASSUMPTION, NOT A RECORDED DECISION. Neither rule document defines when a crew is
# "available", and the database holds no roster, leave, shift or location data. This is the
# narrowest definition the data can actually support: a crew is AVAILABLE when no task it is
# assigned to is IN PROGRESS (started, not finished) on a well still in progress. It says nothing
# about whether the crew is on site, off leave, or anywhere near a given well - the suggestion
# agent is told so, and says so. Change the strings or the definition here and in the guidance
# below; nothing else needs touching.
CREW_AVAILABLE = "AVAILABLE"
CREW_BUSY = "BUSY"

CREW_STATUSES = (CREW_AVAILABLE, CREW_BUSY)


@dataclass(frozen=True)
class QuerySpec:
    key: str
    label: str
    question: str
    grain: str
    contract: tuple[str, ...]
    guidance: str
    # True when the query answers about ONE well rather than the fleet. Such a query is
    # bounded by that well's task count, so it can never be trimmed by the row cap.
    needs_well_id: bool = False

    def contract_line(self) -> str:
        return ", ".join(self.contract)


# -- 1. Well slippage ----------------------------------------------------------

WELL_SLIPPAGE = QuerySpec(
    key="well_slippage",
    label="Well slippage listing",
    question="Which wells still in progress have failed at least one contractual milestone?",
    grain="one row per well",
    contract=tuple(scenarios.expected_columns()),
    guidance="""\
MILESTONE ARITHMETIC - follow milestone_rules §1-§4 exactly:
- Every deadline is DERIVED from an expected date. Deadlines before rig-on count BACKWARDS, so
  an earlier deadline is a larger negative offset.
- Test the missing-data guard FIRST in every status expression. An absent expected date compared
  against anything yields neither true nor false, so without that guard the milestone silently
  reads as on time - the most dangerous wrong answer this query can produce.
- MISSED must be tested before PENDING: both have no completion date, and only the deadline
  comparison separates "late and still not done" from "not yet due".
- There is no tolerance window. ON_SCHEDULE means the completion date EQUALS the deadline exactly.
- The headline verdict tests the scenarios in the given priority order and takes the first that
  FAILED, honouring each scenario's own 'fails when' rule.
- A VARIANCE IS ONLY REPORTED FOR A GRADED OUTCOME. PENDING and DATA_QUALITY_ISSUE carry NULL,
  not a day count: nothing has been measured. MISSED yields deadline-to-today; a completed
  milestone yields deadline-to-completion, signed.
- EVERY MILESTONE ALSO REPORTS ITS ACTUAL DATE in <prefix>_actual: the real recorded date it was
  completed, exactly as that scenario's 'completed when' line defines it. Project the column
  itself - do NOT derive it, round it, or substitute the deadline for it.
  NULL when the milestone is not complete. Not a placeholder date, not the deadline, not today:
  an absent completion is the very thing MISSED and PENDING already report, and a date there
  would contradict the status beside it.
- PREFER deriving the failure condition ONCE and filtering on its result
  (WHERE well_slippage_status IS NOT NULL) over writing the same condition in both the CASE and
  the WHERE. §4 warns those two copies drift apart silently; deriving it once makes that
  impossible rather than merely discouraged.
- Order by the expected rig-on date, soonest first.""",
)


# -- 2. Activity delay ---------------------------------------------------------

ACTIVITY_DELAY = QuerySpec(
    key="activity_delay",
    needs_well_id=True,
    label="Activity delay detail for one well",
    question="For ONE well, which of its tasks are running late, on which activity and WBS, and whose crew?",
    grain="one row per task OF THE SELECTED WELL (90 tasks -> 90 rows; expected, not a fault)",
    contract=(
        "well_id",
        "task_code",
        "action_on",
        "activity_id",
        "activity_code",
        "wbs",
        "crew_code",
        "crew_id",
        "crew_type_id",
        "target_start",
        "target_end",
        "actual_start",
        "actual_end",
        "start_status",
        "start_variance_days",
        "end_status",
        "end_variance_days",
        "execution_status",
        "schedule_risk",
        "progress_percent",
    ),
    guidance="""\
TASK ARITHMETIC - follow milestone_rules §5, and §5.11 for the order of the chain:

1. REDUCE THE HISTORY FIRST, before reading any date. The task table keeps one record per task
   per update. Take the latest record per task by its action date, breaking a tie on the same
   action date with the later-created record (ROW_NUMBER() OVER (PARTITION BY ... ORDER BY
   <action date> DESC, <record id> DESC), then filter to rn = 1). Read EVERY column from that
   reduced set, never from the raw table. Skipping this multiplies every task and corrupts every
   variance - silently, because the query still succeeds.

2. "HAS NOT HAPPENED" (§5.2). In THIS database that is recorded as NULL on the actual start and
   actual end - verified, there is no placeholder date - so there is nothing to normalise and
   `IS NULL` is the correct test. Do NOT invent a sentinel cutoff such as "on or before 1900":
   nothing in the schema defines one, and such a test silently discards real early work.
   Use the actual end, not any completion flag, to decide whether a task is complete.

3. VERDICTS, each testing its missing-data guard first:
   start_status      (§5.3): {start_statuses}
   end_status        (§5.4): {end_statuses}
   execution_status  (§5.5): {execution_statuses}
   schedule_risk     (§5.6): {risk_statuses}
   RED is about the END date; both ambers are about the START date. A task started late that
   still finished on time stays amber - the overrun never reached the schedule.
   DUE_TODAY is its own outcome, separate from both in-progress and overdue.

4. VARIANCES in whole days from the PLANNED date, positive late and negative early. Return NULL,
   never 0, where there is no planned date to measure from - 0 already means "exactly on the
   planned date", and the two must stay distinguishable.

5. PROGRESS is stored as a fraction from 0 to 1. Multiply by 100 for progress_percent, and
   return NULL when the underlying value is absent. Getting this wrong understates every task by
   a factor of a hundred.

6. RESOLVE TASK -> ACTIVITY -> WBS. Two hops, both lookups, neither optional:
   - activity_id is the leading part of the task code, BEFORE its first separator. Guard that
     the separator exists, or a code without one yields a wrong activity rather than none.
   - activity_id -> the activity mapping -> the activity CODE. (This table also has a crew
     column; it is NOT the one the contract wants - see below.)
   - activity code -> the activity description lookup -> the WBS description AND the crew_code.

   ⚠ crew_code COMES FROM THE SECOND HOP, the activity-description lookup - never from the
   mapping table. Both tables carry a crew column and they are not the same thing: the mapping's
   is the superseded scheme. Return the description lookup's crew_code, and return it only when
   the collapsed lookup gives one unambiguously.
   The exact columns, the legacy text-column cast, and the Old/New pitfall are in
   business_rules §3. Follow it. Never guess a WBS or use the activity id as one.
   Use LEFT joins so unmapped work stays visible rather than vanishing from the listing.

   crew_id and crew_type_id are DIFFERENT from crew_code. They are the raw ids recorded on the
   task record itself: which crew was assigned, and what type of crew it is. The suggestion
   agent matches a late task to free crews on crew_type_id, so read both from the REDUCED latest
   record (step 1) - never from the raw history, where an older record may name a crew that has
   since been replaced. Return them as raw ids and do NOT join any crew or crew-type table to
   resolve them (milestone_rules §11: the crew table has no primary key, so the join multiplies
   every task). NULL is correct where the record carries none.

7. SCOPE: ONE WELL. Filter to the well id supplied in the TARGET WELL line of your request,
   comparing it correctly for the declared types on both sides. Also keep the in-progress
   population filter, so this query describes the same fleet as the other two.
   Because the result covers a single well it is bounded by that well's task count, so do
   NOT add a TOP - every one of its late tasks should be returned.

8. ORDER the most urgent first (§5.10): RED before amber before GREEN, then the largest end
   overrun, then the largest start overrun, then the earliest planned end.""",
)


# -- 3. Activity summary per well ----------------------------------------------
# The cap-proof answer to "how much delayed activity does each well carry".
#
# ACTIVITY_DELAY returns one row per task - 5,900 of them here, and growing - so any per-well
# figure read off it is a figure for whatever sample survived MAX_ROWS. This query does the
# counting in SQL instead, returning ONE ROW PER WELL. The result is bounded by the number of
# wells rather than the number of tasks, so it stays complete no matter how much task history
# accumulates. The detail listing keeps its job: showing WHICH tasks are worst.

ACTIVITY_SUMMARY = QuerySpec(
    key="activity_summary",
    label="Delayed activity code count per well",
    question="For each well, how many distinct activity codes have delayed work?",
    grain="one row per well (bounded by the well count, never by the task count)",
    contract=(
        "well_id",
        "delayed_activity_codes",
    ),
    guidance="""\
TWO COLUMNS, NOTHING ELSE. The well, and how many DISTINCT activity codes have delayed work on
it. Do not add a WBS, a crew, a task count, a variance or a status column - activity_delay
already carries all of that per task, and every extra column here is another thing that can be
wrong in a query whose whole job is one number.

THIS IS THE SAME EVIDENCE AS activity_delay, AGGREGATED IN SQL. Build the identical per-task
CTE first - every step of milestone_rules §5.11 applies unchanged, in the same order - then
GROUP BY the well instead of returning the task rows.

Reuse, without exception: the history reduction to the latest record per task; the verdicts that
decide whether a task is delayed; the activity resolution from the task code; the in-progress
population filter.

1. REDUCE THE HISTORY FIRST, before anything is counted. Take the latest record per task by its
   action date, tie-broken by the record id, and PARTITION BY THE TRIMMED task code - stored
   values carry padding, so padded variants would otherwise reduce to separate records and be
   counted twice. Aggregating the raw history multiplies every count by the number of updates a
   task has had, and the query still succeeds, so nothing reveals it.

2. "HAS NOT HAPPENED" (§5.2). In THIS database that is recorded as NULL on the actual start and
   actual end - verified, there is no placeholder date - so `IS NULL` is the correct test. Do
   NOT invent a sentinel cutoff such as "on or before 1900": nothing in the schema defines one,
   and such a test silently discards real early work. Guard a missing planned date FIRST in every
   verdict, before any comparison, or an absent date reads as a real outcome.

3. COUNT DISTINCT ACTIVITY CODES, NEVER TASK ROWS. One well runs the same activity many times
   and a repeating activity is CORRECT (business_rules §3), so COUNT(*) would report a well with
   one activity repeated forty times as forty delayed activities. Use
   COUNT(DISTINCT <activity code>) over that well's delayed tasks only.

4. "DELAYED" MEANS THE END IS LATE - the task finished after its planned end, or is not
   finished and that date has passed. In the end-status vocabulary that is COMPLETED_LATE or
   OVERDUE, which is the same set as schedule risk RED.
   Do NOT use "schedule_risk <> GREEN": that pulls in the two AMBER start risks, and a task
   that started late but is not yet late at the end has lost no schedule time. Counting it
   overstates every well.
   Count an activity code ONCE however many of its tasks are late. An activity whose tasks are
   all on schedule must not appear.

5. THE ACTIVITY CODE is resolved from the task code through the activity mapping, exactly as
   business_rules §3 sets out: the activity id is the leading part of the task code before its
   first separator, guarded for a code with no separator, then mapped to the activity code.

   ⚠ A TASK WITH NO MAPPING IS EXCLUDED FROM THE COUNT. Do NOT substitute '(unmapped)', do NOT
   COALESCE, do NOT ISNULL. That placeholder is not an activity code - it is a task whose code
   is unknown - so counting it adds a phantom +1 to any well with unmapped late work.
   business_rules §3 states this outright about the same pattern on WBS. Count
   COUNT(DISTINCT <activity code>) over non-NULL codes only.

6. INCLUDE ONLY WELLS WITH AT LEAST ONE DELAYED ACTIVITY CODE. A well with nothing late is not a
   finding, and padding the result with zero rows hides the ones that matter.

7. ORDER by delayed_activity_codes descending, then well id ascending so the order is stable
   between runs.""",
)


# -- 4. Crew availability --------------------------------------------------------
# The crew side of "how could this late task be recovered". Fleet-wide, one row per crew, so it
# is bounded by the number of crews rather than the task count and is never trimmed by the row
# cap - the same property that keeps the other fleet queries complete. It takes no parameter:
# the suggestion agent filters it to the late task's crew_type_id in Python, so one frozen query
# serves every task on every well.

CREW_AVAILABILITY = QuerySpec(
    key="crew_availability",
    label="Crew availability",
    question="For every crew on the current task records, what type of crew is it, how much open "
             "work does it carry, and is it free right now?",
    grain="one row per crew (bounded by the number of crews, never by the task count)",
    contract=(
        "crew_id",
        "crew_type_id",
        "open_tasks",
        "in_progress_tasks",
        "overdue_tasks",
        "wells_active",
        "latest_action_on",
        "availability_status",
    ),
    guidance="""\
WHAT THIS IS FOR. When a task is late, the question is which crews of the SAME crew type could
take it on. This query answers the crew side of that, fleet-wide, one row per crew; the matching
to a particular task happens later, on crew_type_id. So crew_type_id must be right, and every
count must describe CURRENT work, not history.

1. REDUCE THE HISTORY FIRST - the same reduction as activity_delay. Latest record per task:
   ROW_NUMBER() OVER (PARTITION BY the TRIMMED task well id, the TRIMMED task code ORDER BY
   <action date> DESC, <record id> DESC), keep rn = 1. Read EVERY column below from that reduced
   set. The raw table keeps one record per task per update, so counting it inflates every figure
   (measured: 33,820 records for 14,243 tasks) and credits a crew that was replaced on a task
   with work it no longer holds.

2. THE CREW UNIVERSE is every non-NULL crew id on those REDUCED records, on ANY well. Filter the
   NULL crew ids AFTER the reduction, never before it - filtering first lets an older record
   that named a crew stand in for a latest record that names none.

3. crew_type_id is the type on the crew's MOST RECENT reduced record (latest action date, then
   highest record id). EXACTLY ONE ROW PER CREW: do NOT group by crew type as well, or a crew
   whose type changed between records splits into two rows that can disagree about whether it
   is free. Return the raw id; do NOT join a crew or crew-type table to resolve it
   (milestone_rules §11 - the crew table has no primary key, so that join multiplies rows).

4. WORKLOAD counts only OPEN work on wells STILL IN PROGRESS - the same population filter as the
   other queries (the well's completion date is absent). Join task -> well with explicit casts on
   BOTH sides: the two well keys are declared different types. A task left open on a completed
   well is a stale record, not current work, and must not make a crew look busy.
   A crew whose every task is finished, or sits on a completed well, STILL GETS A ROW, with zero
   counts. Those crews are the likeliest to be free - dropping them hides the answer.
   - open_tasks:        reduced tasks on in-progress wells with no actual end
   - in_progress_tasks: of those, the ones with an actual start (started, not finished)
   - overdue_tasks:     of the open ones, those whose planned end is before today
   - wells_active:      DISTINCT wells among the open ones
   "Has not happened" is NULL in this database (§5.2) - test IS NULL, never invent a cutoff.
   Every count is 0, never NULL, for a crew with no open work.

5. latest_action_on is the most recent action date across the crew's reduced records, any well.
   It shows how recently the crew was recorded working at all, so a reader can tell a crew that
   is idle today from one nobody has recorded for a year.

6. availability_status: {crew_statuses}.
   {crew_available} when in_progress_tasks = 0, otherwise {crew_busy}. Derive in_progress_tasks
   once and test that result, rather than restating the condition.

7. No TOP, no well filter, no parameter. The result is bounded by the number of crews.

8. ORDER BY crew_type_id, then {crew_available} before {crew_busy}, then in_progress_tasks
   ascending, then overdue_tasks ascending, then crew_id - so within a crew type the crews with
   the most room come first.""",
)


# ORDER MATTERS. activity_summary runs BEFORE activity_delay so that, when no well is given
# on the command line, the worst well can be taken from the summary and drilled into.
QUERIES: tuple[QuerySpec, ...] = (WELL_SLIPPAGE, ACTIVITY_SUMMARY, ACTIVITY_DELAY, CREW_AVAILABILITY)
QUERIES_BY_KEY = {q.key: q for q in QUERIES}
DEFAULT_KEYS = tuple(q.key for q in QUERIES)


def guidance_for(spec: QuerySpec) -> str:
    """The spec's guidance with its vocabularies interpolated."""
    return (
        spec.guidance
        .replace("{start_statuses}", " | ".join(START_STATUSES))
        .replace("{end_statuses}", " | ".join(END_STATUSES))
        .replace("{execution_statuses}", " | ".join(EXECUTION_STATUSES))
        .replace("{risk_statuses}", " | ".join(RISK_STATUSES))
        .replace("{crew_statuses}", " | ".join(CREW_STATUSES))
        .replace("{crew_available}", CREW_AVAILABLE)
        .replace("{crew_busy}", CREW_BUSY)
    )


def describe() -> str:
    """Every query as one prompt block, shared by the Planner, Author and Verifier.

    One rendering for all three, so they cannot form different ideas of what each query is -
    the failure mode that lets an author satisfy a verifier about the wrong thing.
    """
    lines = ["THE QUERIES THIS PROJECT PRODUCES (milestone_rules §5.11 for the first three):", ""]
    for index, spec in enumerate(QUERIES, 1):
        lines.append(str(index) + ". " + spec.key + " - " + spec.label)
        lines.append("   answers: " + spec.question)
        lines.append("   grain:   " + spec.grain)
        lines.append("   returns: " + spec.contract_line())
        lines.append("")
    lines.append(
        "They are SEPARATE questions. A well can be on milestone track while its tasks slip, and "
        "the reverse. A delayed task is evidence that THAT TASK is slipping - it does not by "
        "itself prove the task is why the well is behind. crew_availability describes CREWS, not "
        "wells or tasks: a crew being free says nothing about why any task is late."
    )
    return "\n".join(lines)
