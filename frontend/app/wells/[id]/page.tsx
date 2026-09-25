"use client";

import Link from "next/link";
import { Fragment, use, useCallback, useEffect, useMemo, useState } from "react";
import {
  Card,
  Empty,
  ErrorNote,
  Pill,
  Skeleton,
  Stat,
  StatStrip,
  Status,
  TableShell,
  Td,
  Th,
} from "@/components/ui";
import { SuggestionPanel, WellSuggestionPanel } from "@/components/SuggestionPanel";
import { WellLookup } from "@/components/WellLookup";
import { api, ApiError } from "@/lib/api";
import { EMPTY, cleanLabel, formatDate, formatPercent, formatVariance, varianceTone } from "@/lib/format";
import {
  MILESTONES,
  MILESTONE_LABEL,
  milestoneLabelOf,
  type TaskRow,
  type WellRow,
} from "@/lib/types";

/** Late = the end is missed (RED) or the start slipped (either AMBER). Only these get a Suggest button. */
function isLate(task: TaskRow): boolean {
  return task.schedule_risk === "RED" || (task.schedule_risk ?? "").startsWith("AMBER");
}

const SUGGEST_BUTTON =
  "h-7 rounded-md border border-line-strong px-2.5 text-xs font-semibold text-accent transition-colors hover:bg-accent-soft";

export default function WellDetail({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const wellId = decodeURIComponent(id);

  const [well, setWell] = useState<WellRow | null | undefined>(undefined);
  const [tasks, setTasks] = useState<TaskRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [riskFilter, setRiskFilter] = useState("all");
  const [tasksFailed, setTasksFailed] = useState(false);
  // The task whose suggestion panel is open, by task code (one per well after the history
  // reduction). One at a time: each open panel is a model call, and two answers side by side in a
  // dense table are harder to read than one.
  const [suggestFor, setSuggestFor] = useState<string | null>(null);
  // The well-level answer - why this well is delayed and how to overcome it - above the list.
  const [wellAdviceOpen, setWellAdviceOpen] = useState(false);

  const load = useCallback(async () => {
    setError(null);
    setTasksFailed(false);
    const [w, t] = await Promise.allSettled([api.wells(), api.wellActivity(wellId)]);
    setWell(
      w.status === "fulfilled"
        ? (w.value.find((row) => String(row.well_id) === wellId) ?? null)
        : null,
    );
    if (t.status === "fulfilled") setTasks(t.value);
    else {
      // Deliberately NOT []. An empty array renders every count as 0, which asserts "this well
      // has no delayed activity" when the truth is that nothing could be measured - the exact
      // null-is-not-zero mistake milestone_rules §3 exists to prevent. Null keeps the counts
      // showing an em dash, and `tasksFailed` is what tells the table to show the error instead
      // of a skeleton that never resolves.
      setTasks(null);
      setTasksFailed(true);
      setError(
        t.reason instanceof ApiError ? t.reason.message : "Could not load this well's tasks.",
      );
    }
  }, [wellId]);

  useEffect(() => {
    void load();
  }, [load]);

  const counts = useMemo(() => {
    const rows = tasks ?? [];
    const red = rows.filter((t) => t.schedule_risk === "RED").length;
    const amber = rows.filter((t) => t.schedule_risk?.startsWith("AMBER")).length;
    const notStarted = rows.filter((t) => t.execution_status === "NOT_STARTED_LATE").length;
    const worst = rows.reduce<number | null>((max, t) => {
      const v = t.end_variance_days;
      if (v === null || v === undefined) return max;
      return max === null || Number(v) > max ? Number(v) : max;
    }, null);
    // Distinct activity CODES, not task rows: one well runs the same activity many times, so
    // counting rows would report one activity repeated forty times as forty.
    //
    // An unmapped task is EXCLUDED from the count and reported separately. Folding it in under
    // a "(unmapped)" placeholder adds a phantom +1 - business_rules §3 warns about exactly
    // this, and it made this page disagree with the summary query by one for well 35543.
    const late = rows.filter((t) => t.schedule_risk !== "GREEN");
    const activities = new Set(late.map((t) => t.activity_code).filter(Boolean)).size;
    const unmapped = late.filter((t) => !t.activity_code).length;
    const inProgress = rows.filter((t) => t.execution_status === "IN_PROGRESS").length;
    const completed = rows.filter((t) => t.execution_status === "COMPLETED").length;
    return {
      total: rows.length, red, amber, notStarted, worst, activities, unmapped,
      inProgress, completed,
    };
  }, [tasks]);

  /**
   * Filter on schedule risk OR execution status, prefixed so one control can do both.
   *
   * Execution status is here because risk alone could not reach it: a task that never started
   * is RED once its END is also missed, which puts it in the same bucket as a task that started
   * and ran late. The "Not started, late" figure above counted them, and nothing in the table
   * could isolate them.
   */
  const visible = useMemo(() => {
    const rows = tasks ?? [];
    if (riskFilter === "all") return rows;
    if (riskFilter === "risk:AMBER") return rows.filter((t) => t.schedule_risk?.startsWith("AMBER"));
    if (riskFilter.startsWith("risk:")) {
      return rows.filter((t) => t.schedule_risk === riskFilter.slice(5));
    }
    if (riskFilter.startsWith("exec:")) {
      return rows.filter((t) => t.execution_status === riskFilter.slice(5));
    }
    return rows;
  }, [tasks, riskFilter]);

  const headline = String(well?.well_slippage_status ?? "");

  return (
    <div className="space-y-6">
      <div className="space-y-3">
        <Link
          href="/"
          className="inline-flex items-center gap-1.5 text-xs font-medium text-accent hover:text-accent-strong"
        >
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden>
            <path
              d="M11.5 7H3M6.5 3.5L3 7l3.5 3.5"
              stroke="currentColor"
              strokeWidth="1.5"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
          All wells
        </Link>

        <div className="flex flex-wrap items-end justify-between gap-4">
          <div className="space-y-2">
            <div className="flex flex-wrap items-center gap-3">
              <h1 className="display tnum text-[26px] leading-tight font-semibold">
                Well {wellId}
              </h1>
              {headline && <Pill value={headline} label={milestoneLabelOf(headline)} />}
            </div>
            <p className="text-sm text-ink-2">
              Contractual milestones and task-level activity delay.
            </p>
          </div>
          <WellLookup defaultValue={wellId} />
        </div>
      </div>

      {error && <ErrorNote message={error} onRetry={() => void load()} />}

      <StatStrip>
        <Stat
          label="Delayed activities"
          value={tasks === null ? "—" : counts.activities}
          hint={
            tasks === null
              ? "Distinct activity codes"
              : counts.unmapped > 0
                ? `Distinct activity codes · ${counts.unmapped} late task(s) unmapped, excluded`
                : "Distinct activity codes"
          }
        />
        {/* Tone is conditional, not fixed: a red "0" reads as an alarm for the one outcome that
            is unambiguously good news, and a red "0d" says a task that landed exactly on its
            deadline overran. A count only earns its colour once there is something to count. */}
        <Stat label="Red tasks" value={tasks === null ? "—" : counts.red}
              tone={counts.red > 0 ? "danger" : "neutral"}
              hint="End date already missed" />
        <Stat label="Not started, late" value={tasks === null ? "—" : counts.notStarted}
              tone={counts.notStarted > 0 ? "warn" : "neutral"}
              hint="Planned start has passed" />
        <Stat label="Worst overrun" value={tasks === null ? "—" : formatVariance(counts.worst)}
              tone={varianceTone(counts.worst) === "danger" ? "danger" : "neutral"}
              hint="Days past planned end" />
      </StatStrip>

      <Card title="Contractual milestones" subtitle="Deadlines are derived, never stored">
        {well === undefined ? (
          <Skeleton rows={3} />
        ) : well === null ? (
          <Empty message="This well is not in the current slippage listing." />
        ) : (
          <TableShell>
            <thead>
              <tr>
                <Th className="w-52">Milestone</Th>
                <Th className="w-40">Deadline</Th>
                <Th className="w-40">Actual</Th>
                <Th>Status</Th>
                <Th align="right" className="w-32">Variance</Th>
              </tr>
            </thead>
            <tbody>
              {MILESTONES.map((m) => {
                const variance = well[`${m}_variance_days`] as number | null;
                return (
                  <tr key={m} className="transition-colors hover:bg-surface-2">
                    <Td className="font-semibold">{MILESTONE_LABEL[m]}</Td>
                    <Td className="text-ink-2">{formatDate(well[`${m}_deadline`] as string)}</Td>
                    {/* What actually happened, beside what was promised. An em dash here means
                        the milestone is not complete — which is exactly what MISSED and PENDING
                        say in the next column, so the two never contradict each other. */}
                    <Td className="text-ink-2">
                      {formatDate(well[`${m}_actual`] as string)}
                    </Td>
                    <Td>
                      <Status value={well[`${m}_status`] as string} />
                    </Td>
                    <Td align="right">
                      <span
                        className={
                          varianceTone(variance) === "danger"
                            ? "font-semibold text-danger"
                            : varianceTone(variance) === "good"
                              ? "text-good"
                              : "text-ink-3"
                        }
                      >
                        {formatVariance(variance)}
                      </span>
                    </Td>
                  </tr>
                );
              })}
            </tbody>
          </TableShell>
        )}
      </Card>

      <Card
        title="Activity delay"
        subtitle="Latest record per task · most urgent first"
        action={
          <div className="flex items-center gap-2.5">
            <button
              onClick={() => setWellAdviceOpen((open) => !open)}
              disabled={tasks === null}
              aria-expanded={wellAdviceOpen}
              title="Ask the agent why this well is delayed and how to overcome it, using the crews that are free"
              className="h-9 rounded-lg bg-accent px-3.5 text-xs font-semibold text-on-accent transition-colors hover:bg-accent-strong disabled:opacity-50"
            >
              {wellAdviceOpen ? "Hide suggestion" : "Suggest recovery"}
            </button>
            <span className="tnum text-xs text-ink-3">
              {tasks === null ? "" : `${visible.length} of ${counts.total} tasks`}
            </span>
            <label className="sr-only" htmlFor="risk-filter">
              Filter by schedule risk
            </label>
            <select
              id="risk-filter"
              value={riskFilter}
              onChange={(e) => setRiskFilter(e.target.value)}
              className="h-9 rounded-lg border border-line-strong bg-surface px-2.5 text-[13px] text-ink outline-none"
            >
              <option value="all">All tasks ({counts.total})</option>
              <optgroup label="Schedule risk">
                <option value="risk:RED">Red ({counts.red})</option>
                <option value="risk:AMBER">Amber ({counts.amber})</option>
                <option value="risk:GREEN">Green</option>
              </optgroup>
              <optgroup label="Execution">
                <option value="exec:NOT_STARTED_LATE">
                  Not started, late ({counts.notStarted})
                </option>
                <option value="exec:IN_PROGRESS">In progress ({counts.inProgress})</option>
                <option value="exec:COMPLETED">Completed ({counts.completed})</option>
              </optgroup>
            </select>
          </div>
        }
      >
        {wellAdviceOpen && (
          <div className="border-b border-line bg-surface-2">
            <WellSuggestionPanel wellId={wellId} onClose={() => setWellAdviceOpen(false)} />
          </div>
        )}
        {tasksFailed ? (
          <Empty message="This well's tasks could not be loaded, so no task figures are shown." />
        ) : tasks === null ? (
          <Skeleton rows={8} />
        ) : tasks.length === 0 ? (
          // Zero tasks is NOT "this well is clean". The queries cover only wells still in
          // progress (milestone_rules §4), so a COMPLETED well returns nothing at all - and
          // saying "no late tasks" about it would assert something never measured.
          <Empty
            message={
              "No task rows were returned for well " +
              wellId +
              ". The analysis covers only wells still in progress, so a completed well returns " +
              "nothing — this is not a finding that the well has no late work."
            }
          />
        ) : visible.length === 0 ? (
          <Empty message="No tasks match this filter." />
        ) : (
          <TableShell>
            <caption className="sr-only">
              Tasks for well {wellId}, one row per task, ordered by schedule risk.
            </caption>
            <thead>
              <tr>
                <Th>WBS</Th>
                <Th>Activity</Th>
                {/* The ids recorded on the task itself. Crew TYPE decides which crews can take a
                    task over; the activity's crew-type code (e.g. LCC-0803) is on hover. */}
                <Th title="The crew type recorded on the task">Crew type</Th>
                <Th title="The specific crew recorded on the task">Crew</Th>
                <Th>Risk</Th>
                {/* The start side was missing entirely. Without it a task that never started
                    and one that started and ran late are the same row — both Red, both Overdue —
                    which is why the "Not started, late" count above could not be found. */}
                <Th>Start status</Th>
                <Th>End status</Th>
                <Th align="right">Overrun</Th>
                <Th>Planned end</Th>
                <Th>Actual end</Th>
                <Th align="right">Progress</Th>
                <Th align="right">
                  <span className="sr-only">Recovery suggestion</span>
                </Th>
              </tr>
            </thead>
            <tbody>
              {visible.map((task, i) => {
                const late = isLate(task);
                const open = suggestFor === task.task_code;
                return (
                  <Fragment key={`${task.task_code}-${i}`}>
                    <tr
                      className={`transition-colors hover:bg-surface-2 ${open ? "bg-surface-2" : ""}`}
                    >
                      <Td className="max-w-[280px]">
                        <span className="block truncate" title={cleanLabel(task.wbs)}>
                          {task.wbs ? cleanLabel(task.wbs) : (
                            <span className="text-ink-3 italic">Unmapped</span>
                          )}
                        </span>
                      </Td>
                      <Td>
                        <span className="font-mono text-xs text-ink-2">
                          {task.activity_code ?? EMPTY}
                        </span>
                      </Td>
                      <Td
                        title={
                          task.crew_code ? `Activity crew-type code ${task.crew_code}` : undefined
                        }
                      >
                        <span className="font-mono text-xs text-ink-2">
                          {task.crew_type_id ?? EMPTY}
                        </span>
                      </Td>
                      <Td>
                        <span className="font-mono text-xs text-ink-2">
                          {task.crew_id ?? EMPTY}
                        </span>
                      </Td>
                      <Td>
                        <Status value={task.schedule_risk} />
                      </Td>
                      <Td>
                        <Status value={task.start_status} />
                      </Td>
                      {/* The one tinted chip on this row. */}
                      <Td>
                        <Pill value={task.end_status} />
                      </Td>
                      <Td align="right">
                        <span
                          className={
                            varianceTone(task.end_variance_days) === "danger"
                              ? "font-semibold text-danger"
                              : varianceTone(task.end_variance_days) === "good"
                                ? "text-good"
                                : "text-ink-3"
                          }
                        >
                          {formatVariance(task.end_variance_days)}
                        </span>
                      </Td>
                      <Td className="text-ink-2">{formatDate(task.target_end)}</Td>
                      {/* Beside the planned date, so the slip is on the row rather than inferred
                          from the overrun. An em dash means the task has not finished — which is
                          what OVERDUE and NOT_STARTED_LATE beside it already say. */}
                      <Td className="text-ink-2">{formatDate(task.actual_end)}</Td>
                      <Td align="right" className="text-ink-2">
                        {formatPercent(task.progress_percent)}
                      </Td>
                      <Td align="right">
                        {late && (
                          <button
                            onClick={() => setSuggestFor(open ? null : task.task_code)}
                            aria-expanded={open}
                            className={SUGGEST_BUTTON}
                          >
                            {open ? "Hide" : "Suggest"}
                          </button>
                        )}
                      </Td>
                    </tr>
                    {open && (
                      <tr>
                        <td colSpan={12} className="border-b border-line bg-surface-2">
                          <SuggestionPanel
                            wellId={wellId}
                            task={task}
                            onClose={() => setSuggestFor(null)}
                          />
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </TableShell>
        )}
      </Card>

      <p className="text-xs leading-relaxed text-ink-3">
        A delayed task is evidence that <em>that task</em> is slipping. It does not by itself
        establish that the task caused this well&rsquo;s milestone to slip. Recovery suggestions
        are AI-generated from the verified evidence shown with them &mdash; review them before
        acting.
      </p>
    </div>
  );
}
