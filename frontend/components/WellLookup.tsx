"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

/**
 * Enter any well id and drill into its activity delay.
 *
 * Separate from the table's links on purpose: the table only lists wells that failed a
 * contractual MILESTONE, and a well can carry late tasks while its milestones are still on
 * track (milestone_rules §5.11). Without a free input, those wells would be unreachable from
 * this dashboard even though the query can answer for them.
 *
 * The id is only ever used as a bound parameter server-side, so it cannot alter the query.
 */
export function WellLookup({
  defaultValue = "",
  knownWells,
  className = "",
}: {
  defaultValue?: string;
  /** Ids the summary knows carry delayed activity — used for a hint, never to block a lookup. */
  knownWells?: Set<string>;
  className?: string;
}) {
  const router = useRouter();
  const [value, setValue] = useState(defaultValue);
  const [touched, setTouched] = useState(false);

  useEffect(() => setValue(defaultValue), [defaultValue]);

  const trimmed = value.trim();
  // Deliberately permissive: well ids in this database are not all numeric (there are values
  // like '0000F' on the task record), so anything non-empty and short enough is allowed
  // through. Rejecting non-numeric input here would hide real wells.
  const valid = trimmed.length > 0 && trimmed.length <= 50;
  const unknown = valid && knownWells !== undefined && !knownWells.has(trimmed);

  function submit(event: React.FormEvent) {
    event.preventDefault();
    setTouched(true);
    if (!valid) return;
    router.push(`/wells/${encodeURIComponent(trimmed)}`);
  }

  return (
    <form onSubmit={submit} className={`flex flex-wrap items-start gap-2 ${className}`}>
      <div>
        <label htmlFor="well-lookup" className="sr-only">
          Well ID
        </label>
        <input
          id="well-lookup"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onBlur={() => setTouched(true)}
          placeholder="e.g. 35543"
          inputMode="text"
          autoComplete="off"
          aria-invalid={touched && !valid}
          aria-describedby="well-lookup-hint"
          className="tnum h-10 w-40 rounded-lg border border-line-strong bg-surface px-3 font-mono text-[13px] outline-none placeholder:font-sans placeholder:text-ink-3"
        />
      </div>
      <button
        type="submit"
        disabled={!valid}
        className="h-10 rounded-lg bg-accent px-4 text-[13px] font-semibold text-on-accent transition-colors hover:bg-accent-strong disabled:opacity-40"
      >
        Investigate
      </button>
      <p id="well-lookup-hint" className="basis-full text-xs leading-snug text-ink-3" aria-live="polite">
        {touched && !valid
          ? "Enter a well ID."
          : unknown
            ? "Not in the delayed-activity summary — the lookup will still run, and may return no late tasks."
            : "Runs the verified task-delay query for one well."}
      </p>
    </form>
  );
}
