# MILESTONE AND TASK RULES

How a delay is calculated, and which columns the calculation reads.

`business_rules.md` and `slippage.md` explain what a milestone MEANS in the business. This
file states how each verdict is WORKED OUT, so the arithmetic never has to be inferred from
prose.

It has two parts, and they have different lifetimes — read the distinction before trusting
either:

**Part 1 (§1–§5) — how delay is calculated.** Business concepts only. No table, column or
file is named, because none of those is what makes these rules true. Part 1 stays correct
when the database changes.

**Part 2 (§6–§12) — where the values come from.** The columns of one specific database:
what is read, what it means, what type it is, why the query needs it. **Part 2 goes stale
when that database changes.**

The schema you are given is always authoritative. Where Part 2 names a column the schema
does not contain, that column was renamed or removed — follow the procedure in
`schema_guide.md`. Treat Part 2 as a well-informed starting point, never as proof that a
column exists. Take output names and their exact spellings from the contract in
`sql_examples.md`.

There are two layers of logic throughout. Milestones are the well's contractual chain of
dates. Tasks are the individual pieces of work between them. A well can be on milestone
track while its tasks slip, and the reverse, so both layers are needed to explain a delay.

---

# PART 1 — HOW DELAY IS CALCULATED

*Concepts only. Independent of any particular database.*

## 1. Milestone arithmetic

Deadlines are DERIVED, never stored. Do not look for a stored deadline — compute it from
an expected date.

The expected rig-on date is the anchor for most of the chain. Deadlines before rig-on count
BACKWARDS from it, so an earlier deadline is a larger negative offset.

### The six milestones, in the real order of the work

| # | Milestone | Deadline | Completed when |
|---|---|---|---|
| 1 | FLAF | 90 days before the expected rig-on date | the FLAF was issued |
| 2 | Pegging | 60 days before the expected rig-on date | the well was pegged |
| 3 | Construction | 1 day before the expected rig-on date | the rig arrived |
| 4 | Rig-on | the expected rig-on date itself | the rig arrived |
| 5 | Rig-off | the expected rig-off date itself | the rig left |
| 6 | Hook-up | 2 days after rig-off | engineering completion was recorded |

FLAF is due 90 days before the rig arrives, pegging 60 days before, and construction must
be finished the day before.

### Hook-up is the one that counts forward

Hook-up is the only deadline measured from rig-off rather than rig-on, and the only one
with a positive offset — two days after the rig leaves.

It also has two possible bases. Use the ACTUAL rig-off date when it is known, and fall back
to the expected rig-off date only when it is not. The actual date always wins: once the rig
is genuinely off, the original plan no longer sets the deadline.

### Construction has no completion date of its own

Nothing records "construction finished". The rig arriving is the proxy: if the rig is on
site, construction is complete by definition. So construction is judged only on whether the
rig arrived before its deadline, and it has no "finished early" or "finished exactly on
time" outcomes — only missing-data, missed, pending, and rig-arrived.

### Every deadline needs a missing-data guard

If the expected date a deadline is derived from is absent, the deadline cannot exist, and
the milestone's verdict is a data-quality outcome — never a schedule outcome. Check this
FIRST, before any date comparison. An absent date compared against anything yields neither
true nor false, so without this guard the milestone silently reads as on time, which is the
most dangerous wrong answer this system can produce.

---

## 2. Milestone status

Every milestone is reduced to one verdict. Evaluate in this order, stop at the first match:

1. The expected date the deadline depends on is absent  → data-quality outcome
2. Not completed, and the deadline has already passed   → missed
3. Not completed, deadline still in the future          → pending
4. Completed before the deadline                        → early
5. Completed exactly on the deadline                    → on time
6. Anything else — meaning completed after the deadline → late

Why that order matters:

- The missing-data check must be first, or an absent expected date reads as on time.
- Missed must be tested before pending: both have no completion date, and only the deadline
  comparison separates "late and still not done" from "not yet due".
- Late is the final catch-all, so "completed, but after the deadline" needs no extra
  comparison of its own.

There is no tolerance window. On time means the completion date equals the deadline
exactly; one day either side is early or late.

### One vocabulary for all six milestones

Use a single set of outcome values across every milestone and every query. A reader
comparing two milestones must not have to know which one they are looking at to interpret
the value. Do not invent a second set of words for the same six outcomes, and do not
abbreviate one milestone's values while spelling out another's.

---

## 3. Variance days

Alongside its verdict, each milestone reports a whole number of days measured FROM THE
DEADLINE. Positive means late, negative means early.

Four cases, in this order:

1. **Cannot be measured** — the expected date is absent, so there is no deadline to measure
   from. Report nothing.
2. **It happened** — measure from the deadline to the completion date.
3. **It has not happened and the deadline has passed** — measure from the deadline to
   today, so the number grows every day the milestone stays undone. Without this case an
   overdue milestone reports no delay at all, which is exactly backwards.
4. **Not due yet** — report nothing.

### "Not due yet" must be empty, not zero

Zero is a real measurement: completed exactly on the deadline. If "not due yet" is also
zero, the two become indistinguishable and anything that averages or totals the column is
wrong. An absence of measurement must be reported as absent.

### One suffix for the concept

Name every one of these columns the same way in every query. One concept, one naming
pattern.

---

## 4. The slippage verdict

The well listing reduces all of a well's milestones to a single headline reason.

### A milestone counts as failed when

Its expected date exists, AND either:

- it was completed, but after its deadline; or
- it has not been completed and its deadline has already passed.

### The condition is written twice

Each milestone's failure condition is needed in two places: once to choose the headline
reason, once to decide whether the well belongs in the listing at all. Those two copies
must stay identical. If they drift apart, a well appears in the listing carrying the "not
slipped" reason — that symptom is how you detect it.

### Choosing the headline reason

Test the milestones in a fixed priority order and take the first that failed. The order is
a business decision about which failure to name when several have failed, so keep the order
the contract specifies rather than choosing your own.

A well that failed no milestone is excluded by the listing filter, so the "not slipped"
value should never actually reach the output.

### Population

Both queries cover only wells still in progress — those with no engineering completion
recorded. One consequence follows directly and is easy to miss: inside these queries a
completion date is ALWAYS absent, so any branch testing for a completion date being present
is unreachable and must not be written.

### Ordering

The listing is ordered by the expected rig-on date, soonest first, so the well needed
earliest appears at the top.

---

## 5. Task arithmetic

The per-well investigation goes a layer deeper: every task of the well, with its own
planned and actual dates, its own crew, and its own progress. This is what lets the answer
say WHICH task and WHICH crew caused the delay rather than only that the well is late.

### 5.1 Which record represents a task

The task table is a history: it keeps one record per task per update, so the same task
appears many times. Only the most recent record describes the task as it stands now.

Reduce to one record per task per well before doing anything else: take the latest by its
action date, and where two share an action date, the later-created record wins. Skipping
this multiplies every task and corrupts every count and every variance downstream — and it
does so silently, because the query still succeeds.

### 5.2 Placeholder dates mean "not set"

Actual start and actual end use a placeholder date to mean "has not happened". Treat it as
absent before any comparison. Left alone it reads as a real date far in the past, which
makes every task look completed decades early.

### 5.3 Start verdict

Compares the actual start against the planned start:

1. No planned start → data-quality outcome
2. Not started, and the planned start has passed → not started and slipping
3. Not started, planned start still ahead → not started, on schedule
4. Started before the planned start → started early
5. Started exactly on the planned start → started on time
6. Started after the planned start → start delayed

### 5.4 End verdict

Compares the actual end against the planned end. Completed cases first, then in-flight:

1. No planned end → data-quality outcome
2. Completed before the planned end → completed early
3. Completed exactly on the planned end → completed on time
4. Completed after the planned end → completed late
5. Not completed, planned end still ahead → in progress, not late
6. Not completed, planned end is today → due today
7. Not completed, planned end has passed → overdue and lagging

"Due today" is its own outcome, separate from both in-progress and overdue. A task due
today is not yet late but has no slack left, which is operationally different from either
neighbour.

### 5.5 Execution state

A simpler, orthogonal view of the same task — where it is in its lifecycle, ignoring
whether it is on time:

- ended → completed
- never started, planned start not yet passed → not started
- never started, planned start has passed → not started and late
- started but not ended → in progress

### 5.6 Schedule risk

One triage flag per task, so the reader can sort by severity. Evaluate in order:

1. **Red** — the end is missed: either not completed with the planned end already passed,
   or completed after the planned end. The task itself has already cost time.
2. **Amber, start slipping** — not started and the planned start has passed. Nothing is
   lost yet, but it is heading for red.
3. **Amber, start delayed** — started, but later than planned. Time was lost at the front.
4. **Green** — everything else.

Red is about the END date, both ambers about the START date. A task can be started late and
still finish on time; that stays amber, because the overrun did not reach the schedule.

### 5.7 Progress

Progress is stored as a fraction from 0 to 1, not a percentage. Multiply by 100 to report a
percentage, and report nothing when the underlying value is absent. Getting this wrong
understates every task's progress by a factor of a hundred.

### 5.8 Quantity

Two independent sources can record how much work was done. They can agree, disagree, or
only one may be present. Report both which source was used and the value taken:

- both present and equal → sources agree
- both present and different → sources conflict
- only the first present → first source only
- only the second present → second source only
- neither → no quantity source

Prefer the first source when both exist. Flagging a conflict matters more than resolving
it: a task whose two quantity records disagree is a data problem someone must look at, and
silently picking one hides it.

Remaining quantity is the planned amount minus the observed amount, and can never be
negative — more work done than planned means zero remaining, not a negative number.

### 5.9 Productivity

Quantity achieved per hour worked, from whichever pair of quantity-and-hours columns is
usable. Report which source was used, and report a separate flag for whether productivity
could be computed at all.

Both guards are required before dividing: the hours value must be present AND greater than
zero. Hours of zero is common in this data — it means the task was recorded but no work was
logged — and dividing by it fails the whole query rather than that one row.

### 5.10 Grouping and ordering

Tasks belong to activities, and an activity's identifier is derived from the leading part
of the task's code, before its first separator. Guard that the separator exists: a code
without one yields no activity rather than a wrong one.

Order the task list so the most urgent is first: red risk before amber before green, then
the largest end overrun, then the largest start overrun, then the earliest planned end.
The reader works from the top, so this ordering is part of the answer, not presentation.

---

# PART 2 — WHERE THE VALUES COME FROM

*This database specifically. Re-check against the schema whenever it changes.*

Types shown are as declared. Several of them are the reason a query is written the way it
is, so read the type as carefully as the description.

## 6. The well record — the well and its milestone dates

One row per well. This is the anchor table for both queries.

| Column | Type | Why it is used |
|---|---|---|
| `well_id` | `int NOT NULL` | Identifies the well. The per-well query filters on it; the listing returns it. |
| `project_id` | `uniqueidentifier` | The well's project. A GUID, so it is never shown to a reader — resolve it to a code and name if a project lookup is available. |
| `station_id` | `int` | Where the well sits. Returned by the listing for grouping. |
| `well_type_id` | `int` | What kind of well. Returned by the listing for grouping. |
| `ex_rig_on_date` | `date` | **The anchor of the whole schedule.** FLAF, pegging and construction deadlines are all counted backwards from it, and it is the rig-on deadline itself. If it is null, four milestones cannot be judged at all. |
| `rig_on_date` | `date` | When the rig actually arrived. Settles both rig-on and construction — construction has no completion date of its own, so the rig arriving is its proxy. |
| `ex_rig_off_date` | `date` | Expected rig departure. The rig-off deadline, and the fallback base for the hook-up deadline. |
| `rig_off_date` | `date` | Actual rig departure. Settles rig-off, and is the preferred base for the hook-up deadline once known. |
| `pegged_date` | `date` | When the well was pegged. Settles the pegging milestone. |
| `flaf_issue_date` | `date` | When the FLAF was issued. Settles the FLAF milestone. |
| `eng_completion_date` | `date` | Engineering completion. **Used as the population filter:** both queries keep only wells where this is null, i.e. still in progress. A consequence: inside these queries it is always null, so no branch may test it for being present. |
| `progress` | `varchar(20)` | Overall well progress. **Stored as text, not a number.** Returned raw, never used in arithmetic — doing maths on it requires a cast that can fail on non-numeric rows. |
| `flowline_const_progress` | `int` | Flowline construction progress. Context for a delay attributed to flowline work. |

---

## 7. The task records — the tasks, and who did them

**This table is a history: many rows per task.** It must be reduced to the latest row per
well and task before anything else happens. Every column below is read from that reduced
set, never from the raw table.

### Identity and recency

| Column | Type | Why it is used |
|---|---|---|
| `id` | `bigint NOT NULL` | The record's own id. Breaks ties when two records share an action date, and is returned so a task row can be traced back. Not a task identifier — the same task has many ids. |
| `ActionOn` | `date` | When the record was written. **The recency key**: the latest one describes the task as it stands now. |
| `task_code` | `nvarchar(100)` | Identifies the task. Also the source of the activity grouping — the part before the first separator is the activity id. Trim it; stored values carry padding. |
| `well_id` | `varchar(10)` | Links the task to its well. **Declared `varchar(10)` on the task record but `int` on the well record** — the two sides of this join have different types, which is why both must be converted explicitly before comparing. This is the single most important type fact in the schema. |
| `schedule_id` | `int` | Which schedule the task belongs to. Returned as context. |
| `project_id` | `uniqueidentifier` | The task's own project, returned alongside the well's so a mismatch is visible. |

### Dates — the whole basis of task delay

| Column | Type | Why it is used |
|---|---|---|
| `target_start` | `date` | Planned start. The start verdict compares the actual against it. |
| `target_end` | `date` | Planned end. The end verdict and the red risk flag both key off it. |
| `actual_start` | `date` | When work actually began. **Uses a placeholder date for "not started"** — convert to absent before comparing, or the task reads as started decades ago. |
| `actual_end` | `date` | When work actually finished. Same placeholder problem. Drives the end verdict and whether the task counts as complete. |

### Crew — who was on the task

| Column | Type | Why it is used |
|---|---|---|
| `planned_crew` | `nvarchar(255)` | The crew planned for the task, as text. Directly readable, so it is the most useful crew evidence the query currently returns. |
| `crew_type_id` | `int` | What kind of crew. An id, so it needs a lookup to become meaningful to a reader. |
| `crew_id` | `int` | Which specific crew. An id; see §6 on why it is returned raw. |
| `emp_id` | `varchar(100)` | The employee on the task. An id; see §6. |

### Progress and quantity

| Column | Type | Why it is used |
|---|---|---|
| `progress` | `decimal` | Task progress as a fraction from 0 to 1 — multiply by 100 for a percentage. **Note it is `decimal` on the task record but `varchar` on the well record**: the same concept is typed differently in the two tables, so they cannot be handled the same way. |
| `completed` | `bit` | Completion flag. Compare to 1, never to a string. |
| `planned` | `decimal` | The quantity of work planned. Remaining work is this minus what was observed. |
| `data_qty` | `decimal` | Quantity achieved, first source. Preferred when both sources exist. |
| `data_hours` | `decimal` | Hours worked, first source. Paired with `data_qty` for productivity. |
| `daily_actual_quantity` | `decimal` | Quantity achieved, second source. Used when the first is absent. |
| `daily_actual_hours` | `decimal` | Hours worked, second source. **Must be checked for greater than zero before dividing** — zero hours is common and means the task was recorded with no work logged. |

The two quantity sources exist because the same work is captured twice by different
processes. They can disagree, and the query reports the disagreement rather than resolving
it: a conflict is a data problem someone needs to see.

---

## 8. The task-to-activity mapping — task code to activity and crew

| Column | Type | Why it is used |
|---|---|---|
| `Activity_ID` | `text(2147483647)` | Joined against the activity id derived from the task code. **A legacy large-text column** — it cannot be grouped, compared or made distinct without being cast first, and it must be cast the same way everywhere. |
| `New_Activity_Code` | `nvarchar(50)` | The activity code the task belongs to. Feeds the activity description lookup. |
| `New_Crew_code` | `nvarchar(50)` | The crew code that owns the activity — the planned owner of the work, as opposed to who actually did it. |

This table holds several rows per activity id and they can conflict. Collapse it to one row
per id before joining, returning a value only when it is unambiguous so a conflict yields
nothing rather than an arbitrary pick.

The `Old_*` columns are the superseded mapping and are not used.

---

## 9. The activity descriptions

| Column | Type | Why it is used |
|---|---|---|
| `activity_code` | `nvarchar(50)` | Joined from the mapping table's activity code. |
| `activity_group_description` | `nvarchar(50)` | The human-readable activity name. **This is what makes a task explainable** — without it the answer can only quote a task code. |

Also holds several rows per code; collapse it the same way.

---

## 10. Columns that do not exist — they are calculated

Do not search the schema for any of these. Every one is derived, and looking for a stored
version is a common way to invent a column name.

Deadlines for all six milestones; every `status` verdict at both milestone and task level;
every variance or delay figure; the activity id, sliced from the task code; progress as a
percentage; remaining quantity; productivity per hour; the schedule risk flag; and the
single headline slippage reason.

---

## 11. Columns deliberately left unresolved

`crew_id` and `emp_id` are returned as raw ids rather than names, even though names would
be more useful, because the schema gives no safe way to resolve them:

- The crew table has no primary key and holds many rows per crew id, so joining it directly
  would multiply every task row.
- The employee table offers several plausible target columns for the employee id and
  declares no foreign key, so any choice would be a guess — and a wrong one returns
  confident, wrong names.

This is the rule from `schema_guide.md` applied: when a join is ambiguous, return the raw
id and let the reader see there is an id, rather than resolve it wrongly. If either table
later gains a primary key or a declared foreign key, both become safe to join.

---

## 12. Known gap

The JSON the application builds asks for a project code and name, and a crew type code and
name. The per-well query does not currently return them, so those four fields arrive empty
every time. They are not errors and nothing fails — which is exactly why the gap is easy to
miss.

Closing it needs the project table and the crew-type table joined on their primary keys.
Both are safe joins: the join column is each table's primary key, so at most one row
matches and no task row can be multiplied.
