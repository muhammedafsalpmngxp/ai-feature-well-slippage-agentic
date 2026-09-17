"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { Empty, Pill, Status, StatusLegend, Td, TableShell, Th } from "./ui";
import { EMPTY, formatDate, formatVariance, varianceTone } from "@/lib/format";
import {
  MILESTONES,
  MILESTONE_LABEL,
  milestoneLabelOf,
  milestoneOf,
  type WellRow,
} from "@/lib/types";

type SortKey = "well_id" | "status" | "variance";

/** The variance of the milestone a well's headline status actually names. */
function headlineVariance(well: WellRow): number | null {
  const milestone = milestoneOf(String(well.well_slippage_status ?? ""));
  if (!milestone) return null;
  const value = well[`${milestone}_variance_days`];
  return value === null || value === undefined || value === "" ? null : Number(value);
}

const FIELD =
  "h-9 rounded-lg border border-line-strong bg-surface px-2.5 text-[13px] text-ink outline-none";

export function WellsTable({
  wells,
  activityByWell,
}: {
  wells: WellRow[];
  activityByWell: Map<string, number>;
}) {
  const [query, setQuery] = useState("");
  const [milestone, setMilestone] = useState<string>("all");
  const [sort, setSort] = useState<SortKey>("variance");

  const rows = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const filtered = wells.filter((w) => {
      if (milestone !== "all" && milestoneOf(String(w.well_slippage_status ?? "")) !== milestone) {
        return false;
      }
      if (!needle) return true;
      return String(w.well_id).toLowerCase().includes(needle);
    });

    return [...filtered].sort((a, b) => {
      if (sort === "well_id") return String(a.well_id).localeCompare(String(b.well_id), undefined, { numeric: true });
      if (sort === "status") {
        return String(a.well_slippage_status).localeCompare(String(b.well_slippage_status));
      }
      // Worst first. A null variance sorts last rather than as zero - it is unmeasured, not on time.
      const av = headlineVariance(a);
      const bv = headlineVariance(b);
      if (av === null && bv === null) return 0;
      if (av === null) return 1;
      if (bv === null) return -1;
      return bv - av;
    });
  }, [wells, query, milestone, sort]);

  return (
    <>
      <div className="flex flex-wrap items-center gap-2 border-b border-line px-5 py-3">
        <label className="sr-only" htmlFor="well-search">
          Search by well ID
        </label>
        <div className="relative flex items-center">
          <span aria-hidden className="pointer-events-none absolute left-3 text-ink-3">
            <svg width="15" height="15" viewBox="0 0 15 15" fill="none">
              <circle cx="6.5" cy="6.5" r="4.5" stroke="currentColor" strokeWidth="1.4" />
              <path d="M10 10L13.5 13.5" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
            </svg>
          </span>
          <input
            id="well-search"
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search well ID"
            className={`${FIELD} w-52 pl-8 placeholder:text-ink-3`}
          />
        </div>
        <label className="sr-only" htmlFor="milestone-filter">
          Filter by milestone
        </label>
        <select
          id="milestone-filter"
          value={milestone}
          onChange={(e) => setMilestone(e.target.value)}
          className={FIELD}
        >
          <option value="all">All milestones</option>
          {MILESTONES.map((m) => (
            <option key={m} value={m}>
              {MILESTONE_LABEL[m]}
            </option>
          ))}
        </select>
        <label className="sr-only" htmlFor="sort-by">
          Sort by
        </label>
        <select
          id="sort-by"
          value={sort}
          onChange={(e) => setSort(e.target.value as SortKey)}
          className={FIELD}
        >
          <option value="variance">Worst overrun first</option>
          <option value="well_id">Well ID</option>
          <option value="status">Milestone</option>
        </select>
        <span className="tnum ml-auto text-xs text-ink-3" aria-live="polite">
          {rows.length.toLocaleString("en-GB")} of {wells.length.toLocaleString("en-GB")} wells
        </span>
      </div>

      <div className="border-b border-line px-5 py-2.5">
        <StatusLegend />
      </div>

      {rows.length === 0 ? (
        <Empty message="No wells match these filters." />
      ) : (
        <TableShell>
          <caption className="sr-only">
            Wells that failed at least one contractual milestone, with the first failure in
            priority order.
          </caption>
          <thead>
            <tr>
              <Th>Well</Th>
              <Th>First failure</Th>
              <Th align="right">Overrun</Th>
              <Th>Expected rig-on</Th>
              <Th align="right">Delayed activities</Th>
              <Th>FLAF</Th>
              <Th>Pegging</Th>
              <Th>Hook-up</Th>
            </tr>
          </thead>
          <tbody>
            {rows.map((well) => {
              const id = String(well.well_id);
              const variance = headlineVariance(well);
              const activity = activityByWell.get(id);
              return (
                <tr key={id} className="transition-colors hover:bg-surface-2">
                  <Td>
                    <Link
                      href={`/wells/${encodeURIComponent(id)}`}
                      className="tnum font-mono font-medium text-accent hover:text-accent-strong hover:underline"
                    >
                      {id}
                    </Link>
                  </Td>
                  {/* The one tinted chip on this row: the column a reader is actually scanning.
                      Labelled with the milestone alone - the column header already says these
                      are failures, so repeating "Slipped -" in every cell says nothing. */}
                  <Td>
                    <Pill
                      value={String(well.well_slippage_status ?? "")}
                      label={milestoneLabelOf(well.well_slippage_status)}
                    />
                  </Td>
                  <Td align="right">
                    <span
                      className={
                        varianceTone(variance) === "danger"
                          ? "font-semibold text-danger"
                          : varianceTone(variance) === "good"
                            ? "text-good"
                            : "text-ink-3"
                      }
                    >
                      {formatVariance(variance)}
                    </span>
                  </Td>
                  <Td className="text-ink-2">{formatDate(well.rig_on_deadline as string)}</Td>
                  <Td align="right">
                    {activity === undefined ? (
                      <span className="text-ink-3">{EMPTY}</span>
                    ) : (
                      <span className="font-semibold">{activity}</span>
                    )}
                  </Td>
                  <Td>
                    <Status value={well.flaf_status as string} />
                  </Td>
                  <Td>
                    <Status value={well.pegging_status as string} />
                  </Td>
                  <Td>
                    <Status value={well.hookup_status as string} />
                  </Td>
                </tr>
              );
            })}
          </tbody>
        </TableShell>
      )}
    </>
  );
}
