/** Presentation helpers. The rule the whole file exists to enforce: null is not zero. */

/**
 * A missing value renders as an em dash, never as 0 or "on time".
 *
 * milestone_rules §3 is explicit that "not due yet" must be empty rather than zero, because 0
 * already means "completed exactly on the deadline". Rendering a null as 0 would put the
 * distinction the SQL works to preserve straight back in the bin.
 */
export const EMPTY = "—";

export function isMissing(value: unknown): boolean {
  return value === null || value === undefined || value === "";
}

/** A date as "12 Mar 2026". Fixed en-GB so a screenshot reads the same for everyone. */
export function formatDate(value: string | null | undefined): string {
  if (isMissing(value)) return EMPTY;
  const date = new Date(value as string);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" });
}

/**
 * A variance in days, signed so the direction is never ambiguous.
 *
 * business_rules §5: a NEGATIVE variance means the work finished EARLY, which is a good outcome,
 * not a delay. Showing an absolute value here is the mistake that rule warns about, so the sign
 * is always rendered.
 */
export function formatVariance(value: number | null | undefined): string {
  if (isMissing(value)) return EMPTY;
  const days = Number(value);
  if (Number.isNaN(days)) return EMPTY;
  if (days === 0) return "0d";
  return `${days > 0 ? "+" : ""}${days}d`;
}

export function varianceTone(value: number | null | undefined): "danger" | "good" | "neutral" {
  if (isMissing(value)) return "neutral";
  const days = Number(value);
  if (Number.isNaN(days) || days === 0) return "neutral";
  return days > 0 ? "danger" : "good";
}

export function formatNumber(value: number | null | undefined): string {
  if (isMissing(value)) return EMPTY;
  return Number(value).toLocaleString("en-GB");
}

export function formatPercent(value: number | null | undefined): string {
  if (isMissing(value)) return EMPTY;
  return `${Math.round(Number(value))}%`;
}

/** "3 hours ago" / "just now", for how stale the last pipeline run is. */
export function formatAge(hours: number | null | undefined): string {
  if (isMissing(hours)) return "never run";
  const h = Number(hours);
  if (h < 0.05) return "just now";
  if (h < 1) return `${Math.round(h * 60)} min ago`;
  if (h < 24) return `${h.toFixed(1)} h ago`;
  return `${Math.round(h / 24)} d ago`;
}

/** Human text for a status token, without inventing meaning it does not carry. */
export function humanise(value: string | null | undefined): string {
  if (isMissing(value)) return EMPTY;
  return String(value)
    .replace(/_/g, " ")
    .toLowerCase()
    .replace(/^\w/, (c) => c.toUpperCase());
}

/**
 * WBS names in this database carry embedded newlines, which break any single-line cell.
 * Collapsing whitespace is presentation only - the value itself is untouched.
 */
export function cleanLabel(value: string | null | undefined): string {
  if (isMissing(value)) return EMPTY;
  return String(value).split(/\s+/).join(" ").trim() || EMPTY;
}
