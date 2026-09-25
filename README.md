# Well Slippage — PDO / Al Tasnim

Detects contractual **milestone slippage** and task-level **activity delay** across the active
well portfolio, using LangGraph agents that write and verify their own SQL.

The design splits slow, expensive reasoning from fast serving:

```
backend/main.py   schema changed:  agents write + verify SQL, then freeze it   ~90 s, LLM tokens
   │              schema stable:   run the frozen SQL from sql/                ~25 s, one LLM call
   │              writes sql/*.sql (committed)  out/*.csv  out/brief.md
   ▼
backend/api/      runs that verified SQL on request                            ~2 s, no LLM
   │              Suggest: one model call over those same results              ~1 min, on click
   ▼
frontend/         Next.js dashboard
```

The API serves the verified SQL; it does not re-derive it. Once a query is verified it is a
deterministic artefact, so serving a page means running it. The one endpoint that calls a model is
the **suggestion agent** (below): it reasons over those same verified results and never writes SQL.

The same reasoning applies to the pipeline itself, which is why verified SQL is **frozen** into
`sql/` and reused until the database's *structure* changes.

## Project layout

```
well/
├── backend/                  Python: agents, pipeline, API
│   ├── main.py               the pipeline CLI
│   ├── api/                  FastAPI: serves the frozen SQL's results, plus the suggestion agent
│   ├── app/                  agents, LangGraph graph, DB introspection, config
│   ├── domain/               business_rules 1.md, milestone_rules 1.md (grounding for the agents)
│   ├── sql/                  frozen, verified queries (committed)
│   ├── requirements.txt
│   ├── .env                  DB + OpenAI settings (git-ignored)
│   └── out/ logs/ .cache/    run outputs, logs, schema cache (git-ignored)
├── frontend/                 Next.js dashboard
│   ├── app/  components/  lib/
│   └── package.json
└── .venv/                    Python environment (git-ignored)
```

The two halves only meet over HTTP: the frontend proxies `/api/*` to the backend on port 8000
(`frontend/next.config.ts`, override with `API_URL`), so either can move or be deployed alone.

Everything the backend reads or writes (`.env`, `sql/`, `out/`, `.cache/`, `logs/`, `domain/`)
is resolved from **its own folder**, not from wherever it was started. The one exception is an
explicit `--out DIR`, which like any CLI argument is relative to where you run it. Paths in the
rest of this file are relative to `backend/` unless they say otherwise.

⚠ `domain/` is not optional. If the two rule documents are missing, the agents do not fail: they
log a warning and run **without the business rules**, which is much harder to notice.

## The four queries

| Query | Answers | Grain | Rows |
|---|---|---|---|
| `well_slippage` | which wells failed a contractual milestone | one per well | 222 |
| `activity_summary` | how many activity codes are delayed per well | one per well | 189 |
| `activity_delay` | which tasks are late on **one** well, with each task's crew id and crew type id | one per task | 88 |
| `crew_availability` | every crew's type, open work, and whether it is free | one per crew | 1,253 |

Row counts are from the current database; they move with the data, not with the code.

Every query is bounded by the *fleet*, by *one well* or by the *number of crews*, never by
accumulated task history — which is why none of them can be silently trimmed by the row cap.
`activity_delay` takes the well as a **bound parameter**, so the API can drill into any well
without re-running the agents.

⚠ **"Available" is an assumption, not a recorded rule.** Neither rule document defines it, and the
database holds no roster, leave, shift or location data. `crew_availability` calls a crew
`AVAILABLE` when no task it is assigned to is *in progress* on a well still in progress — nothing
more. It is marked `⚠ ASSUMPTION` in `app/graph/queries.py`. Also note that about two thirds of
current task records carry no crew id, so crew workload is understated.

## Recovery suggestions

On a well's page, **Suggest recovery** above the task list asks the suggestion agent
(`api/advisor.py`, `POST /api/wells/{id}/suggest-well`): *why is this well delayed, and how could
it be overcome?* It starts from the failed milestones and their owners, then the late work grouped
by WBS and by **crew type**, and proposes actions using free crews of each type, ranked by least
open work.

The task-level endpoint (`POST /api/wells/{id}/suggest?task_code=…`) still exists, but the page no
longer has a Suggest button on each task.

What keeps a model's opinion apart from the verified figures:

- **The evidence is selected in Python** from the frozen queries. The agent reasons over it; it
  runs no SQL and counts nothing.
- **The answer is returned beside that evidence**, and the page shows the two side by side.
- **It may only name crews it was given.** A crew id outside the candidate list is flagged as
  unverified on the page.
- An answer is **reused only while its data is unchanged**. Every click re-reads the well's
  tasks, its milestones and the crews of the types it needs (~1 s); if any of that has changed,
  the agent is asked again (~35 s). Row order is ignored, and a change to an unrelated crew type
  does not count. "Ask again" forces a new answer regardless.
- Crew availability is **read live on every request**, never cached: checking whether it has
  changed costs as much as re-reading it on this database (see `crew_availability()` in
  `api/service.py`).

This reverses the brief's rule against recommending manpower changes *for this panel only*: the
synthesizer still recommends nothing, and every suggestion is labelled as AI-generated.

## The frozen query store (`backend/sql/`)

Authoring and verifying four queries costs minutes of LLM latency and real tokens. What it
produces is deterministic: a SELECT proved correct against one specific schema. Nothing about it
changes when a row is loaded, so paying for it again buys nothing.

```bash
python main.py --frozen          # what is in sql/, and whether it is still valid
```

```
  well_slippage     current       frozen 2026-09-18 04:37:33, 216 rows then
  activity_summary  current       frozen 2026-09-18 04:37:33, 184 rows then
  activity_delay    current       frozen 2026-09-18 04:37:33, 88 rows then
```

**What invalidates a freeze** is the *structural* fingerprint — database identity, table scope,
columns with declared types, constraints. Deliberately **not** row counts:

| Change | Frozen SQL | Schema block / value hints |
|---|---|---|
| rows loaded | still valid — reused | rebuilt (hints are sampled from live data) |
| column renamed, retyped or dropped | re-authored | rebuilt |
| constraint added or dropped | re-authored | rebuilt |
| different `DB_NAME`, or `INCLUDED_TABLES` narrowed | re-authored | rebuilt |
| `_RENDERER_VERSION` bumped | still valid | rebuilt |

Keying the freeze on the schema cache's fingerprint instead would have discarded every query on
every ingest — in a database that loads daily, nothing would ever be reused. The two hashes are
`structure_fingerprint` and `_fingerprint` in `backend/app/db/introspect.py`.

`sql/` is **committed**, unlike `out/`. It is schema rather than data — a SELECT naming columns,
with no well ids or dates in it — it is the artefact a reviewer reads to see what changed about
how slippage is calculated, and it lets a fresh clone serve the dashboard with no API key.

Each file carries a provenance header (frozen-at, fingerprint, output contract, row count) above
a sentinel line; `manifest.json` records a hash of the body below it. **Hand editing is allowed
and reported**: the hash stops matching, `--frozen` shows the file as `hand-edited`, and the run
reports that query as unverified rather than claiming an approval that no longer covers the text.

Three things make a run fall back to authoring even when the fingerprint matches — frozen SQL that
no longer executes (a redefined view, a revoked grant), a result whose columns no longer match the
output contract, or a missing file. The run repairs itself rather than failing.

## The pipeline

```
                    ┌── frozen + current ──> reuse ──┐   (no agent; just execute sql/)
detect ──> decide ──┤                                ├──> synthesize
                    └── stale / missing ──> planner ──> queries ──┘
                                                          │
                     every outstanding query, CONCURRENTLY, one isolated state each:
                     sql_author ──> validator ──> executor ──> verifier ──> freeze
                          ^             │            │            │
                          └─────────────┴────────────┴────────────┘
                                rewrite, with a specific reason
```

- **detect** — introspects the live schema and samples real lookup values. Cached behind a
  fingerprint covering columns, constraints **and** row counts, so new data rebuilds the block;
  it also reads the narrower *structural* fingerprint that decides what can be reused.
- **decide** — routes each query to `reuse` or to `planner`. `--regenerate` forces `planner`.
- **reuse** — executes the frozen SQL. No LLM and no authoring; the column contract is still
  re-checked, because it is free and catches a hand edit that changed the output shape.
- **planner** — binds each business term ("the expected rig-on date") to a real column. Separate
  from the author so an unresolvable binding is *reported* rather than guessed. Its worklist is
  narrowed to what the freeze could not serve, so one stale query does not cost you the other two.
- **queries** — runs every outstanding query at once, on isolated state. They have no dependency
  on each other: the per-well query binds a `?`, so it needs only a *sample* well
  (`SAMPLE_WELL_ID`) to verify against, not one derived from another query's result.
- **validator** — deterministic SELECT-only gate. Single-pass scrub of strings, comments and
  bracketed identifiers, so a keyword inside data is not mistaken for a command.
- **executor** — deterministic. Binds parameters, never interpolates.
- **verifier** — independent review, **fails closed** on an unparseable verdict. Rejected results
  are set aside, never deleted.
- **freeze** — writes each *verified* query to `sql/`, stamped with the fingerprint it was
  verified against. An unverified result is never frozen.
- **synthesize** — writes the brief. Every count is computed in Python first, so the model is
  formatting exact figures rather than counting a sample.

## Running it

**1. Configure `backend/.env`** (git-ignored):

```
DB_SERVER=…
DB_NAME=…
DB_USER=…
DB_PASSWORD=…
DB_DRIVER=ODBC Driver 18 for SQL Server
DB_TRUST_SERVER_CERTIFICATE=yes

OPENAI_API_KEY=…
OPENAI_MODEL=gpt-5.6-luna

ALLOWED_SCHEMAS=landing,well,dbo
INCLUDED_TABLES=well.well_master,well.task_daily,dbo.mapping_master,dbo.activity_master_csv
SAMPLE_WELL_ID=35543
MAX_ROWS=2000
VERIFY_RETRIES=2
```

⚠ **`ALLOWED_SCHEMAS` is applied before `INCLUDED_TABLES`.** The schema filter runs inside the
catalogue queries; the table allowlist filters what comes back. A table whose schema is missing
from `ALLOWED_SCHEMAS` is dropped before the allowlist ever sees it, and reports as *"matched no
table"* — which sends you hunting for a typo in a name that is perfectly correct. Pointing `.env`
at a database that keeps its tables in another schema means setting **both**.

`INCLUDED_TABLES` is an allowlist and the strongest lever here: the schema block goes into every
authoring and verifying prompt, so each unused table is paid for on every call. These four tables
are 4.6k chars; every table in the allowed schemas was ~41k.

`SAMPLE_WELL_ID` is the well `activity_delay` is *verified* against — the API binds whichever well
a reader asks for. Prefer a well that still has late tasks: verified against an empty result, only
the SQL text was checked and never its output, and the run warns when that happens.

**2. Generate or reuse the queries:**

```bash
cd backend
pip install -r requirements.txt
python main.py --out out                 # reuses sql/ when the schema is unchanged
python main.py --frozen                  # freeze state, no LLM calls
python main.py --regenerate --out out    # force the agents to re-author everything
python main.py --detect-only             # just introspect, no LLM calls
python main.py --only activity_delay --well 33785
python main.py --sql                     # print the SQL that ran
python main.py --rework                  # what rework has cost, worst cause first
```

Run these from `backend/`. `--out` is relative to the current folder like any CLI argument, and
the API reads `backend/out/`, so a run started elsewhere writes its brief somewhere the dashboard
never looks.

Exit code is non-zero unless **every** query produced a verified result, so a scheduled run fails
visibly rather than quietly reporting half the picture.

**3. Serve it:**

```bash
cd backend
python -m uvicorn api.main:app --port 8000
```

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:3000. The dashboard is white by default, with a dark theme behind the header
toggle; the choice is remembered and applied before first paint, and it follows the OS setting only
until you pick for yourself.

To watch a run triggered from the dashboard, follow the shared log from its own terminal — the
pipeline runs as a subprocess of the API, so its output appears in neither console:

```bash
Get-Content -Wait -Tail 40 backend\logs\pipeline.log
```

## Per-agent tuning

Set in `backend/app/llm.py`, one dict, keyed by agent:

| Agent | temp | effort | verbosity | Why |
|---|---|---|---|---|
| planner | 0.0 | medium | low | bounded matching; must not guess, need not explore |
| sql_author | 0.0 | medium | low | hardest task; an error costs a full rewrite cycle |
| verifier | 0.0 | medium | low | the gate — its misses are *silent* |
| synthesize | 0.2 | low | medium | derives nothing; the counts are precomputed |
| advisor | 0.2 | high | medium | serve-time suggestion agent; reasons about knock-on causes — drop to medium if the button feels slow |

⚠ `sql_author` and `verifier` were lowered from **high** to cut latency, and measurement says that
is a net loss: at high, `well_slippage` was written correctly on one attempt (82 s to verified); at
medium it was written faster but took three attempts and a verifier rejection (116 s to verified) —
and the rejection was the §2 missing-data guard, the one `milestone_rules` calls the most dangerous
wrong answer this system can produce. Revert by setting those two back to `"high"`, and watch the
rework rate: if a query needs more than one attempt more often than not, medium is costing time.

`OPENAI_REASONING_EFFORT` / `OPENAI_VERBOSITY` override every agent, for measuring what the
tuning buys.

## Where the cost goes

A run that reuses `sql/` spends nothing on authoring — one synthesizer call for the brief, and
about 1.4 s of database time. The figures below are for a run that actually authors.

Measured, ~228k input tokens: **79% is the rule documents** (`milestone_rules.md` at 6.3k tokens ×
every call), 4% is row data. Raising `MAX_ROWS` costs nothing — row previews are capped separately
at 15 (verifier) and 40 (synthesizer) while the Python tallies read every row.

`backend/logs/rework.jsonl` records every execution failure and verifier rejection with its SQL, so the
prompts can be fixed against real causes rather than guesses; `--rework` summarises it.

## Authority

`backend/domain/business_rules 1.md` and `backend/domain/milestone_rules 1.md` are authoritative
and loaded into every pipeline prompt. The suggestion agent carries `business_rules` only:
`milestone_rules` is about writing the SQL, which it never does, and would triple the cost of
every click.
Where a document names something the detected schema lacks, **the schema wins**.

Two values are **assumptions**, not recorded decisions — both isolated in
`backend/app/graph/scenarios.py` with `⚠ ASSUMPTION` markers, because the contract that should specify
them was deleted:

- the milestone **status vocabulary** (one set, as §2 requires)
- the **slippage priority order** (§4 says this is a business decision)

## Caveats

- The dashboard's figures are re-read from the database on every visit (cached 60 s), so they
  are never older than a minute. What ages is the *SQL*: `/api/status` reports when the queries
  were last written and verified, and the per-query freeze state (`current` / `stale` / `hand-edited` / `unverified` / `missing`).
- `schema_drifted` means *a re-run would author new SQL* — a structural change, not a data load.
  It is `null`, never `false`, when the check could not run: unknown is not the same as fine.
- A verified query is verified *as written*, not proven correct for all future data.
- Nothing in the executable path hardcodes a table, column or database name; pointing `.env`
  somewhere else changes the SQL the agents write. `ALLOWED_SCHEMAS` and `INCLUDED_TABLES` are
  the two settings a person must update by hand.
- This reports recorded schedule slippage. It does **not** establish a cause, and a delayed task
  is not evidence that it caused its well's milestone to slip.
