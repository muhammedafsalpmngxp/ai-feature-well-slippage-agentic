"use client";

import { type ReactNode, useCallback, useEffect, useState } from "react";
import { ErrorNote, Pill, Status } from "./ui";
import { api, ApiError } from "@/lib/api";
import { EMPTY, cleanLabel, formatDate, formatNumber, formatVariance } from "@/lib/format";
import type {
  CauseVerdict,
  Suggestion,
  TaskRow,
  WellDelayVerdict,
  WellSuggestion,
} from "@/lib/types";

/**
 * The suggestion agent's answer for ONE late task, opened in place under its row.
 *
 * Two halves, deliberately kept apart on screen:
 *   - the ADVICE, which is what a model concluded. It is labelled as a suggestion and never styled
 *     like a figure, because it is not one;
 *   - the EVIDENCE, the verified rows the agent was handed, so a reader can check the one against
 *     the other rather than take the advice on trust.
 *
 * A crew the agent names that was not among the candidates it was given is marked as unverified,
 * never shown as a recommendation (the API flags it; this only renders the flag).
 */

type Tone = "danger" | "warn" | "good" | "neutral" | "accent";

const VERDICT: Record<CauseVerdict, { label: string; tone: Tone }> = {
  yes: { label: "Yes — knock-on from another delay", tone: "danger" },
  possibly: { label: "Possibly — timing points upstream", tone: "warn" },
  no: { label: "No — nothing upstream in the data", tone: "neutral" },
  unknown: { label: "Unknown from the data", tone: "neutral" },
};

const BUTTON =
  "h-8 rounded-md border border-line-strong px-3 text-xs font-semibold text-ink-2 transition-colors hover:bg-surface disabled:opacity-40";

export function SuggestionPanel({
  wellId,
  task,
  onClose,
}: {
  wellId: string;
  task: TaskRow;
  onClose: () => void;
}) {
  const [data, setData] = useState<Suggestion | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const ask = useCallback(
    async (fresh: boolean) => {
      setLoading(true);
      setError(null);
      try {
        setData(await api.suggest(wellId, task.task_code, fresh));
      } catch (err) {
        setError(
          err instanceof ApiError ? err.message : "The suggestion agent could not be reached.",
        );
      } finally {
        setLoading(false);
      }
    },
    [wellId, task.task_code],
  );

  useEffect(() => {
    void ask(false);
  }, [ask]);

  return (
    <div className="rise space-y-4 px-5 py-4 whitespace-normal" aria-live="polite">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="space-y-0.5">
          <p className="eyebrow">Recovery suggestion · {cleanLabel(task.wbs)}</p>
          <p className="text-xs text-ink-3">
            AI-generated from the verified evidence shown beside it. Review before acting.
            {data && !loading && (
              <>
                {" "}
                · {data.cached ? "cached" : "generated"} {formatDate(data.generated_at)}
              </>
            )}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={() => void ask(true)} disabled={loading} className={BUTTON}>
            Ask again
          </button>
          <button onClick={onClose} className={BUTTON}>
            Close
          </button>
        </div>
      </div>

      {loading ? (
        <Thinking message="The agent is weighing this task against the well’s other late work, its milestones, and every crew of the same type. This can take up to a minute." />
      ) : error ? (
        <ErrorNote message={error} onRetry={() => void ask(false)} />
      ) : data ? (
        <div className="grid gap-5 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
          <div className="min-w-0 space-y-4">
            {data.advice ? <Advice advice={data.advice} /> : <RawAnswer text={data.raw} />}
          </div>
          <Evidence evidence={data.evidence} />
        </div>
      ) : null}
    </div>
  );
}

function Thinking({ message }: { message: string }) {
  return (
    <div className="space-y-2" aria-busy="true">
      <p className="text-xs leading-relaxed text-ink-3">{message}</p>
      {[92, 78, 64].map((width) => (
        <div key={width} className="skeleton h-4 rounded" style={{ width: `${width}%` }} />
      ))}
    </div>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="space-y-2">
      <p className="text-xs font-semibold text-ink">{title}</p>
      {children}
    </div>
  );
}

function Advice({ advice }: { advice: NonNullable<Suggestion["advice"]> }) {
  const verdict = VERDICT[advice.caused_by_other_delay] ?? VERDICT.unknown;
  return (
    <>
      {advice.summary && <p className="text-sm leading-relaxed text-ink">{advice.summary}</p>}

      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs text-ink-3">Caused by another delay?</span>
        <Pill value={advice.caused_by_other_delay} tone={verdict.tone} label={verdict.label} />
      </div>

      {advice.causes.length > 0 && (
        <Section title="Why it is late">
          <ul className="space-y-2.5">
            {advice.causes.map((c, i) => (
              <li key={i} className="space-y-0.5">
                <p className="text-[13px] leading-relaxed text-ink">
                  {c.cause}
                  {c.confidence && (
                    <span className="ml-2 text-[11px] font-semibold uppercase text-ink-3">
                      {c.confidence} confidence
                    </span>
                  )}
                </p>
                {c.evidence && (
                  <p className="text-xs leading-relaxed text-ink-3">Evidence: {c.evidence}</p>
                )}
              </li>
            ))}
          </ul>
        </Section>
      )}

      {advice.actions.length > 0 && (
        <Section title="How to recover">
          <ol className="space-y-2.5">
            {advice.actions.map((a, i) => (
              <li key={i} className="flex gap-2.5">
                <span className="tnum mt-px h-5 w-5 shrink-0 rounded-full bg-accent-soft text-center text-[11px] leading-5 font-semibold text-accent">
                  {i + 1}
                </span>
                <div className="min-w-0 space-y-0.5">
                  <p className="text-[13px] leading-relaxed text-ink">
                    {a.action}
                    {a.crew_id !== null && a.crew_id !== undefined && (
                      <span className="ml-2 rounded bg-surface px-1.5 py-0.5 font-mono text-[11px] text-ink-2">
                        crew {a.crew_id}
                      </span>
                    )}
                  </p>
                  {a.crew_unverified && (
                    <p className="text-xs font-semibold text-danger">
                      This crew was not in the candidate list the agent was given. Check before
                      acting.
                    </p>
                  )}
                  {a.rationale && (
                    <p className="text-xs leading-relaxed text-ink-3">{a.rationale}</p>
                  )}
                </div>
              </li>
            ))}
          </ol>
        </Section>
      )}

      {advice.checks.length > 0 && (
        <Section title="To verify on site">
          <ul className="list-disc space-y-1 pl-4 text-xs leading-relaxed text-ink-2">
            {advice.checks.map((c, i) => (
              <li key={i}>{c}</li>
            ))}
          </ul>
        </Section>
      )}

      {advice.caveats.length > 0 && (
        <Section title="Limits of this answer">
          <ul className="list-disc space-y-1 pl-4 text-xs leading-relaxed text-ink-3">
            {advice.caveats.map((c, i) => (
              <li key={i}>{c}</li>
            ))}
          </ul>
        </Section>
      )}
    </>
  );
}

/** The agent's reply when it was not the JSON asked for: shown as said, never dropped. */
function RawAnswer({ text }: { text: string | null }) {
  if (!text) return <p className="text-sm text-ink-3">The agent returned no answer. Ask again.</p>;
  return (
    <div className="space-y-2">
      <p className="text-xs text-warn">
        The agent&rsquo;s reply was not in the expected structure, so it is shown as written.
      </p>
      <p className="text-[13px] leading-relaxed whitespace-pre-wrap text-ink">{text}</p>
    </div>
  );
}

function Evidence({ evidence }: { evidence: Suggestion["evidence"] }) {
  const { crew, well } = evidence;
  const upstream = evidence.earlier_late_tasks;
  const failed = well.milestones.filter((m) =>
    ["MISSED", "DELAYED"].includes(String(m.status ?? "").toUpperCase()),
  );

  return (
    <aside className="min-w-0 space-y-4 rounded-lg border border-line bg-surface p-4">
      <p className="eyebrow">Evidence the agent was given</p>

      <Section title={`Crews of type ${crew.crew_type_id ?? EMPTY}`}>
        {crew.note ? (
          <p className="text-xs leading-relaxed text-warn">{crew.note}</p>
        ) : (
          <p className="tnum text-xs text-ink-2">
            {crew.available_count} available · {crew.busy_count} busy · {crew.type_total} of this
            type
          </p>
        )}
        {crew.assigned && (
          <p className="tnum text-xs text-ink-3">
            Assigned crew {crew.assigned_crew_id}: {formatNumber(crew.assigned.open_tasks)} open,{" "}
            {formatNumber(crew.assigned.overdue_tasks)} overdue, on{" "}
            {formatNumber(crew.assigned.wells_with_open_tasks)} well(s)
          </p>
        )}
        {crew.available.length > 0 && (
          <table className="tnum w-full text-xs">
            <thead>
              <tr className="text-left text-ink-3">
                <th className="py-1 font-semibold">Crew</th>
                <th className="py-1 text-right font-semibold">Open</th>
                <th className="py-1 text-right font-semibold">Overdue</th>
                <th className="py-1 text-right font-semibold">Last recorded</th>
              </tr>
            </thead>
            <tbody>
              {crew.available.map((c) => (
                <tr key={String(c.crew_id)} className="border-t border-line text-ink-2">
                  <td className="py-1 font-mono">{c.crew_id}</td>
                  <td className="py-1 text-right">{formatNumber(c.open_tasks)}</td>
                  <td className="py-1 text-right">{formatNumber(c.overdue_tasks)}</td>
                  <td className="py-1 text-right">{formatDate(c.latest_action_on)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <p className="text-[11px] leading-relaxed text-ink-3">
          Available means no task in progress is recorded for the crew. It does not show leave,
          location or whether the crew is on site.
        </p>
      </Section>

      <Section title="Late work due to finish before this task started">
        {upstream.length === 0 ? (
          <p className="text-xs text-ink-3">None on this well.</p>
        ) : (
          <ul className="space-y-1.5">
            {upstream.slice(0, 5).map((t, i) => (
              <li key={`${t.task_code}-${i}`} className="text-xs leading-snug">
                <span className="text-ink-2">{cleanLabel(t.wbs)}</span>
                <span className="block text-ink-3">
                  due {formatDate(t.target_end)} · <Status value={t.end_status} />{" "}
                  {formatVariance(t.end_variance_days)}
                </span>
              </li>
            ))}
            {upstream.length > 5 && (
              <li className="text-xs text-ink-3">and {upstream.length - 5} more</li>
            )}
          </ul>
        )}
      </Section>

      <Section title="Well milestones">
        {well.listed === true ? (
          failed.length === 0 ? (
            <p className="text-xs text-ink-3">No milestone failed.</p>
          ) : (
            <ul className="space-y-1">
              {failed.map((m) => (
                <li key={m.milestone} className="tnum text-xs text-ink-2">
                  {m.milestone} ({m.owner}): <Status value={m.status} />{" "}
                  {formatVariance(m.variance_days)}
                </li>
              ))}
            </ul>
          )
        ) : well.listed === false ? (
          <p className="text-xs text-ink-3">No contractual milestone has failed on this well.</p>
        ) : (
          <p className="text-xs text-warn">{well.note}</p>
        )}
      </Section>
    </aside>
  );
}


/* ── Well-level suggestion ─────────────────────────────────────────────────── */

const WELL_VERDICT: Record<WellDelayVerdict, { label: string; tone: Tone }> = {
  yes: { label: "Delayed", tone: "danger" },
  at_risk: { label: "At risk — late work, no milestone failed yet", tone: "warn" },
  no: { label: "Not delayed", tone: "good" },
  unknown: { label: "Unknown from the data", tone: "neutral" },
};

/**
 * Why is this WELL delayed, and how could it be overcome - opened from the button above the task
 * list. The same split as the task panel: the agent's description and actions on one side, the
 * verified evidence it was given on the other.
 */
export function WellSuggestionPanel({ wellId, onClose }: { wellId: string; onClose: () => void }) {
  const [data, setData] = useState<WellSuggestion | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const ask = useCallback(
    async (fresh: boolean) => {
      setLoading(true);
      setError(null);
      try {
        setData(await api.suggestWell(wellId, fresh));
      } catch (err) {
        setError(
          err instanceof ApiError ? err.message : "The suggestion agent could not be reached.",
        );
      } finally {
        setLoading(false);
      }
    },
    [wellId],
  );

  useEffect(() => {
    void ask(false);
  }, [ask]);

  return (
    <div className="rise space-y-4 px-5 py-4 whitespace-normal" aria-live="polite">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="space-y-0.5">
          <p className="eyebrow">Why well {wellId} is delayed · how to recover</p>
          <p className="text-xs text-ink-3">
            AI-generated from the verified evidence shown beside it. Review before acting.
            {data && !loading && (
              <>
                {" "}
                · {data.cached ? "cached" : "generated"} {formatDate(data.generated_at)}
              </>
            )}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={() => void ask(true)} disabled={loading} className={BUTTON}>
            Ask again
          </button>
          <button onClick={onClose} className={BUTTON}>
            Close
          </button>
        </div>
      </div>

      {loading ? (
        <Thinking message="The agent is reading this well’s milestones, every late task, and the crews free in each crew type. This can take up to a minute." />
      ) : error ? (
        <ErrorNote message={error} onRetry={() => void ask(false)} />
      ) : data ? (
        <div className="grid gap-5 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
          <div className="min-w-0 space-y-4">
            {data.advice ? <WellAdviceView advice={data.advice} /> : <RawAnswer text={data.raw} />}
          </div>
          <WellEvidence evidence={data.evidence} />
        </div>
      ) : null}
    </div>
  );
}

function WellAdviceView({ advice }: { advice: NonNullable<WellSuggestion["advice"]> }) {
  const verdict = WELL_VERDICT[advice.delayed] ?? WELL_VERDICT.unknown;
  return (
    <>
      <div className="flex flex-wrap items-center gap-2">
        <Pill value={advice.delayed} tone={verdict.tone} label={verdict.label} />
      </div>

      {advice.description && (
        <p className="text-sm leading-relaxed text-ink">{advice.description}</p>
      )}

      {advice.why_delayed.length > 0 && (
        <Section title="Why the well is delayed">
          <ul className="space-y-2.5">
            {advice.why_delayed.map((r, i) => (
              <li key={i} className="space-y-0.5">
                <p className="text-[13px] leading-relaxed text-ink">
                  {r.reason}
                  {r.owner && r.owner !== "unknown" && (
                    <span className="ml-2 rounded bg-surface px-1.5 py-0.5 text-[11px] font-semibold text-ink-2">
                      {r.owner}
                    </span>
                  )}
                  {r.confidence && (
                    <span className="ml-2 text-[11px] font-semibold uppercase text-ink-3">
                      {r.confidence} confidence
                    </span>
                  )}
                </p>
                {r.evidence && (
                  <p className="text-xs leading-relaxed text-ink-3">Evidence: {r.evidence}</p>
                )}
              </li>
            ))}
          </ul>
        </Section>
      )}

      {advice.actions.length > 0 && (
        <Section title="How to overcome it">
          <ol className="space-y-2.5">
            {[...advice.actions]
              .sort((a, b) => a.priority - b.priority)
              .map((a, i) => (
                <li key={i} className="flex gap-2.5">
                  <span className="tnum mt-px h-5 w-5 shrink-0 rounded-full bg-accent-soft text-center text-[11px] leading-5 font-semibold text-accent">
                    {i + 1}
                  </span>
                  <div className="min-w-0 space-y-0.5">
                    <p className="text-[13px] leading-relaxed text-ink">
                      {a.action}
                      {a.crew_type_id !== null && a.crew_type_id !== undefined && (
                        <span className="ml-2 rounded bg-surface px-1.5 py-0.5 font-mono text-[11px] text-ink-2">
                          type {a.crew_type_id}
                        </span>
                      )}
                      {a.crew_id !== null && a.crew_id !== undefined && (
                        <span className="ml-1.5 rounded bg-surface px-1.5 py-0.5 font-mono text-[11px] text-ink-2">
                          crew {a.crew_id}
                        </span>
                      )}
                    </p>
                    {(a.crew_unverified || a.type_unverified) && (
                      <p className="text-xs font-semibold text-danger">
                        {a.crew_unverified
                          ? "This crew was not among the crews the agent was given."
                          : "This crew type carries none of this well’s late work."}{" "}
                        Check before acting.
                      </p>
                    )}
                    {a.rationale && (
                      <p className="text-xs leading-relaxed text-ink-3">{a.rationale}</p>
                    )}
                  </div>
                </li>
              ))}
          </ol>
        </Section>
      )}

      {advice.checks.length > 0 && (
        <Section title="To verify on site">
          <ul className="list-disc space-y-1 pl-4 text-xs leading-relaxed text-ink-2">
            {advice.checks.map((c, i) => (
              <li key={i}>{c}</li>
            ))}
          </ul>
        </Section>
      )}

      {advice.caveats.length > 0 && (
        <Section title="Limits of this answer">
          <ul className="list-disc space-y-1 pl-4 text-xs leading-relaxed text-ink-3">
            {advice.caveats.map((c, i) => (
              <li key={i}>{c}</li>
            ))}
          </ul>
        </Section>
      )}
    </>
  );
}

function WellEvidence({ evidence }: { evidence: WellSuggestion["evidence"] }) {
  const { well, tasks } = evidence;
  const failed = well.milestones.filter((m) =>
    ["MISSED", "DELAYED"].includes(String(m.status ?? "").toUpperCase()),
  );

  return (
    <aside className="min-w-0 space-y-4 rounded-lg border border-line bg-surface p-4">
      <p className="eyebrow">Evidence the agent was given</p>

      <Section title="Contractual milestones">
        {well.listed === true ? (
          failed.length === 0 ? (
            <p className="text-xs text-ink-3">No milestone failed.</p>
          ) : (
            <ul className="space-y-1">
              {failed.map((m) => (
                <li key={m.milestone} className="tnum text-xs text-ink-2">
                  {m.milestone} ({m.owner}): <Status value={m.status} />{" "}
                  {formatVariance(m.variance_days)}
                </li>
              ))}
            </ul>
          )
        ) : well.listed === false ? (
          <p className="text-xs text-ink-3">No contractual milestone has failed on this well.</p>
        ) : (
          <p className="text-xs text-warn">{well.note}</p>
        )}
      </Section>

      <Section title="Tasks">
        <p className="tnum text-xs text-ink-2">
          {tasks.late} late of {tasks.total} · {tasks.red} red · {tasks.amber} amber ·{" "}
          {tasks.not_started_late} not started, late
        </p>
      </Section>

      <Section title="Late work by crew type">
        {evidence.crew_note && (
          <p className="text-xs leading-relaxed text-warn">{evidence.crew_note}</p>
        )}
        {evidence.late_by_crew_type.length === 0 ? (
          <p className="text-xs text-ink-3">No late work.</p>
        ) : (
          <table className="tnum w-full text-xs">
            <thead>
              <tr className="text-left text-ink-3">
                <th className="py-1 font-semibold">Crew type</th>
                <th className="py-1 text-right font-semibold">Late tasks</th>
                <th className="py-1 text-right font-semibold">Free crews</th>
              </tr>
            </thead>
            <tbody>
              {evidence.late_by_crew_type.map((g) => (
                <tr key={String(g.crew_type_id)} className="border-t border-line text-ink-2">
                  <td className="py-1 font-mono">{g.crew_type_id ?? "not recorded"}</td>
                  <td className="py-1 text-right">{formatNumber(g.late_tasks)}</td>
                  <td className="py-1 text-right">
                    {g.crew_type_id === null ? EMPTY : `${g.available_count} of ${g.type_total}`}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <p className="text-[11px] leading-relaxed text-ink-3">
          Free means no task in progress is recorded for the crew. It does not show leave, location
          or whether the crew is on site.
        </p>
      </Section>

      {evidence.late_by_wbs.length > 0 && (
        <Section title="Late work by WBS">
          <ul className="space-y-1">
            {evidence.late_by_wbs.slice(0, 5).map((g) => (
              <li key={g.wbs} className="tnum text-xs text-ink-2">
                {cleanLabel(g.wbs)}: {g.late_tasks} late
                {g.worst_overrun_days !== null && ` · worst ${formatVariance(g.worst_overrun_days)}`}
              </li>
            ))}
          </ul>
        </Section>
      )}
    </aside>
  );
}
