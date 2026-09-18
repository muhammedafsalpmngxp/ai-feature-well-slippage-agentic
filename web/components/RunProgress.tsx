"use client";

import { useEffect, useState } from "react";
import type { PipelineStatus } from "@/lib/types";

/**
 * The screen a re-run shows while it runs.
 *
 * A re-run writes and verifies every query again — a minute or two — and replaces the SQL the
 * whole dashboard is built on. So it TAKES OVER: while it runs, the figures beneath it describe
 * a state that is being thrown away, and showing them under a progress bar invites reading them
 * as current.
 *
 * ⚠ THE STAGES ARE READ FROM THE PIPELINE'S OWN LOG, never simulated on a timer. The API streams
 * the subprocess's output and keeps the last ~40 lines in its run state; this matches the same
 * lines a person would read in the terminal. A bar that advances on a timer is most confident
 * exactly when a run has stalled, which is precisely when someone is watching it.
 */

type StepState = "pending" | "active" | "done";

type Stage = {
  key: string;
  label: string;
  detail: string;
  /** Lines meaning this stage has BEGUN, and ones meaning it has finished. */
  starts: RegExp;
  ends?: RegExp;
};

// In pipeline order. A later stage starting implies every earlier one is done, which is what
// keeps this correct once early lines scroll out of the rolling window.
const STAGES: Stage[] = [
  {
    key: "detect",
    label: "Reading the database",
    detail: "Schema, column types and real lookup values",
    starts: /db: Connected|introspect:/,
    ends: /detect: done/,
  },
  {
    key: "plan",
    label: "Binding columns",
    detail: "Mapping every milestone onto a real column of this database",
    starts: /llm {2}planner|frozen: --regenerate/,
    ends: /plan: \d+\/\d+ milestone/,
  },
  {
    key: "author",
    label: "Writing and verifying SQL",
    detail: "Three queries at once, each reviewed by an independent verifier",
    starts: /parallel: starting|llm {2}sql_author/,
    ends: /parallel: all \d+ queries done/,
  },
  {
    key: "freeze",
    label: "Freezing the verified queries",
    detail: "Written to sql/ with the schema they were proved against",
    starts: /frozen: wrote/,
    ends: /llm {2}synthesize|synthesize: brief/,
  },
  {
    key: "brief",
    label: "Writing the brief",
    detail: "Summarising what the numbers show",
    starts: /llm {2}synthesize/,
    ends: /synthesize: brief written/,
  },
];

const QUERIES = [
  { key: "well_slippage", label: "Well slippage" },
  { key: "activity_summary", label: "Delayed activity count" },
  { key: "activity_delay", label: "Activity delay detail" },
] as const;

/** `[well_slippage] DONE in 24.7s - 212 rows, verified=True`, and its REUSED twin. */
const QUERY_DONE = /\[(\w+)\]\s+(?:DONE|REUSED) in ([\d.]+)s - (\d+) rows(?:, verified=(\w+))?/;
/** `verify[activity_summary]: rejected - <reason>` — a rewrite is under way, worth showing. */
const QUERY_REJECTED = /verify\[(\w+)\]: rejected/;

type Outcome = { key: string; seconds: string; rows: string; verified: boolean };

function readProgress(tail: string[]) {
  const text = tail.join("\n");

  let furthest = -1;
  const ended = new Set<string>();
  STAGES.forEach((stage, i) => {
    if (stage.starts.test(text)) furthest = Math.max(furthest, i);
    if (stage.ends?.test(text)) {
      ended.add(stage.key);
      furthest = Math.max(furthest, i);
    }
  });

  const states: StepState[] = STAGES.map((stage, i) => {
    if (ended.has(stage.key)) return "done";
    if (i < furthest) return "done";
    if (i === furthest) return "active";
    return "pending";
  });

  const outcomes: Outcome[] = [];
  const rewriting = new Set<string>();
  for (const line of tail) {
    const done = QUERY_DONE.exec(line);
    if (done && !outcomes.some((o) => o.key === done[1])) {
      outcomes.push({
        key: done[1],
        seconds: done[2],
        rows: done[3],
        verified: done[4] !== "False",
      });
    }
    const rejected = QUERY_REJECTED.exec(line);
    if (rejected) rewriting.add(rejected[1]);
  }
  return { states, outcomes, rewriting };
}

function Elapsed({ startedAt }: { startedAt: string | null }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  if (!startedAt) return null;
  const started = new Date(startedAt).getTime();
  if (Number.isNaN(started)) return null;
  const secs = Math.max(0, Math.round((now - started) / 1000));
  return (
    <span className="tnum">
      {secs < 60 ? `${secs}s` : `${Math.floor(secs / 60)}m ${secs % 60}s`}
    </span>
  );
}

function StepMark({ state }: { state: StepState }) {
  if (state === "done") {
    return (
      <span className="grid h-[22px] w-[22px] place-items-center rounded-full bg-good">
        <svg width="12" height="12" viewBox="0 0 12 12" fill="none" aria-hidden>
          <path
            d="M2.5 6.2l2.4 2.4L9.5 4"
            stroke="var(--color-surface)"
            strokeWidth="1.8"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </span>
    );
  }
  if (state === "active") {
    return (
      <span className="relative grid h-[22px] w-[22px] place-items-center">
        <span className="halo absolute inset-0 rounded-full bg-accent" aria-hidden />
        <span className="ring-spin absolute inset-0 rounded-full" aria-hidden />
        <span className="h-1.5 w-1.5 rounded-full bg-accent" aria-hidden />
      </span>
    );
  }
  return (
    <span
      aria-hidden
      className="grid h-[22px] w-[22px] place-items-center rounded-full border border-line-strong"
    >
      <span className="h-1 w-1 rounded-full bg-line-strong" />
    </span>
  );
}

function QueryLane({
  label,
  state,
  outcome,
  rewriting,
}: {
  label: string;
  state: StepState;
  outcome?: Outcome;
  rewriting: boolean;
}) {
  return (
    <li className="flex items-center gap-2.5 text-xs">
      <span
        aria-hidden
        className={`h-1.5 w-1.5 shrink-0 rounded-full ${
          state === "done"
            ? outcome?.verified === false
              ? "bg-warn"
              : "bg-good"
            : state === "active"
              ? "dot-active bg-accent"
              : "bg-line-strong"
        }`}
      />
      <span className={state === "pending" ? "text-ink-3" : "text-ink-2"}>{label}</span>
      <span className="ml-auto tnum text-ink-3">
        {outcome ? (
          <>
            {Number(outcome.rows).toLocaleString("en-GB")} rows · {outcome.seconds}s
            {outcome.verified === false && (
              <span className="ml-1.5 text-warn" title="The verifier did not approve this query">
                not approved
              </span>
            )}
          </>
        ) : state === "active" ? (
          <span className="text-ink-3">{rewriting ? "rewriting…" : "writing…"}</span>
        ) : (
          ""
        )}
      </span>
    </li>
  );
}

export function RunProgress({
  status,
  starting = false,
  startedLocally = null,
}: {
  status: PipelineStatus | null;
  /**
   * True between the click and the first poll that reports the run.
   *
   * Without it the screen took ~13 seconds to appear, because startRun asks for the schema check
   * first — so the one moment feedback matters most was the one moment there was none.
   */
  starting?: boolean;
  /** Click time, so the timer runs from the click rather than the API's first report. */
  startedLocally?: string | null;
}) {
  const run = status?.run;
  const running = run?.running ?? false;
  // Until the run is CONFIRMED, `status` still describes the previous one — whose started_at
  // rendered a fresh click as "2m 12s elapsed" and whose tail showed every stage already done.
  const startedAt = running ? (run?.started_at ?? startedLocally) : startedLocally;
  const tail = run?.tail;

  /**
   * Outcomes ACCUMULATE, because the tail is a rolling 40-line window.
   *
   * Observed live: a finished query's line scrolled out from under the other two and its result
   * vanished from the screen. A finished query un-finishing itself reads as a fault in the run,
   * when it is only a fault in the window.
   */
  const [seen, setSeen] = useState<Outcome[]>([]);
  const [seenFor, setSeenFor] = useState<string | null>(null);

  useEffect(() => {
    if (!running) return;
    const fresh = readProgress(tail ?? []).outcomes;
    if (seenFor !== startedAt) {
      setSeenFor(startedAt);
      setSeen(fresh);
      return;
    }
    setSeen((prev) => {
      const merged = [...prev];
      for (const o of fresh) if (!merged.some((p) => p.key === o.key)) merged.push(o);
      return merged.length === prev.length ? prev : merged;
    });
  }, [running, startedAt, tail, seenFor]);

  if (!running && !starting) return null;

  const live = running ? readProgress(tail ?? []) : null;
  const states =
    live?.states ?? STAGES.map((_, i): StepState => (i === 0 ? "active" : "pending"));
  const outcomes = running ? seen : [];
  const rewriting = live?.rewriting ?? new Set<string>();
  const activeIndex = states.indexOf("active");
  const authorIndex = STAGES.findIndex((s) => s.key === "author");
  const authorState = states[authorIndex];

  const doneCount = states.filter((s) => s === "done").length;

  return (
    <section
      role="status"
      aria-live="polite"
      aria-busy="true"
      className="flex min-h-[62vh] items-center justify-center py-10"
    >
      <div className="w-full max-w-[520px]">
        {/* Hero */}
        <div className="flex flex-col items-center text-center">
          <span className="relative grid h-14 w-14 place-items-center">
            <span className="halo absolute inset-0 rounded-full bg-accent" aria-hidden />
            <span className="ring-spin absolute inset-0 rounded-full" aria-hidden />
            <svg width="20" height="20" viewBox="0 0 14 14" fill="none" aria-hidden>
              <path
                d="M2 12V6M7 12V2M12 12V9"
                stroke="var(--color-accent)"
                strokeWidth="1.7"
                strokeLinecap="round"
              />
            </svg>
          </span>

          <h2 className="display mt-5 text-lg font-semibold text-ink">
            Re-running the analysis
          </h2>
          <p className="mt-1.5 max-w-[420px] text-sm leading-relaxed text-ink-2">
            The agents are writing and verifying every query from scratch. This usually takes a
            minute or two.
          </p>
          <p className="mt-3 flex items-center gap-2 text-xs text-ink-3">
            <span className="tnum">
              {doneCount} of {STAGES.length} steps
            </span>
            <span aria-hidden>·</span>
            <Elapsed startedAt={startedAt} />
          </p>
        </div>

        {/* Stepper */}
        <div className="mt-8 overflow-hidden rounded-[10px] border border-line bg-surface">
          <div className="bar-indeterminate relative h-0.5 w-full overflow-hidden bg-accent/15" />
          <ol className="space-y-0 px-5 py-2">
            {STAGES.map((stage, i) => {
              const state = states[i];
              const last = i === STAGES.length - 1;
              return (
                <li key={stage.key} className="flex gap-3.5">
                  {/* Rail: mark plus the connector to the next step. */}
                  <div className="flex flex-col items-center">
                    <span className="py-3">
                      <StepMark state={state} />
                    </span>
                    {!last && (
                      <span
                        aria-hidden
                        className={`w-px flex-1 ${state === "done" ? "bg-good/40" : "bg-line"}`}
                      />
                    )}
                  </div>

                  <div className={`min-w-0 flex-1 py-3 ${last ? "" : "pb-4"}`}>
                    <p
                      className={`text-[13px] leading-[22px] ${
                        state === "pending"
                          ? "text-ink-3"
                          : state === "active"
                            ? "font-semibold text-ink"
                            : "text-ink-2"
                      }`}
                    >
                      {stage.label}
                    </p>

                    {i === activeIndex && (
                      <p className="rise mt-1 text-xs leading-relaxed text-ink-3">
                        {stage.detail}
                      </p>
                    )}

                    {/* The three queries, as lanes under the stage that produces them. Shown
                        from the moment that stage begins, so the reader sees what is being
                        worked on rather than only what has finished. */}
                    {stage.key === "author" && authorState !== "pending" && (
                      <ul className="rise mt-2.5 space-y-2 rounded-lg border border-line bg-surface-2 px-3 py-2.5">
                        {QUERIES.map((q) => {
                          const outcome = outcomes.find((o) => o.key === q.key);
                          return (
                            <QueryLane
                              key={q.key}
                              label={q.label}
                              state={
                                outcome ? "done" : authorState === "active" ? "active" : "pending"
                              }
                              outcome={outcome}
                              rewriting={rewriting.has(q.key)}
                            />
                          );
                        })}
                      </ul>
                    )}
                  </div>
                </li>
              );
            })}
          </ol>
        </div>

        <p className="mt-5 text-center text-xs leading-relaxed text-ink-3">
          The dashboard returns as soon as this finishes. Nothing below is hidden for long — the
          figures it showed are about to be replaced by these.
        </p>
      </div>
    </section>
  );
}
