# BUSINESS RULES — PDO / AL TASNIM WELL PROJECT

Authoritative business interpretation. Follow it exactly. Do not infer, modify, or invent a
definition. If a question needs a rule that is not stated below, say the rule is **not yet
defined** — do not assume one.

The rules are stated in business terms, so they stay true when the data model changes. The
worked SQL examples show how they are applied against the CURRENT database — they are
illustrations, not a schema definition. Where an example names something the schema you
were given does not contain, the schema wins: keep the rule, find the new source, and
treat the example as out of date.

## 1. Parties and scope

| Term | Meaning |
|---|---|
| **PDO** | Petroleum Development Oman — the client. Provides the pegging sheet and the FLAF; performs drilling. |
| **Al Tasnim** | The contractor working for PDO. Performs Location Construction, Flowline Construction and hook-up. |
| **Well** | The central entity. Everything below is per well. |

Each well has **two construction projects**: **Location Construction** and **Flowline
Construction**. They are separate — never merge or substitute one for the other.

## 2. The key dates

Every rule below is built from these. Each is either an **expected** date (a plan, which can
be revised) or an **actual** date (something that happened). Never substitute one kind for
the other.

| Business term | Kind |
|---|---|
| Expected / master rig-on date | expected |
| Actual rig-on date | actual |
| Expected / predicted rig-off date | expected |
| Actual rig-off date | actual |
| Pegging sheet date | actual |
| FLAF date | actual |
| Hook-up completion = **well completion date** | actual |

The **expected rig-on date is the master date** the whole schedule is measured against: all
construction must finish in time for the rig to come on the well.

### Employee nationality

| Category | Meaning |
|---|---|
| National | Omani employee |
| Expat | non-Omani employee |
| not recorded | unknown — **not** an Expat |

Three groups, not two. "Not recorded" is a real third group, so an Omani-vs-non-Omani
answer must report all three or say how many are unrecorded. Never treat the remainder of a
National count as Expat.

## 3. Task → activity → WBS → crew

A task's code encodes the activity that task belongs to. Resolving a task to its WBS takes
**two hops**, and both are lookups. Never guess a WBS, and never use the activity id as one.

The two lookups are referred to below by their role, aliased `m` and `amc` in the examples:

- **activity mapping** (`m`) — activity id → activity code, and the owning crew code
- **activity description** (`amc`) — activity code → WBS description and crew

`<activity mapping>` and `<activity description>` in the SQL below are placeholders. Find
the real source of each in the schema you were given.

```
task_code                       e.g. 'FLME1180-30356'
 └ text before the FIRST '-'  = activity_id           'FLME1180'
    → activity mapping: Activity_ID           (CAST to nvarchar — it is a `text` column)
       → .New_Activity_Code                            'F-M-SLW-GWD-01'
          → activity description: activity_code
             → .activity_group_description = WBS      'Straightline Welding incl. supports'
             → .crew_code                  = crew     'YWS-0602'
```

- The text before the FIRST '-' is ALWAYS `activity_id`; a task_code may hold more dashes
  (`FLME1180-34516-T02` is still `FLME1180`). Extract it NULL-safely:
  `LEFT(task_code, NULLIF(CHARINDEX('-', task_code), 0) - 1)`
- ⚠ `Activity_ID` is a legacy `text` column — comparing it directly FAILS with "the data types
  text and nvarchar are incompatible". ALWAYS `CAST(m.Activity_ID AS nvarchar(50))`.
- ⚠ Join on `New_Activity_Code`, NEVER `Old_Activity_Code`. the activity-description lookup was migrated to
  the new scheme, and the old column now fails SILENTLY — a NULL WBS, not an error.
  (Maintainers: this flipped once. If WBS comes back empty everywhere, count how many Old vs New
  codes match the description lookup's `activity_code` using two SEPARATE joins — a single join with
  `IN (Old, New)` matches on either column then counts both, which reverses the answer. Higher
  count wins.)
- `task_code` is an INTERNAL key: use it to derive `activity_id`, and NEVER display it when the
  question asks about ACTIVITIES. Identify an activity by `New_Activity_Code`, listed DISTINCT.
- A repeating `activity_id` is CORRECT — one well runs the same activity many times. Never
  de-duplicate it away; choose the GRAIN the question asks for:

```sql
-- Second hop, identical in every shape below:
--   LEFT JOIN <activity description> amc ON amc.activity_code = m.New_Activity_Code
-- LEFT so unmapped work stays visible and the unmapped tally can be non-zero. The ACTIVITY LIST
-- is the one exception: it uses an inner JOIN, since an activity with no mapping row is not
-- a listable activity.

-- per ACTIVITY  ("what activities does this well have")  -> one row per activity, NO task_code
SELECT DISTINCT a.activity_id, m.New_Activity_Code AS activity_code
FROM a JOIN <activity mapping> m ON CAST(m.Activity_ID AS nvarchar(50)) = a.activity_id

-- per TASK  (ONLY when the question asks for tasks)  -> one row per task_code
SELECT a.task_code, a.activity_id, m.New_Activity_Code AS activity_code,
       amc.activity_group_description AS wbs, amc.crew_code

-- WBS BREAKDOWN  ("which WBS", "tasks per WBS")  -> one row per WBS
SELECT ISNULL(amc.activity_group_description, '(unmapped)') AS wbs,
       COUNT(DISTINCT a.task_code) AS tasks
GROUP BY amc.activity_group_description

-- WBS COUNT  ("how many WBS")  -> the number, with the unmapped tally beside it
SELECT COUNT(DISTINCT amc.activity_group_description) AS wbs_count,
       COUNT(DISTINCT CASE WHEN amc.activity_group_description IS NULL
                           THEN a.task_code END)      AS unmapped_tasks
```

⚠ NEVER `COUNT(DISTINCT ISNULL(activity_group_description, '(unmapped)'))` — "(unmapped)" is not a
WBS but a task whose WBS is unknown, so it adds a phantom +1: one real well has 21 WBS and 2
unmapped tasks, and that expression reports 22. Give the two figures separately. Never count the
per-TASK rows for a per-WBS question either — that same well returns 90 task rows for its 21 WBS.

## 4. Milestone deadlines

Every deadline is derived from a date in §2; none is stored. For a milestone that **has** an
actual date, "missed" means the actual date is later than the deadline, or the deadline has
passed and the actual date is still absent. The two construction milestones have no actual
date — see the rig rule below.

| Milestone | Owner | Has an actual date | Deadline |
|---|---|---|---|
| Pegging sheet issued | PDO | yes | expected rig-on date − 60 days |
| FLAF issued | PDO | yes | expected rig-on date − 90 days |
| Location Construction complete | Al Tasnim | no — computed | expected rig-on date − 1 day |
| Flowline Construction complete | Al Tasnim | no — computed | expected rig-on date − 1 day |
| Hook-up complete | Al Tasnim | yes | rig-off date + 2 days |

**Construction deadlines are COMPUTED from the expected rig-on date** — never read from a
stored value. Nothing in the data is an approved Location Construction completion date, so
never substitute one that merely looks like it.

**Judging a missed construction deadline.** With no actual completion date, judge it from
the rig: the deadline has passed and the rig has still not come on the well.

```
missed  ⇔  CAST(GETDATE() AS date) > DATEADD(day, -1, ex_rig_on_date)
           AND rig_on_date IS NULL
```

If the rig has come on, the construction deadline is **not** treated as missed. This rule
therefore identifies wells still waiting for a rig past their deadline; it does not detect a
construction delay on a well whose rig has already arrived.

**Hook-up deadline — planned vs actual.** Before the rig is off, the planned deadline is the
expected rig-off date + 2 days. Once the actual rig-off date exists, the deadline is that
date + 2 days, and **the actual date takes precedence**.

## 5. Schedule variance — being EARLY is not an anomaly

An expected date and an actual date differing is **normal**. A difference is never, on its
own, a data error, a suspicious value, or an anomaly to flag. Read the **direction**:

| Comparison | Meaning | Report it as |
|---|---|---|
| actual **earlier than** expected | the work finished sooner than planned | **ahead of schedule — accelerated.** A GOOD outcome. |
| actual **equal to** expected | on the planned date | on schedule |
| actual **later than** expected | the work finished after the planned date | behind schedule — delayed |

This applies to **both** rig dates:

* Rig-on earlier than expected → construction finished early and the rig came on ahead of
  the master date. The well is **accelerating**, not anomalous.
* Rig-off earlier than expected → drilling finished early. Again ahead of schedule, not a
  wrong date.

Use one signed measure, so the sign always carries the meaning:

```
schedule_variance_days = DATEDIFF(day, ex_rig_on_date, rig_on_date)
    negative → AHEAD of schedule (accelerated)
    zero     → on schedule
    positive → BEHIND schedule (delayed)
```

⚠ NEVER describe an early actual date as a delay, a variance problem, a data-quality issue
or an anomaly, and never take its absolute value and call it "days of delay". Only a
**later** actual date is a delay. When a well is early, say so plainly as good news.

This section is about actual-vs-expected variance only. It does not change the milestone
deadline rules in §4.

## 6. Delay consequences — ownership matters

| Delay | Consequence |
|---|---|
| **Location Construction** late | Can cause a well/rig delay, and may lead to a **penalty** for Al Tasnim. |
| **Flowline Construction** late | Can cause a well/rig delay, but per the business rule this is **not a due well for Al Tasnim**, because the originating issue is from PDO. |

Never report a delay as Al Tasnim's without applying this distinction.

## 7. Lifecycle order

```
PDO issues pegging sheet  → Al Tasnim: Location Construction ┐
PDO issues FLAF           → Al Tasnim: Flowline Construction ┘
  → both complete before the expected rig-on date
  → PDO rig-on
  → PDO drilling
  → rig-off
  → well cleaned, Christmas Tree fitted    ← "Christmas Tree" = the pipe/motor assembly
  → handover to Al Tasnim
  → Al Tasnim hook-up
  → WELL COMPLETED
```

Drilling runs from actual rig-on to actual rig-off and is **PDO's** activity, not Al
Tasnim's.

## 8. Well completion

A well is **completed** when Al Tasnim's hook-up is complete. The hook-up completion date
**is** the well completion date, and:

```
completed  ⇔  eng_completion_date IS NOT NULL
```

A hook-up *deadline* and a *completed well* are different things — do not conflate them.

## 9. Project → WBS → Activity, and PMS weightage

Hierarchy: **Project → WBS / Activity Group → Activities**. Both Location Construction and
Flowline Construction can have their own WBS/activity structures.

```
Project
├── WBS 1  ── Activity 1, Activity 2, Activity 3
└── WBS 2  ── Activity 4, Activity 5
```

Weightage rules:

* Each WBS/activity group carries its own **PMS percentage (weightage)**. Different WBS
  groups can have different weightages — e.g. WBS 1 = 30%, WBS 2 = 20%, WBS 3 = 50%.
* Activities **within the same WBS have equal weightage**. For a WBS holding `N` activities,
  each activity's share inside that WBS is `1 / N`.
* An activity's contribution to the overall project PMS is therefore:

  ```
  activity contribution = parent WBS PMS weight × (1 / N)
  ```

* **Do not** assume activities in different WBS groups carry the same overall PMS weight —
  their contribution depends on the weight of their parent WBS.

## 10. Never interchange these pairs

| | vs | |
|---|---|---|
| Expected / master date | ⟷ | Actual date |
| Expected rig-on date | ⟷ | Actual rig-on date |
| Expected rig-off date | ⟷ | Actual rig-off date |
| PDO responsibility | ⟷ | Al Tasnim responsibility |
| Location Construction | ⟷ | Flowline Construction |
| Project-level PMS weightage | ⟷ | Activity-level equal weighting |
| Hook-up deadline | ⟷ | Well completion |

The expected rig-off date is a planning figure. Never report it as the actual rig-off date.

## 11. Not yet defined

Answer what you can and state plainly that the rule is undefined — never invent one:

* Whether Location Construction and Flowline Construction have an **actual completion date**
  recorded anywhere. Nothing is approved as that date, so never substitute something to fill
  the gap — on-time/missed is judged by the rig rule in §4 instead.

## 12. Strict instructions

* These rules are authoritative for every question about PDO, Al Tasnim, wells, Location
  Construction, Flowline Construction, pegging, FLAF, rig-on, rig-off, hook-up, well
  completion, WBS, activities and PMS.
* Do not infer, modify, or invent a business definition.
* If the data holds a value that conflicts with a business rule, **report the actual value**
  and explain the business-rule interpretation separately. Never silently change a result.
