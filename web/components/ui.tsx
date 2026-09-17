import type { ReactNode } from "react";
import { EMPTY, humanise } from "@/lib/format";

/* ── Card ──────────────────────────────────────────────────────────────────── */

export function Card({
  title,
  subtitle,
  action,
  children,
  className = "",
}: {
  title?: string;
  subtitle?: string;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section
      className={`rounded-xl border border-line bg-surface ${className}`}
    >
      {(title || action) && (
        <header className="flex flex-wrap items-start justify-between gap-3 border-b border-line px-5 py-4">
          <div>
            {title && <h2 className="text-sm font-semibold tracking-tight">{title}</h2>}
            {subtitle && <p className="mt-0.5 text-xs text-ink-3">{subtitle}</p>}
          </div>
          {action}
        </header>
      )}
      {children}
    </section>
  );
}

/* ── Status pill ───────────────────────────────────────────────────────────── */

type Tone = "danger" | "warn" | "good" | "neutral" | "accent";

const TONE: Record<Tone, string> = {
  danger: "bg-danger-soft text-danger",
  warn: "bg-warn-soft text-warn",
  good: "bg-good-soft text-good",
  accent: "bg-accent-soft text-accent",
  neutral: "bg-muted-soft text-ink-2",
};

/**
 * A status token's tone.
 *
 * DATA_QUALITY_ISSUE is deliberately NOT red: it does not mean late, it means the date needed to
 * judge lateness is absent. Colouring it as a delay would assert something the data does not say.
 * PENDING and RIG_ARRIVED are likewise neutral and good - a rig that has arrived is never a
 * construction failure (business_rules §4), whenever it arrived.
 */
export function toneFor(status: string | null | undefined): Tone {
  switch ((status || "").toUpperCase()) {
    case "MISSED":
    case "DELAYED":
    case "OVERDUE":
    case "COMPLETED_LATE":
    case "START_DELAYED":
    case "RED":
    case "NOT_STARTED_LATE":
    case "NOT_STARTED_SLIPPING":
      return "danger";
    case "DUE_TODAY":
    case "AMBER_START_SLIPPING":
    case "AMBER_START_DELAYED":
      return "warn";
    case "AHEAD_OF_SCHEDULE":
    case "ON_SCHEDULE":
    case "COMPLETED_EARLY":
    case "COMPLETED_ON_TIME":
    case "STARTED_EARLY":
    case "STARTED_ON_TIME":
    case "GREEN":
    case "COMPLETED":
    case "RIG_ARRIVED":
      return "good";
    case "DATA_QUALITY_ISSUE":
      return "accent";
    default:
      return "neutral";
  }
}

export function Pill({
  value,
  tone,
  title,
}: {
  value: string | null | undefined;
  tone?: Tone;
  title?: string;
}) {
  if (!value) return <span className="text-ink-3">{EMPTY}</span>;
  return (
    <span
      title={title ?? String(value)}
      className={`inline-flex items-center whitespace-nowrap rounded-md px-2 py-0.5 text-xs font-medium ${
        TONE[tone ?? toneFor(value)]
      }`}
    >
      {humanise(value)}
    </span>
  );
}

/* ── Stat ──────────────────────────────────────────────────────────────────── */

export function Stat({
  label,
  value,
  hint,
  tone = "neutral",
}: {
  label: string;
  value: ReactNode;
  hint?: string;
  tone?: Tone;
}) {
  const accent =
    tone === "danger"
      ? "text-danger"
      : tone === "warn"
        ? "text-warn"
        : tone === "good"
          ? "text-good"
          : "text-ink";
  return (
    <div className="rounded-xl border border-line bg-surface px-5 py-4">
      <p className="text-xs font-medium uppercase tracking-wide text-ink-3">{label}</p>
      <p className={`tnum mt-1.5 text-2xl font-semibold tracking-tight ${accent}`}>{value}</p>
      {hint && <p className="mt-1 text-xs text-ink-3">{hint}</p>}
    </div>
  );
}

/* ── States ────────────────────────────────────────────────────────────────── */

export function Skeleton({ rows = 6 }: { rows?: number }) {
  return (
    <div className="space-y-2 p-5" aria-busy="true" aria-live="polite">
      <span className="sr-only">Loading</span>
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="skeleton h-9 rounded-md" />
      ))}
    </div>
  );
}

export function ErrorNote({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div role="alert" className="m-5 rounded-lg border border-line bg-danger-soft px-4 py-3">
      <p className="text-sm font-medium text-danger">{message}</p>
      {onRetry && (
        <button
          onClick={onRetry}
          className="mt-2 text-xs font-medium text-danger underline underline-offset-2"
        >
          Try again
        </button>
      )}
    </div>
  );
}

export function Empty({ message }: { message: string }) {
  return <p className="px-5 py-10 text-center text-sm text-ink-3">{message}</p>;
}

/* ── Table shell ───────────────────────────────────────────────────────────── */

export function TableShell({ children }: { children: ReactNode }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-sm">{children}</table>
    </div>
  );
}

export function Th({
  children,
  align = "left",
  className = "",
}: {
  children: ReactNode;
  align?: "left" | "right";
  className?: string;
}) {
  return (
    <th
      scope="col"
      className={`sticky top-0 z-10 whitespace-nowrap border-b border-line bg-surface-2 px-4 py-2.5 text-xs font-semibold uppercase tracking-wide text-ink-3 ${
        align === "right" ? "text-right" : "text-left"
      } ${className}`}
    >
      {children}
    </th>
  );
}

export function Td({
  children,
  align = "left",
  className = "",
}: {
  children: ReactNode;
  align?: "left" | "right";
  className?: string;
}) {
  return (
    <td
      className={`border-b border-line px-4 py-2.5 ${
        align === "right" ? "tnum text-right" : ""
      } ${className}`}
    >
      {children}
    </td>
  );
}
