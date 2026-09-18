/**
 * Shapes returned by the API.
 *
 * Every row is keyed by the OUTPUT CONTRACT column names the agents are held to, so a rename on
 * the SQL side shows up here as a type error rather than as a silently blank column.
 */

/**
 * The milestones, in reporting-priority order (milestone_rules §4).
 *
 * FOUR, not the six §1 defines: construction and hook-up were removed. See the note at the top
 * of app/graph/scenarios.py for why — this list must stay in step with the SCENARIOS there,
 * which is what generates the query's output contract.
 */
export const MILESTONES = ["rig_on", "flaf", "pegging", "rig_off"] as const;

export type Milestone = (typeof MILESTONES)[number];

export const MILESTONE_LABEL: Record<Milestone, string> = {
  rig_on: "Rig-on",
  flaf: "FLAF",
  pegging: "Pegging",
  rig_off: "Rig-off",
};

export type MilestoneStatus =
  | "DATA_QUALITY_ISSUE"
  | "MISSED"
  | "PENDING"
  | "AHEAD_OF_SCHEDULE"
  | "ON_SCHEDULE"
  | "DELAYED";

export type ScheduleRisk = "RED" | "AMBER_START_SLIPPING" | "AMBER_START_DELAYED" | "GREEN";

export interface WellRow {
  well_id: number | string;
  well_slippage_status: string;
  [key: string]: string | number | null;
}

export interface ActivitySummaryRow {
  well_id: number | string;
  delayed_activity_codes: number;
}

export interface TaskRow {
  well_id: number | string;
  task_code: string;
  action_on: string | null;
  activity_id: string | null;
  activity_code: string | null;
  wbs: string | null;
  crew_code: string | null;
  target_start: string | null;
  target_end: string | null;
  actual_start: string | null;
  actual_end: string | null;
  start_status: string | null;
  start_variance_days: number | null;
  end_status: string | null;
  end_variance_days: number | null;
  execution_status: string | null;
  schedule_risk: ScheduleRisk | null;
  progress_percent: number | null;
}

export interface PipelineStatus {
  out_dir: string;
  last_run: string | null;
  age_hours: number | null;
  /**
   * True when the live database no longer matches what these queries were built against, i.e.
   * a run would re-write them rather than just re-execute them.
   *
   * `null` means no answer, for either of two reasons — and `schema_checked` is what separates
   * them. Never shown as fine either way.
   */
  schema_drifted: boolean | null;
  /**
   * Whether the schema check was attempted at all. False on an ordinary status read: the check
   * needs a database catalogue read, which is slow, so it is only asked for when a run starts.
   */
  schema_checked: boolean;
  /** Per-query freeze state, from `sql/`. Filesystem facts; `state` needs the check to be exact. */
  frozen?: Record<
    string,
    {
      key: string;
      label: string;
      state: "current" | "stale" | "hand-edited" | "unverified" | "missing" | "unknown";
      frozen_at: string | null;
      fingerprint: string | null;
      columns: string[];
      row_count: number;
      hand_edited: boolean;
    }
  >;
  queries: Record<
    string,
    { label: string; available: boolean; needs_well_id: boolean; generated_at: string | null }
  >;
  run: {
    running: boolean;
    started_at: string | null;
    finished_at: string | null;
    exit_code: number | null;
  };
}

/**
 * The milestone NAME a headline status carries, for display.
 *
 * `undefined` when the status names no milestone (a data-quality headline, say), which is the
 * signal to fall back to the generic humanised token rather than to invent a label.
 */
export function milestoneLabelOf(status: string | null | undefined): string | undefined {
  const milestone = milestoneOf(String(status ?? ""));
  return milestone ? MILESTONE_LABEL[milestone] : undefined;
}

/** The two statuses that mean a milestone was failed, as milestone_rules §4 defines failure. */
const FAILED_STATUSES = new Set(["MISSED", "DELAYED"]);

/**
 * EVERY milestone this well failed, in reporting-priority order.
 *
 * Shared by the table and the chart so they can never disagree about what "failed" means.
 * Derived from each milestone's own status rather than from `well_slippage_status`, which
 * carries only the FIRST failure - the headline is the right thing to sort by and the wrong
 * thing to count with, because the priority order lets rig-on absorb everything behind it.
 */
export function failedMilestones(well: WellRow): Milestone[] {
  return MILESTONES.filter((m) =>
    FAILED_STATUSES.has(String(well[`${m}_status`] ?? "").toUpperCase()),
  );
}

/**
 * The worst overrun among the milestones a well actually failed.
 *
 * Nulls are skipped, never treated as 0: unmeasured is not on time.
 */
export function worstVariance(well: WellRow): number | null {
  let worst: number | null = null;
  for (const m of failedMilestones(well)) {
    const value = well[`${m}_variance_days`];
    if (value === null || value === undefined || value === "") continue;
    const days = Number(value);
    if (!Number.isNaN(days) && (worst === null || days > worst)) worst = days;
  }
  return worst;
}

/** The milestone a well's headline status names, or null when it is not a slippage value. */
export function milestoneOf(status: string): Milestone | null {
  const key = (status || "").replace(/^SLIPPED\s*-\s*/i, "").trim().toUpperCase();
  const map: Record<string, Milestone> = {
    "RIG ON": "rig_on",
    FLAF: "flaf",
    PEGGING: "pegging",
    "RIG OFF": "rig_off",
  };
  return map[key] ?? null;
}
