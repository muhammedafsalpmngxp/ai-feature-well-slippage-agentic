"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { SqlViewer } from "@/components/SqlViewer";
import { WellLookup } from "@/components/WellLookup";
import { WellsTable } from "@/components/WellsTable";
import { Card, ErrorNote, Skeleton, Stat, StatStrip } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import { formatAge, formatVariance, varianceTone } from "@/lib/format";
import {
  MILESTONES,
  MILESTONE_LABEL,
  milestoneOf,
  type ActivitySummaryRow,
  type PipelineStatus,
  type WellRow,
} from "@/lib/types";

export default function Dashboard() {
  const [wells, setWells] = useState<WellRow[] | null>(null);
  const [activity, setActivity] = useState<ActivitySummaryRow[]>([]);
  const [status, setStatus] = useState<PipelineStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);

  const load = useCallback(async () => {
    setError(null);

    // ⚠ STATUS IS DELIBERATELY NOT AWAITED WITH THE OTHERS.
    //
    // It used to be, in one Promise.allSettled of all three - and allSettled waits for the
    // SLOWEST. /api/status runs a live catalogue read for its drift check, which on a database
    // with many objects can take minutes, so the well table sat behind a skeleton waiting on a
    // check that has no bearing on a single figure in it. Coming back from a well took as long
    // as that read, every time.
    //
    // The table is the page; status is decoration - how old the run is, the drift banner, the
    // SQL viewer's metadata. So the page renders as soon as the data lands and status fills in
    // late, or never, without blocking anything.
    //
    // NO SCHEMA CHECK HERE. Mounting a page is not a reason to read the database catalogue: the
    // check is slow, and its answer only matters when you are about to start a run. That one
    // call asks for it (see startRun).
    void api
      .status()
      .then(setStatus)
      .catch(() => setStatus(null));

    try {
      // Settled, not all: the activity summary is a bonus column on this page, so its absence
      // must not blank the well list, which is the page's actual subject.
      const [w, a] = await Promise.allSettled([api.wells(), api.activitySummary()]);
      if (w.status === "rejected") throw w.reason;
      setWells(w.value);
      setActivity(a.status === "fulfilled" ? a.value : []);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong loading the data.");
      setWells([]);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // Poll only while a run is actually in progress, then refresh once and stop.
  useEffect(() => {
    if (!status?.run.running) return;
    const timer = setInterval(async () => {
      const next = await api.status().catch(() => null);
      if (next && !next.run.running) {
        setStatus(next);
        void load();
      } else if (next) {
        setStatus(next);
      }
    }, 5000);
    return () => clearInterval(timer);
  }, [status?.run.running, load]);

  const activityByWell = useMemo(
    () => new Map(activity.map((r) => [String(r.well_id), Number(r.delayed_activity_codes)])),
    [activity],
  );

  // Only a hint for the lookup: a well absent here may still be worth investigating, so this
  // never blocks a lookup.
  const knownWells = useMemo(() => new Set(activityByWell.keys()), [activityByWell]);

  const stats = useMemo(() => {
    const rows = wells ?? [];
    const byMilestone = new Map<string, number>();
    for (const well of rows) {
      const m = milestoneOf(String(well.well_slippage_status ?? ""));
      if (!m) continue;
      byMilestone.set(m, (byMilestone.get(m) ?? 0) + 1);
    }
    // Worst overrun across the fleet, on whichever milestone each well's headline names.
    // Null variances are skipped, not treated as 0 - they are unmeasured, not on time.
    let worst: number | null = null;
    for (const well of rows) {
      const m = milestoneOf(String(well.well_slippage_status ?? ""));
      if (!m) continue;
      const v = well[`${m}_variance_days`];
      if (v === null || v === undefined || v === "") continue;
      const days = Number(v);
      if (!Number.isNaN(days) && (worst === null || days > worst)) worst = days;
    }
    // A well is counted once here however many milestones report a data-quality outcome, so
    // this is "wells affected", not a sum of flags.
    const dataQuality = rows.filter((w) =>
      MILESTONES.some((m) => w[`${m}_status`] === "DATA_QUALITY_ISSUE"),
    ).length;
    return { total: rows.length, byMilestone, worst, dataQuality };
  }, [wells]);

  const maxMilestone = Math.max(1, ...Array.from(stats.byMilestone.values()));
  const running = status?.run.running ?? false;

  async function startRun() {
    setStarting(true);
    try {
      await api.startRun(false);
      // The ONE place the schema check is asked for. At this moment the answer is worth its
      // cost: it says whether the run now starting will re-author the queries - minutes, and
      // tokens - or just re-execute the frozen ones.
      setStatus(await api.status(true));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not start a run.");
    } finally {
      setStarting(false);
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div className="space-y-1.5">
          <p className="eyebrow">Portfolio</p>
          <h1 className="display text-[26px] leading-tight font-semibold">
            Delayed well portfolio
          </h1>
          <p className="text-sm text-ink-2">
            Wells still in progress that have failed at least one contractual milestone.
          </p>
        </div>
        <div className="flex items-center gap-4">
          <div className="flex flex-col items-end gap-1">
            <span className="eyebrow">Last analysis</span>
            {/* `status === null` means the check has not answered YET, which is not the same
                claim as "never run" - and formatAge(undefined) returns exactly that. Asserting
                a fleet has never been analysed because a fetch is still in flight is the
                null-is-not-zero mistake wearing different clothes, so unknown gets a grey dot
                and its own wording until the answer arrives. */}
            <span className="flex items-center gap-1.5 text-[13px]">
              <span
                aria-hidden
                className={`h-1.5 w-1.5 rounded-full ${
                  status === null ? "bg-muted" : running ? "bg-warn" : "bg-good"
                }`}
              />
              <span className="tnum">
                {status === null
                  ? "Checking…"
                  : running
                    ? "Running…"
                    : formatAge(status.age_hours)}
              </span>
            </span>
          </div>
          <button
            onClick={startRun}
            disabled={running || starting}
            className="h-10 rounded-lg bg-accent px-[18px] text-[13px] font-semibold text-on-accent transition-colors hover:bg-accent-strong disabled:opacity-50"
          >
            {running ? "Running…" : starting ? "Starting…" : "Re-run analysis"}
          </button>
        </div>
      </div>

      {error && <ErrorNote message={error} onRetry={() => void load()} />}

      {/* Only reachable after starting a run, because that is the one call that asks for the
          schema check. So this describes what the run is doing, rather than telling a reader to
          re-run something they have just re-run. */}
      {status?.schema_drifted && (
        <div
          role="status"
          className="rounded-[10px] border border-warn/25 bg-warn-soft px-5 py-3.5 text-sm leading-relaxed text-warn"
        >
          <strong className="font-semibold">The database structure has changed</strong> since these
          queries were written, so this run is re-writing and re-verifying them. That takes a few
          minutes rather than a few seconds. The figures below describe the previous run until it
          finishes.
        </div>
      )}

      <StatStrip>
        <Stat
          label="Slipped wells"
          value={wells === null ? "—" : stats.total}
          hint="Failed at least one milestone"
        />
        <Stat
          label="Wells with delayed activity"
          value={activity.length === 0 ? "—" : activity.length}
          hint="At least one delayed activity code"
        />
        {/* Coloured only when there is actually an overrun - see the note on the detail page. */}
        <Stat
          label="Worst overrun"
          value={wells === null ? "—" : formatVariance(stats.worst)}
          tone={varianceTone(stats.worst) === "danger" ? "danger" : "neutral"}
          hint="Days past the headline milestone deadline"
        />
        <Stat
          label="Data-quality flags"
          value={wells === null ? "—" : stats.dataQuality}
          hint="Wells missing a date needed to judge"
        />
      </StatStrip>

      <div className="grid gap-6 lg:grid-cols-3">
        <Card
          title="First failure by milestone"
          subtitle="Priority order · each well counted once"
          className="lg:col-span-2"
        >
          <div className="space-y-3.5 px-5 py-[18px]">
            {MILESTONES.map((m) => {
              const count = stats.byMilestone.get(m) ?? 0;
              return (
                <div key={m} className="flex items-center gap-3.5">
                  <span className="w-24 shrink-0 text-right text-xs font-medium text-ink-2">
                    {MILESTONE_LABEL[m]}
                  </span>
                  <div className="h-2 flex-1 overflow-hidden rounded-sm bg-muted-soft">
                    <div
                      className="h-full rounded-sm bg-accent"
                      style={{ width: `${(count / maxMilestone) * 100}%` }}
                    />
                  </div>
                  <span className="tnum w-7 shrink-0 text-right text-[13px] font-semibold">
                    {count}
                  </span>
                </div>
              );
            })}
          </div>
        </Card>

        <Card title="Investigate a well">
          <div className="px-5 py-[18px]">
            <WellLookup knownWells={knownWells} />
          </div>
        </Card>
      </div>

      <Card title="Slipped wells" subtitle="Select a well to see its task-level delay">
        {wells === null ? <Skeleton rows={8} /> : (
          <WellsTable wells={wells} activityByWell={activityByWell} />
        )}
      </Card>

      <SqlViewer status={status} />
    </div>
  );
}
