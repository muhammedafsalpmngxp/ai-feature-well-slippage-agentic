"use client";

import Link from "next/link";
import { use, useCallback, useEffect, useMemo, useState } from "react";
import {
  Card,
  Empty,
  ErrorNote,
  Pill,
  Skeleton,
  Stat,
  TableShell,
  Td,
  Th,
} from "@/components/ui";
import { WellLookup } from "@/components/WellLookup";
import { api, ApiError } from "@/lib/api";
import { EMPTY, cleanLabel, formatDate, formatPercent, formatVariance, varianceTone } from "@/lib/format";
import {
  MILESTONES,
  MILESTONE_LABEL,
  type TaskRow,
  type WellRow,
} from "@/lib/types";

export default function WellDetail({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const wellId = decodeURIComponent(id);

  const [well, setWell] = useState<WellRow | null | undefined>(undefined);
  const [tasks, setTasks] = useState<TaskRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [riskFilter, setRiskFilter] = useState("all");
  const [tasksFailed, setTasksFailed] = useState(false);

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
    return { total: rows.length, red, amber, notStarted, worst, activities, unmapped };
  }, [tasks]);

  const visible = useMemo(() => {
    const rows = tasks ?? [];
    if (riskFilter === "all") return rows;
    if (riskFilter === "AMBER") return rows.filter((t) => t.schedule_risk?.startsWith("AMBER"));
    return rows.filter((t) => t.schedule_risk === riskFilter);
  }, [tasks, riskFilter]);

  return (
    <div className="space-y-6">
      <div>
        <Link href="/" className="text-xs text-accent hover:underline">
          ← All wells
        </Link>
        <div className="mt-2 flex flex-wrap items-start justify-between gap-4">
          <div>
            <h1 className="tnum text-xl font-semibold tracking-tight">Well {wellId}</h1>
            <p className="mt-1 text-sm text-ink-2">
              Contractual milestones and task-level activity delay.
            </p>
          </div>
          <WellLookup defaultValue={wellId} />
        </div>
      </div>

      {error && <ErrorNote message={error} onRetry={() => void load()} />}

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
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
        <Stat label="Red tasks" value={tasks === null ? "—" : counts.red} tone="danger"
              hint="End date already missed" />
        <Stat label="Not started, late" value={tasks === null ? "—" : counts.notStarted} tone="warn"
              hint="Planned start has passed" />
        <Stat label="Worst overrun" value={tasks === null ? "—" : formatVariance(counts.worst)}
              tone="danger" hint="Days past planned end" />
      </div>

      <Card title="Contractual milestones" subtitle="Deadlines are derived, never stored">
        {well === undefined ? (
          <Skeleton rows={3} />
        ) : well === null ? (
          <Empty message="This well is not in the current slippage listing." />
        ) : (
          <TableShell>
            <thead>
              <tr>
                <Th>Milestone</Th>
                <Th>Deadline</Th>
                <Th>Status</Th>
                <Th align="right">Variance</Th>
              </tr>
            </thead>
            <tbody>
              {MILESTONES.map((m) => {
                const variance = well[`${m}_variance_days`] as number | null;
                return (
                  <tr key={m}>
                    <Td className="font-medium">{MILESTONE_LABEL[m]}</Td>
                    <Td className="whitespace-nowrap text-ink-2">
                      {formatDate(well[`${m}_deadline`] as string)}
                    </Td>
                    <Td>
                      <Pill value={well[`${m}_status`] as string} />
                    </Td>
                    <Td align="right">
                      <span
                        className={
                          varianceTone(variance) === "danger"
                            ? "font-medium text-danger"
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
          <select
            value={riskFilter}
            onChange={(e) => setRiskFilter(e.target.value)}
            aria-label="Filter by schedule risk"
            className="h-8 rounded-md border border-line bg-canvas px-2 text-sm"
          >
            <option value="all">All tasks ({counts.total})</option>
            <option value="RED">Red ({counts.red})</option>
            <option value="AMBER">Amber ({counts.amber})</option>
            <option value="GREEN">Green</option>
          </select>
        }
      >
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
                <Th>Crew</Th>
                <Th>Risk</Th>
                <Th>End status</Th>
                <Th align="right">Overrun</Th>
                <Th>Planned end</Th>
                <Th align="right">Progress</Th>
              </tr>
            </thead>
            <tbody>
              {visible.map((task, i) => (
                <tr key={`${task.task_code}-${i}`} className="hover:bg-surface-2">
                  <Td className="max-w-[260px]">
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
                  <Td>
                    <span className="font-mono text-xs text-ink-2">
                      {task.crew_code ?? EMPTY}
                    </span>
                  </Td>
                  <Td>
                    <Pill value={task.schedule_risk} />
                  </Td>
                  <Td>
                    <Pill value={task.end_status} />
                  </Td>
                  <Td align="right">
                    <span
                      className={
                        varianceTone(task.end_variance_days) === "danger"
                          ? "font-medium text-danger"
                          : varianceTone(task.end_variance_days) === "good"
                            ? "text-good"
                            : "text-ink-3"
                      }
                    >
                      {formatVariance(task.end_variance_days)}
                    </span>
                  </Td>
                  <Td className="whitespace-nowrap text-ink-2">
                    {formatDate(task.target_end)}
                  </Td>
                  <Td align="right" className="text-ink-2">
                    {formatPercent(task.progress_percent)}
                  </Td>
                </tr>
              ))}
            </tbody>
          </TableShell>
        )}
      </Card>

      <p className="text-xs text-ink-3">
        A delayed task is evidence that <em>that task</em> is slipping. It does not by itself
        establish that the task caused this well&rsquo;s milestone to slip.
      </p>
    </div>
  );
}
