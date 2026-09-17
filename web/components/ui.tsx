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
      className={`overflow-hidden rounded-[10px] border border-line bg-surface ${className}`}
    >
      {(title || action) && (
        <header className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-5 py-4">
          <div className="space-y-0.5">
            {title && <h2 className="display text-sm font-semibold text-ink">{title}</h2>}
            {subtitle && <p className="text-xs text-ink-3">{subtitle}</p>}
          </div>
          {action}
        </header>
      )}
      {children}
    </section>
  );
}

/* ── Status vocabulary ─────────────────────────────────────────────────────── */

type Tone = "danger" | "warn" | "good" | "neutral" | "accent";

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

const CHIP: Record<Tone, string> = {
  danger: "bg-danger-soft text-danger",
  warn: "bg-warn-soft text-warn",
  good: "bg-good-soft text-good",
  accent: "bg-accent-soft text-accent",
  neutral: "bg-muted-soft text-ink-2",
};

/**
 * A tinted chip. RATIONED TO ONE COLUMN PER TABLE.
 *
 * Eight tinted chips across a row is confetti: everything is emphasised, so nothing is, and the
 * eye has no way to find the column that actually decides what to do next. Use this for the
 * single column a reader is scanning for - the headline failure, the end status - and use
 * `Status` for every other status column on the same row.
 */
export function Pill({
  value,
  tone,
  title,
  label,
}: {
  value: string | null | undefined;
  tone?: Tone;
  title?: string;
  /**
   * Display text, when the raw token reads badly. `humanise` lowercases everything it does not
   * capitalise, which turns "SLIPPED - FLAF" into "Slipped - flaf" - an acronym spelled as a
   * word. The untouched token always stays in the tooltip, so nothing is hidden by this.
   */
  label?: string;
}) {
  if (!value) return <span className="text-ink-3">{EMPTY}</span>;
  return (
    <span
      title={title ?? String(value)}
      className={`inline-flex h-[22px] items-center rounded-[5px] px-2 text-xs font-semibold whitespace-nowrap ${
        CHIP[tone ?? toneFor(value)]
      }`}
    >
      {label ?? humanise(value)}
    </span>
  );
}

const DOT: Record<Tone, string> = {
  danger: "bg-danger",
  warn: "bg-warn",
  good: "bg-good",
  accent: "bg-accent",
  neutral: "bg-muted",
};

/** Text tone. Only the states that need reading as a colour get one; the rest stay ink. */
const DOT_TEXT: Record<Tone, string> = {
  danger: "text-ink",
  warn: "text-warn",
  good: "text-ink",
  accent: "text-accent",
  neutral: "text-ink-2",
};

/**
 * A status as a coloured dot plus plain text, for the dense columns.
 *
 * The dot carries the colour so the text can stay black and legible: a row of six of these reads
 * as a row of data, where six chips reads as a row of buttons. The colour is never the only
 * signal - the word is always there - which is what keeps it usable for a colour-blind reader.
 */
export function Status({
  value,
  tone,
  title,
}: {
  value: string | null | undefined;
  tone?: Tone;
  title?: string;
}) {
  if (!value) return <span className="text-ink-3">{EMPTY}</span>;
  const resolved = tone ?? toneFor(value);
  return (
    <span
      title={title ?? String(value)}
      className={`inline-flex items-center gap-[7px] whitespace-nowrap ${DOT_TEXT[resolved]}`}
    >
      <span aria-hidden className={`h-[7px] w-[7px] shrink-0 rounded-full ${DOT[resolved]}`} />
      {humanise(value)}
    </span>
  );
}

/** The key the dots need. Without it a colour is decoration; with it, it is a legend. */
export function StatusLegend({ className = "" }: { className?: string }) {
  const items: Array<[string, string]> = [
    ["bg-danger", "Missed or delayed"],
    ["bg-warn", "Slipping"],
    ["bg-good", "On or ahead"],
    ["bg-muted", "Pending"],
    ["bg-accent", "Data quality"],
  ];
  return (
    <span className={`flex flex-wrap items-center gap-x-3.5 gap-y-1 text-[11px] text-ink-3 ${className}`}>
      {items.map(([dot, label]) => (
        <span key={label} className="inline-flex items-center gap-1.5">
          <span aria-hidden className={`h-[7px] w-[7px] rounded-full ${dot}`} />
          {label}
        </span>
      ))}
    </span>
  );
}

/* ── Stats ─────────────────────────────────────────────────────────────────── */

/**
 * One bordered strip of figures, divided by hairlines, rather than four cards floating apart.
 *
 * The four numbers are one reading - a portfolio's shape - so they belong in one object. The
 * negative margins are what collapse each cell's border onto its neighbour's, so the rule between
 * two cells is 1px however the grid wraps.
 */
export function StatStrip({ children }: { children: ReactNode }) {
  return (
    <div className="grid grid-cols-2 overflow-hidden rounded-[10px] border border-line bg-surface lg:grid-cols-4">
      {children}
    </div>
  );
}

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
    <div className="-mt-px -ml-px space-y-1.5 border-t border-l border-line px-5 py-[18px]">
      <p className="eyebrow">{label}</p>
      <p className={`display tnum text-[30px] leading-none font-semibold ${accent}`}>{value}</p>
      {hint && <p className="text-xs leading-snug text-ink-3">{hint}</p>}
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
    <div
      role="alert"
      className="flex flex-wrap items-center gap-x-4 gap-y-2 rounded-[10px] border border-danger/20 bg-danger-soft px-5 py-3.5"
    >
      <span aria-hidden className="shrink-0 text-danger">
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
          <circle cx="8" cy="8" r="6.5" stroke="currentColor" strokeWidth="1.5" />
          <path d="M8 5v3.5M8 11h.01" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
        </svg>
      </span>
      <p className="text-sm font-medium text-danger">{message}</p>
      {onRetry && (
        <button
          onClick={onRetry}
          className="ml-auto h-8 shrink-0 rounded-md border border-danger/30 px-3 text-xs font-semibold text-danger"
        >
          Try again
        </button>
      )}
    </div>
  );
}

export function Empty({ message }: { message: string }) {
  return (
    <p className="mx-auto max-w-2xl px-5 py-12 text-center text-sm leading-relaxed text-ink-3">
      {message}
    </p>
  );
}

/* ── Table shell ───────────────────────────────────────────────────────────── */

export function TableShell({ children }: { children: ReactNode }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-[13px]">{children}</table>
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
      className={`eyebrow sticky top-0 z-10 h-[34px] border-b border-line-strong bg-surface-2 px-4 whitespace-nowrap ${
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
      className={`h-11 border-b border-line px-4 whitespace-nowrap ${
        align === "right" ? "tnum text-right" : ""
      } ${className}`}
    >
      {children}
    </td>
  );
}
