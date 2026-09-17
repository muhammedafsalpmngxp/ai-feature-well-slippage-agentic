# Well Slippage — PDO / Al Tasnim

Detects contractual **milestone slippage** and task-level **activity delay** across the active
well portfolio, using LangGraph agents that write and verify their own SQL.

The design splits slow, expensive reasoning from fast serving:

```
main.py     agents write + verify SQL, run it, explain it   ~4 min, LLM tokens
   │        writes out/*.sql  out/*.csv  out/brief.md
   ▼
api/        runs that verified SQL on request               ~2 s, no LLM
   ▼
web/        Next.js dashboard
```

The API never calls a model. Once a query is verified it is a deterministic artefact, so serving
a page means running it, not re-deriving it.

## The three queries

| Query | Answers | Grain | Rows |
|---|---|---|---|
| `well_slippage` | which wells failed a contractual milestone | one per well | 215 |
| `activity_summary` | how many activity codes are delayed per well | one per well | 187 |
| `activity_delay` | which tasks are late on **one** well | one per task | ~90 |

Every query is bounded by the *fleet* or by *one well*, never by accumulated task history — which
is why none of them can be silently trimmed by the row cap. `activity_delay` takes the well as a
**bound parameter**, so the API can drill into any well without re-running the agents.

## The pipeline

```
detect ──> planner ──> dispatch ──> sql_author ──> validator ──> executor ──> verifier
                          ^  │           ^             │            │            │
                          │  │           └─────────────┴────────────┴────────────┘
                          │  │                  rewrite, with a specific reason
                          │  └──> synthesize (worklist empty)
                          └─────────────────────────────────── file result, take next query
```

- **detect** — introspects the live schema and samples real lookup values. Cached behind a
  fingerprint covering columns, constraints **and** row counts, so new data invalidates it too.
- **planner** — binds each business term ("the expected rig-on date") to a real column. Separate
  from the author so an unresolvable binding is *reported* rather than guessed.
- **validator** — deterministic SELECT-only gate. Single-pass scrub of strings, comments and
  bracketed identifiers, so a keyword inside data is not mistaken for a command.
- **executor** — deterministic. Binds parameters, never interpolates.
- **verifier** — independent review, **fails closed** on an unparseable verdict. Rejected results
  are set aside, never deleted.
- **synthesize** — writes the brief. Every count is computed in Python first, so the model is
  formatting exact figures rather than counting a sample.

## Running it

**1. Configure `.env`** (git-ignored):

```
DB_SERVER=…
DB_NAME=…
DB_USER=…
DB_PASSWORD=…
DB_TRUST_SERVER_CERTIFICATE=yes
OPENAI_API_KEY=…
OPENAI_MODEL=gpt-5.6-luna

INCLUDED_TABLES=well.well_master,well.task_daily,dbo.mapping_master,dbo.activity_master_csv
MAX_ROWS=2000
VERIFY_RETRIES=2
```

`INCLUDED_TABLES` is an allowlist and the strongest lever here: the schema block goes into every
authoring and verifying prompt, so each unused table is paid for on every call. Four tables is
~4.6k chars; every table in the allowed schemas was ~41k.

**2. Generate the queries:**

```bash
pip install -r requirements.txt
python main.py --out out          # all three queries
python main.py --detect-only      # just introspect, no LLM calls
python main.py --only activity_delay --well 33785
python main.py --sql              # print the generated SQL
```

Exit code is non-zero unless **every** query produced a verified result, so a scheduled run fails
visibly rather than quietly reporting half the picture.

**3. Serve it:**

```bash
python -m uvicorn api.main:app --port 8000
```

```bash
cd web && npm install && npm run dev
```

Open http://localhost:3000.

## Per-agent tuning

Set in `app/llm.py`, one dict, keyed by agent:

| Agent | temp | effort | verbosity | Why |
|---|---|---|---|---|
| planner | 0.0 | medium | low | bounded matching; must not guess, need not explore |
| sql_author | 0.0 | **high** | low | hardest task; an error costs a full rewrite cycle |
| verifier | 0.0 | **high** | low | the gate — its misses are *silent* |
| synthesize | 0.2 | low | medium | derives nothing; the counts are precomputed |

`OPENAI_REASONING_EFFORT` / `OPENAI_VERBOSITY` override every agent, for measuring what the
tuning buys.

## Where the cost goes

Measured, ~228k input tokens for a full run: **79% is the rule documents** (`milestone_rules.md`
at 6.3k tokens × every call), 4% is row data. Raising `MAX_ROWS` costs nothing — row previews are
capped separately at 15 (verifier) and 40 (synthesizer) while the Python tallies read every row.

## Authority

`business_rules 1.md` and `milestone_rules 1.md` are authoritative and loaded into every prompt.
Where a document names something the detected schema lacks, **the schema wins**.

Two values are **assumptions**, not recorded decisions — both isolated in
`app/graph/scenarios.py` with `⚠ ASSUMPTION` markers, because the contract that should specify
them was deleted:

- the milestone **status vocabulary** (one set, as §2 requires)
- the **slippage priority order** (§4 says this is a business decision)

## Caveats

- The dashboard is only as current as the last verified run; `/api/status` reports its age.
- A verified query is verified *as written*, not proven correct for all future data.
- This reports recorded schedule slippage. It does **not** establish a cause, and a delayed task
  is not evidence that it caused its well's milestone to slip.
