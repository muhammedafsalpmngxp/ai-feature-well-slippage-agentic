"use client";

import { useEffect, useState } from "react";
import type { PipelineStatus } from "@/lib/types";

/**
 * What a re-run is doing, while it does it.
 *
 * A re-run writes and verifies every query again — about a minute and a half — and until this
 * existed the only signal was a disabled button reading "Running…". That is indistinguishable
 * from a hung request, and the figures on the page stayed put with nothing to say they were
 * about to be replaced.
 *
 * ⚠ THE STAGES ARE READ FROM THE PIPELINE'S OWN LOG, not simulated on a timer. The API streams
 * the subprocess's output and keeps the last ~40 lines in its run state; this component matches
 * the same lines a person would read in the terminal. A fake progress bar that advances on a
 * timer is a lie whenever the run stalls, and a stalled run is exactly when someone is watching.
 */

type StageState = "pending" | "active" | "done";

const QUERY_LABEL: Record<string, string> = {
  well_slippage: "Well slippage",
  activity_summary: "Delayed activity count",
  activity_delay: "Activity delay detail",
};

type Stage = {
  key: string;
  label: string;
  detail: string;
  /** Lines that mean this stage has BEGUN, and ones that mean it has finished. */
  starts: RegExp;
  ends?: RegExp;
};

// In pipeline order. `starts` on a later stage implies every earlier one is done, which is what
// keeps this correct when early lines have scrolled out of the rolling window.
const STAGES: Stage[] = [
  {
    key: "detect",
    label: "Reading the database",
    detail: "Schema, types and real lookup values",
    starts: /db: Connected|introspect:/,
    ends: /detect: done/,
  },
  {
    key: "plan",
    label: "Binding columns",
    detail: "Mapping each milestone onto a real column",
    starts: /llm {2}planner|frozen: --regenerate/,
    ends: /plan: \d+\/\d+ milestone/,
  },
  {
    key: "author",
    label: "Writing and verifying SQL",
    detail: "Three queries at once, each independently reviewed",
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

/** `[well_slippage] DONE in 24.7s - 212 rows, verified=True` and its REUSED twin. */
const QUERY_DONE = /\[(\w+)\]\s+(DONE|REUSED) in ([\d.]+)s - (\d+) rows(?:, verified=(\w+))?/;

type QueryOutcome = { key: string; seconds: string; rows: string; verified: boolean };

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

  const states: StageState[] = STAGES.map((stage, i) => {
    if (ended.has(stage.key)) return "done";
    if (i < furthest) return "done";
    if (i === furthest) return "active";
    return "pending";
  });

  const queries: QueryOutcome[] = [];
  for (const line of tail) {
    const m = QUERY_DONE.exec(line);
    if (m && !queries.some((q) => q.key === m[1])) {
      queries.push({ key: m[1], seconds: m[3], rows: m[4], verified: m[5] !== "False" });
    }
  }
  return { states, queries };
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
  const shown = secs < 60 ? `${secs}s` : `${Math.floor(secs / 60)}m ${secs % 60}s`;
  return <span className="tnum">{shown}</span>;
}

function Indicator({ state }: { state: StageState }) {
  if (state === "done") {
    return (
      <span aria-hidden className="grid h-4 w-4 shrink-0 place-items-center rounded-full bg-good">
        <svg width="10" height="10" viewBox="0 0 10 10" fill="none">
          <path
            d="M2 5.2l2 2L8 3"
            stroke="var(--color-surface)"
            strokeWidth="1.6"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </span>
    );
  }
  if (state === "active") {
    return (
      <span aria-hidden className="grid h-4 w-4 shrink-0 place-items-center">
        <span className="dot-active h-2.5 w-2.5 rounded-full bg-accent" />
      </span>
    );
  }
  return (
    <span
      aria-hidden
      className="h-4 w-4 shrink-0 rounded-full border border-line-strong"
    />
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
   * Without it the panel took ~13 seconds to appear, because startRun asks for the schema check
   * before anything re-renders — so the one moment the user most needs feedback was the one
   * moment there was none. The panel is what the button promises; it should not wait on an
   * unrelated request.
   */
  starting?: boolean;
  /** Click time, so the timer runs from the click rather than from the API's first report. */
  startedLocally?: string | null;
}) {
  const run = status?.run;
  const running = run?.running ?? false;
  // Same stale-status trap as the tail below: until the run is confirmed, `run.started_at` is
  // the PREVIOUS run's, which rendered a fresh click as "2m 12s elapsed". The click time is the
  // only true answer in that window.
  const startedAt = running ? (run?.started_at ?? startedLocally) : startedLocally;
  const tail = run?.tail;

  /**
   * Query outcomes ACCUMULATE, because the log tail is a rolling 40-line window.
   *
   * Observed live: "Well slippage · 212 rows · 23.8s" appeared, then disappeared a poll later
   * when its line scrolled out from under the other two queries' output. A finished query
   * un-finishing itself reads as a fault in the run, when it is only a fault in the window.
   * Reset when a new run starts, keyed on started_at.
   */
  const [seen, setSeen] = useState<QueryOutcome[]>([]);
  const [seenFor, setSeenFor] = useState<string | null>(null);

  useEffect(() => {
    if (!running) return;
    if (seenFor !== startedAt) {
      setSeenFor(startedAt);
      setSeen(readProgress(tail ?? []).queries);
      return;
    }
    const fresh = readProgress(tail ?? []).queries;
    setSeen((prev) => {
      const merged = [...prev];
      for (const q of fresh) if (!merged.some((p) => p.key === q.key)) merged.push(q);
      return merged.length === prev.length ? prev : merged;
    });
  }, [running, startedAt, tail, seenFor]);

  if (!running && !starting) return null;

  // ⚠ IGNORE THE TAIL UNTIL THE RUN IS CONFIRMED RUNNING. Between the click and the first poll,
  // `status` still holds the PREVIOUS run — whose tail ends at "brief written", so every stage
  // would render as already complete before the new run had read a single row. Starting from
  // nothing and marking only the first stage active is the honest reading of "we have just asked
  // for this and heard nothing back yet".
  const states = running
    ? readProgress(tail ?? []).states
    : STAGES.map((_, i): StageState => (i === 0 ? "active" : "pending"));
  const queries = running ? seen : [];
  const activeIndex = states.indexOf("active");

  return (
    <section
      role="status"
      aria-live="polite"
      className="overflow-hidden rounded-[10px] border border-accent/25 bg-accent-soft"
    >
      {/* Indeterminate, because the pipeline cannot say what fraction is left: the agents may
          rewrite a query two or three times. A bar claiming 60% would be invented. */}
      <div className="bar-indeterminate relative h-1 w-full overflow-hidden bg-accent/15" />

      <div className="space-y-4 px-5 py-4">
        <div className="flex flex-wrap items-baseline justify-between gap-3">
          <h2 className="display text-sm font-semibold text-ink">Re-running the analysis</h2>
          <p className="text-xs text-ink-2">
            Writing and verifying every query again · <Elapsed startedAt={startedAt} />
          </p>
        </div>

        <ol className="space-y-2.5">
          {STAGES.map((stage, i) => (
            <li key={stage.key} className="flex items-start gap-3">
              <span className="mt-0.5">
                <Indicator state={states[i]} />
              </span>
              <div className="min-w-0">
                <p
                  className={`text-[13px] ${
                    states[i] === "pending"
                      ? "text-ink-3"
                      : states[i] === "active"
                        ? "font-semibold text-ink"
                        : "text-ink-2"
                  }`}
                >
                  {stage.label}
                </p>
                {i === activeIndex && (
                  <p className="mt-0.5 text-xs text-ink-3">{stage.detail}</p>
                )}

                {/* Per-query outcomes, under the stage that produces them. */}
                {stage.key === "author" && queries.length > 0 && (
                  <ul className="mt-1.5 flex flex-wrap gap-1.5">
                    {queries.map((q) => (
                      <li
                        key={q.key}
                        className={`inline-flex h-[22px] items-center rounded-[5px] px-2 text-xs font-medium ${
                          q.verified ? "bg-good-soft text-good" : "bg-warn-soft text-warn"
                        }`}
                        title={
                          q.verified
                            ? "Written, executed and approved by the independent verifier"
                            : "Produced rows, but the verifier did not approve it"
                        }
                      >
                        {QUERY_LABEL[q.key] ?? q.key} · {q.rows} rows · {q.seconds}s
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </li>
          ))}
        </ol>

        <p className="border-t border-accent/15 pt-3 text-xs leading-relaxed text-ink-3">
          The figures below are from the previous run and will update when this finishes. You can
          keep using the page meanwhile.
        </p>
      </div>
    </section>
  );
}
