import type {
  ActivitySummaryRow,
  CrewRow,
  PipelineStatus,
  Suggestion,
  TaskRow,
  WellRow,
  WellSuggestion,
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
  crews: () => get<{ crews: CrewRow[] }>("/api/crews").then((r) => r.crews),
  /**
   * Ask the suggestion agent about one late task. The only call that spends a model call, so it
   * is a POST, and it gets a long leash: a high-effort answer can take a minute or more. The Next
   * proxy's own timeout is raised to match in next.config.ts - without that it cuts the request
   * off at 30s whatever this says. `fresh` skips the API's cached answer.
   */
  /** Why is this WELL delayed, and how to overcome it. Same leash and reasons as `suggest`. */
  suggestWell: (wellId: string, fresh = false) =>
    get<WellSuggestion>(`/api/wells/${encodeURIComponent(wellId)}/suggest-well?fresh=${fresh}`, {
      method: "POST",
      timeoutMs: 250_000,
    }),
  suggest: (wellId: string, taskCode: string, fresh = false) =>
    get<Suggestion>(
      `/api/wells/${encodeURIComponent(wellId)}/suggest` +
        `?task_code=${encodeURIComponent(taskCode)}&fresh=${fresh}`,
      { method: "POST", timeoutMs: 250_000 },
    ),
  /**
   * Start a pipeline run.
   *
   * `regenerate` re-authors every query with the agents rather than reusing the frozen SQL —
   * minutes and real tokens, against ~17 seconds for a reuse. It is the default because that is
   * what the button says it does; pass false for a cheap refresh of the figures alone.
   */
  startRun: (refresh = false, regenerate = true) =>
    get<{ started: boolean }>(
      `/api/run?refresh=${refresh}&regenerate=${regenerate}`,
      { method: "POST" },
    ),
};
