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
  /**
   * Raw ids from the task record: which crew was assigned, and its type. Crew TYPE decides which
   * crews can take over a late task. Optional because an activity query frozen before these
   * columns existed does not return them - the next pipeline run re-authors it.
   */
  crew_id?: number | string | null;
  crew_type_id?: number | string | null;
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

/** One crew, fleet-wide, from the crew_availability query. */
export interface CrewRow {
  crew_id: number | string;
  crew_type_id: number | string | null;
  open_tasks: number;
  in_progress_tasks: number;
  overdue_tasks: number;
  wells_active: number;
  latest_action_on: string | null;
  /** AVAILABLE = no in-progress task is recorded for the crew. Not leave, location or shift. */
  availability_status: "AVAILABLE" | "BUSY" | string;
}

export type CauseVerdict = "yes" | "possibly" | "no" | "unknown";

/** What the suggestion agent concluded. Advice, not a verified figure - render it as such. */
export interface Advice {
  summary: string;
  caused_by_other_delay: CauseVerdict;
  causes: { cause: string; evidence: string; confidence: string }[];
  actions: {
    action: string;
    crew_id: number | string | null;
    rationale: string;
    /** Set by the API when the agent named a crew it was not given as a candidate. */
    crew_unverified?: boolean;
  }[];
  checks: string[];
  caveats: string[];
}

export interface MilestoneEvidence {
  milestone: string;
  owner: string;
  deadline: string | null;
  actual: string | null;
  status: string | null;
  variance_days: number | null;
}

export type WellDelayVerdict = "yes" | "at_risk" | "no" | "unknown";

/** What the agent concluded about the WHOLE well. Advice, not a verified figure. */
export interface WellAdvice {
  description: string;
  delayed: WellDelayVerdict;
  why_delayed: { reason: string; evidence: string; owner: string; confidence: string }[];
  actions: {
    priority: number;
    action: string;
    crew_type_id: number | string | null;
    crew_id: number | string | null;
    rationale: string;
    /** Set by the API when the agent named a crew / crew type it was not given. */
    crew_unverified?: boolean;
    type_unverified?: boolean;
  }[];
  checks: string[];
  caveats: string[];
}

/** One crew type's share of a well's late work, with the crews that could take it over. */
export interface CrewTypeGroup {
  crew_type_id: string | null;
  late_tasks: number;
  worst_overrun_days: number | null;
  wbs: string[];
  unassigned_late_tasks: number;
  assigned_crews: CrewRow[];
  available_count: number;
  busy_count: number;
  type_total: number;
  candidates: CrewRow[];
}

/** POST /api/wells/{id}/suggest-well: why this well is delayed, beside the evidence behind it. */
export interface WellSuggestion {
  well_id: string;
  generated_at: string;
  model: string;
  cached: boolean;
  advice: WellAdvice | null;
  raw: string | null;
  evidence: {
    well: Suggestion["evidence"]["well"];
    tasks: {
      total: number;
      late: number;
      red: number;
      amber: number;
      not_started_late: number;
      in_progress: number;
      completed: number;
      due_today: number;
    };
    late_by_wbs: {
      wbs: string;
      late_tasks: number;
      red: number;
      not_started_late: number;
      worst_overrun_days: number | null;
      crew_type_ids: string[];
    }[];
    late_by_crew_type: CrewTypeGroup[];
    most_urgent: Partial<TaskRow>[];
    crew_note: string | null;
  };
}

/** POST /api/wells/{id}/suggest: the advice, beside the verified evidence it was reasoned from. */
export interface Suggestion {
  well_id: string;
  task_code: string;
  generated_at: string;
  model: string;
  cached: boolean;
  /** Null when the reply was not the structure asked for; `raw` then holds what was said. */
  advice: Advice | null;
  raw: string | null;
  evidence: {
    task: Partial<TaskRow>;
    earlier_late_tasks: Partial<TaskRow>[];
    same_wbs_late_tasks: Partial<TaskRow>[];
    well: {
      /** true: in the slippage listing. false: no milestone failed. null: listing unavailable. */
      listed: boolean | null;
      headline: string | null;
      milestones: MilestoneEvidence[];
      note: string | null;
    };
    crew: {
      crew_type_id: number | string | null;
      assigned_crew_id: number | string | null;
      assigned: CrewRow | null;
      available: CrewRow[];
      available_count: number;
      busy_count: number;
      type_total: number;
      note: string | null;
    };
  };
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
    /**
     * The pipeline's last ~40 log lines, streamed by the API as the subprocess writes them.
     * This is what lets the browser show which STAGE a run is in rather than only "running" —
     * real events, not a guessed progress bar. It is a rolling window, so early lines scroll
     * off on a long run; read it as "furthest progress seen", never as a complete transcript.
     */
    tail?: string[];
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
