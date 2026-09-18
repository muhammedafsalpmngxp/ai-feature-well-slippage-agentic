"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { SqlViewer } from "@/components/SqlViewer";
import { WellLookup } from "@/components/WellLookup";
import { RunProgress } from "@/components/RunProgress";
import { WellsTable } from "@/components/WellsTable";
import { Card, ErrorNote, Skeleton, Stat, StatStrip } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import { formatAge, formatVariance, varianceTone } from "@/lib/format";
import {
  MILESTONES,
  MILESTONE_LABEL,
  failedMilestones,
  worstVariance,
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
  // When the button was pressed, so the progress timer starts at the click.
  const [startedLocally, setStartedLocally] = useState<string | null>(null);

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
  //
  // 2s rather than 5s: this now drives a live stage list, and a run's stages turn over faster
  // than five seconds - at 5s the planner could begin and finish between two polls and never
  // appear. Still cheap, because a status read touches no database unless asked (see load).
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
    }, 2000);
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
    // EVERY failure, not just each well's first. Counting headlines made the priority order
    // masquerade as a finding: pegging showed 11 wells when it had actually failed on 94, and
    // rig-off 4 when it was 66, because rig-on sits first and absorbs everything behind it.
    // A well is therefore counted once PER MILESTONE it failed, so these no longer sum to the
    // fleet size - which is what the subtitle says.
    const byMilestone = new Map<string, number>();
    for (const well of rows) {
      for (const m of failedMilestones(well)) {
        byMilestone.set(m, (byMilestone.get(m) ?? 0) + 1);
      }
    }
    // Worst overrun across the fleet, over every milestone a well failed rather than only its
    // headline. Null variances are skipped, not treated as 0 - unmeasured is not on time.
    let worst: number | null = null;
    for (const well of rows) {
      const days = worstVariance(well);
      if (days !== null && (worst === null || days > worst)) worst = days;
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
    setStartedLocally(new Date().toISOString());
    try {
      // regenerate: the agents author and verify every query again. "Re-run analysis" has to
      // mean the analysis, not a re-execution of the SQL it produced last time.
      await api.startRun(false, true);
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
            title="Writes and verifies every query again with the agents. Takes a few minutes; the figures below stay on the last run until it finishes."
            className="h-10 rounded-lg bg-accent px-[18px] text-[13px] font-semibold text-on-accent transition-colors hover:bg-accent-strong disabled:opacity-50"
          >
            {running ? "Running…" : starting ? "Starting…" : "Re-run analysis"}
          </button>
        </div>
      </div>

      <RunProgress status={status} starting={starting} startedLocally={startedLocally} />

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
          hint="Days past any missed milestone deadline"
        />
        <Stat
          label="Data-quality flags"
          value={wells === null ? "—" : stats.dataQuality}
          hint="Wells missing a date needed to judge"
        />
      </StatStrip>

      <div className="grid gap-6 lg:grid-cols-3">
        <Card
          title="Wells failing each milestone"
          subtitle="A well appears once for every milestone it failed, so these do not sum to the fleet"
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
