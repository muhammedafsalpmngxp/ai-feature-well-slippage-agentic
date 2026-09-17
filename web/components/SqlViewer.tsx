"use client";

import { useCallback, useEffect, useState } from "react";
import { Card, ErrorNote, Skeleton } from "./ui";
import { api, ApiError } from "@/lib/api";
import { formatAge } from "@/lib/format";
import type { PipelineStatus } from "@/lib/types";

/**
 * The verified SQL behind each panel, selectable.
 *
 * This is provenance, not a debugging aid. An operations figure nobody can trace is a figure
 * nobody should act on, and these three statements are the entire derivation of every number on
 * the dashboard - the deadlines, the statuses, the variances are all computed in SQL, not here.
 *
 * Shown as read-only text. There is deliberately no way to edit or run a statement from the
 * browser: the pipeline's guarantee is that what executes was reviewed, and an editable box
 * would quietly void it.
 */
const QUERIES = [
  { key: "well_slippage", label: "1 · Well slippage", note: "One row per well · fleet-wide" },
  { key: "activity_summary", label: "2 · Delayed activity per well", note: "One row per well · fleet-wide" },
  { key: "activity_delay", label: "3 · Activity delay detail", note: "One row per task · one well, bound parameter" },
] as const;

export function SqlViewer({ status }: { status: PipelineStatus | null }) {
  const [key, setKey] = useState<string>(QUERIES[0].key);
  const [sql, setSql] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const load = useCallback(async (which: string) => {
    setSql(null);
    setError(null);
    try {
      const result = await api.sql(which);
      setSql(result.sql);
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : "Could not load this query. It may not have been generated yet.",
      );
    }
  }, []);

  useEffect(() => {
    void load(key);
  }, [key, load]);

  async function copy() {
    if (!sql) return;
    try {
      await navigator.clipboard.writeText(sql);
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    } catch {
      // Clipboard access is blocked outside a secure context and in some embedded views. The
      // text is selectable either way, so this is not worth an error banner.
      setCopied(false);
    }
  }

  const selected = QUERIES.find((q) => q.key === key);
  const meta = status?.queries?.[key];
  const lines = sql ? sql.split("\n").length : 0;

  return (
    <Card
      title="Generated SQL"
      subtitle="The verified query behind each panel — every figure above is computed here, not in the browser"
      action={
        <div className="flex flex-wrap items-center gap-2">
          <label htmlFor="sql-select" className="sr-only">
            Choose a query
          </label>
          <select
            id="sql-select"
            value={key}
            onChange={(e) => setKey(e.target.value)}
            className="h-9 rounded-lg border border-line-strong bg-surface px-2.5 text-[13px] text-ink outline-none"
          >
            {QUERIES.map((q) => (
              <option key={q.key} value={q.key}>
                {q.label}
              </option>
            ))}
          </select>
          <button
            onClick={copy}
            disabled={!sql}
            className="h-9 rounded-lg border border-line-strong px-3 text-xs font-semibold text-ink-2 transition-colors hover:bg-surface-2 disabled:opacity-40"
          >
            {copied ? "Copied" : "Copy"}
          </button>
        </div>
      }
    >
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b border-line bg-surface-2 px-5 py-2.5 text-xs text-ink-3">
        <span>{selected?.note}</span>
        {meta?.generated_at && (
          <span>
            Generated {formatAge(status?.age_hours)} · reviewed and approved before it was written
          </span>
        )}
        {lines > 0 && <span className="tnum ml-auto">{lines} lines</span>}
      </div>

      {error ? (
        <div className="p-5">
          <ErrorNote message={error} onRetry={() => void load(key)} />
        </div>
      ) : sql === null ? (
        <Skeleton rows={6} />
      ) : (
        <div className="p-5">
          <pre className="max-h-[28rem] overflow-auto rounded-lg border border-line bg-surface-2 px-4 py-3.5 text-xs leading-relaxed">
            <code className="font-mono whitespace-pre text-ink">{sql}</code>
          </pre>
        </div>
      )}
    </Card>
  );
}
