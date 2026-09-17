import type {
  ActivitySummaryRow,
  PipelineStatus,
  TaskRow,
  WellRow,
} from "./types";

/**
 * Requests go to a relative /api path, proxied to FastAPI by next.config.ts.
 *
 * That keeps the browser on one origin - no CORS preflight per request - and means no API host
 * is baked into the client bundle, so the same build runs in any environment.
 */
export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function get<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, { cache: "no-store", ...init });
  } catch {
    // A fetch rejection is a connection failure, never an HTTP status. Saying "the API is not
    // reachable" points at the actual fix; "failed to fetch" points at nothing.
    throw new ApiError("Cannot reach the API. Is the FastAPI service running?", 0);
  }

  if (!response.ok) {
    let detail: string | null = null;
    try {
      detail = ((await response.json()) as { detail?: string }).detail ?? null;
    } catch {
      /* body was not JSON; fall through to the status text */
    }
    throw new ApiError(detail || `Request failed (${response.status})`, response.status);
  }
  return (await response.json()) as T;
}

export const api = {
  status: () => get<PipelineStatus>("/api/status"),
  wells: () => get<{ wells: WellRow[] }>("/api/wells").then((r) => r.wells),
  activitySummary: () =>
    get<{ wells: ActivitySummaryRow[] }>("/api/activity-summary").then((r) => r.wells),
  wellActivity: (wellId: string) =>
    get<{ well_id: string; tasks: TaskRow[] }>(
      `/api/wells/${encodeURIComponent(wellId)}/activity`,
    ).then((r) => r.tasks),
  brief: () => get<{ markdown: string }>("/api/brief").then((r) => r.markdown),
  sql: (key: string) => get<{ key: string; sql: string }>(`/api/sql/${encodeURIComponent(key)}`),
  startRun: (refresh = false) =>
    get<{ started: boolean }>(`/api/run?refresh=${refresh}`, { method: "POST" }),
};
