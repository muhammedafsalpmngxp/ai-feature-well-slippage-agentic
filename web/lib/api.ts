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

/**
 * A request that never returns is worse than one that fails: the UI has no way to tell the
 * difference between "still working" and "will never answer", so it waits forever behind a
 * skeleton. Every call gets a ceiling; slow endpoints get their own.
 */
const DEFAULT_TIMEOUT_MS = 30_000;

type Options = RequestInit & { timeoutMs?: number };

async function get<T>(path: string, init?: Options): Promise<T> {
  const { timeoutMs = DEFAULT_TIMEOUT_MS, ...rest } = init ?? {};
  let response: Response;
  try {
    // `rest` is spread last so a caller-supplied signal still wins.
    response = await fetch(path, {
      cache: "no-store",
      signal: AbortSignal.timeout(timeoutMs),
      ...rest,
    });
  } catch (err) {
    if (err instanceof DOMException && err.name === "TimeoutError") {
      throw new ApiError(
        `The API did not respond within ${Math.round(timeoutMs / 1000)}s.`,
        0,
      );
    }
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
  /**
   * Pipeline status. Decoration, and nothing waits for it, so it gets a short leash: a status
   * read still going after ten seconds will not arrive in time to be useful.
   *
   * `checkSchema` is what makes the API compare the live schema against the frozen queries, and
   * it is the only slow part of that response — a catalogue read. Left off, this is pure
   * filesystem. Pass it only where the answer changes what happens next.
   */
  status: (checkSchema = false) =>
    get<PipelineStatus>(`/api/status${checkSchema ? "?check_schema=true" : ""}`, {
      // The check has its own budget server-side; allow for it before giving up here.
      timeoutMs: checkSchema ? 20_000 : 10_000,
    }),
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
