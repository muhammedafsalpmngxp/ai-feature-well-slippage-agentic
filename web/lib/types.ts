/**
 * Shapes returned by the API.
 *
 * Every row is keyed by the OUTPUT CONTRACT column names the agents are held to, so a rename on
 * the SQL side shows up here as a type error rather than as a silently blank column.
 */

/** The six milestones, in reporting-priority order (milestone_rules §4). */
export const MILESTONES = [
  "rig_on",
  "flaf",
  "pegging",
  "construction",
  "rig_off",
  "hookup",
] as const;

export type Milestone = (typeof MILESTONES)[number];

export const MILESTONE_LABEL: Record<Milestone, string> = {
  rig_on: "Rig-on",
  flaf: "FLAF",
  pegging: "Pegging",
  construction: "Construction",
  rig_off: "Rig-off",
  hookup: "Hook-up",
};

export type MilestoneStatus =
  | "DATA_QUALITY_ISSUE"
  | "MISSED"
  | "PENDING"
  | "AHEAD_OF_SCHEDULE"
  | "ON_SCHEDULE"
  | "DELAYED"
  | "RIG_ARRIVED";

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
   * True when the live database no longer matches what these queries were built against.
   * `null` means the check could not run — which must be shown as unknown, never as fine.
   */
  schema_drifted: boolean | null;
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

/** The milestone a well's headline status names, or null when it is not a slippage value. */
export function milestoneOf(status: string): Milestone | null {
  const key = (status || "").replace(/^SLIPPED\s*-\s*/i, "").trim().toUpperCase();
  const map: Record<string, Milestone> = {
    "RIG ON": "rig_on",
    FLAF: "flaf",
    PEGGING: "pegging",
    CONSTRUCTION: "construction",
    "RIG OFF": "rig_off",
    "HOOK-UP": "hookup",
  };
  return map[key] ?? null;
}
