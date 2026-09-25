"""Synthesizer - explains what the queries returned.

The last step. It is given the rows and nothing else that could be mistaken for data: no schema,
no SQL. The brief it writes is the deliverable, so every figure in it has to be one the database
actually produced.

Counts are computed HERE, in Python, not by the model. The model sees a SAMPLE of rows, so any
total it worked out by reading them would be a total of the sample. Computing them here means the
headline figures are exact even when the preview is partial.
"""
from __future__ import annotations

from app.graph import queries, scenarios
from app.graph.nodes.verifier import render_rows
from app.graph.prompts import SYNTHESIZE_SYSTEM
from app.graph.state import SlippageState
from app.llm import chat
from app.observability import get_logger

log = get_logger()

# Enough rows to describe the picture without pushing the whole listing through the model.
EXPLAIN_ROWS = 40


def _clean(value: object) -> str:
    """Collapse whitespace in a value used as a label.

    WBS descriptions in this database carry embedded newlines (e.g. "Flowline\\nBoxup &
    Hydrotest"). Left alone they break any line-oriented block and the model reads the tail as
    a separate item.
    """
    return " ".join(str(value).split())


def _index(columns: list[str], name: str) -> int:
    names = [str(c).lower() for c in (columns or [])]
    return names.index(name) if name in names else -1


def _count_by(columns: list[str], rows: list[list], column: str, title: str) -> str:
    index = _index(columns, column)
    if index < 0:
        return ""
    counts: dict[str, int] = {}
    for row in rows or []:
        value = row[index]
        key = "(not recorded)" if value is None else str(value)
        counts[key] = counts.get(key, 0) + 1
    if not counts:
        return ""
    lines = [title]
    for value, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        lines.append("  " + value + ": " + str(count))
    return "\n".join(lines)


def _top_wbs(columns: list[str], rows: list[list], limit: int = 8) -> str:
    """Which WBS groups carry the most RED work - the activity query's headline."""
    wbs_index = _index(columns, "wbs")
    risk_index = _index(columns, "schedule_risk")
    if wbs_index < 0 or risk_index < 0:
        return ""
    counts: dict[str, int] = {}
    for row in rows or []:
        if str(row[risk_index]) != queries.RISK_RED:
            continue
        wbs = row[wbs_index]
        key = "(unmapped)" if wbs in (None, "") else _clean(wbs)
        counts[key] = counts.get(key, 0) + 1
    if not counts:
        return "RED TASKS BY WBS: none."
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]
    lines = ["RED TASKS BY WBS (exact counts over every returned row):"]
    for wbs, count in ranked:
        lines.append("  " + wbs + ": " + str(count))
    return "\n".join(lines)


def _per_well_activity(columns: list[str], rows: list[list], limit: int = 20) -> str:
    """Per well: how many DISTINCT activity codes are delayed, and on which WBS.

    Counts distinct activity codes, never task rows. One well runs the same activity many
    times (business_rules §3), so counting rows would report a well with one activity repeated
    forty times as forty delayed activities. The WBS is carried alongside because an activity
    code means nothing to a reader on its own (milestone_rules §5.11).

    Computed here rather than by the model: it only sees a sample of rows, so anything it
    counted would be a count of that sample.
    """
    well_i = _index(columns, "well_id")
    act_i = _index(columns, "activity_code")
    wbs_i = _index(columns, "wbs")
    if well_i < 0 or act_i < 0:
        return ""

    UNMAPPED = "(unmapped activity)"

    # well -> wbs -> set of activity codes
    per_well: dict[str, dict[str, set[str]]] = {}
    for row in rows or []:
        well = "(not recorded)" if row[well_i] is None else str(row[well_i])
        act = row[act_i]
        if act in (None, ""):
            act = UNMAPPED
        act = _clean(act)
        wbs = row[wbs_i] if wbs_i >= 0 else None
        wbs = "(unmapped WBS)" if wbs in (None, "") else _clean(wbs)
        per_well.setdefault(well, {}).setdefault(wbs, set()).add(act)

    if not per_well:
        return ""

    def total(well: str) -> int:
        """Distinct real activity codes. The unmapped placeholder is NOT one of them.

        Counting it adds a phantom +1 - business_rules §3 states this outright about the same
        pattern on WBS, and the SQL summary excludes it, so including it here would make the
        two disagree by one on any well with unmapped late work.
        """
        codes = {a for group in per_well[well].values() for a in group}
        return len(codes - {UNMAPPED})

    ranked = sorted(per_well, key=lambda w: (-total(w), w))
    lines = [
        "DELAYED ACTIVITY PER WELL (exact over every returned row - DISTINCT activity codes, "
        "not task rows; use these figures, do not recount):"
    ]
    for well in ranked[:limit]:
        lines.append("  well " + well + ": " + str(total(well)) + " delayed activity code(s)")
        for wbs, codes in sorted(per_well[well].items(), key=lambda kv: (-len(kv[1]), kv[0])):
            lines.append("      " + wbs + " -> " + ", ".join(sorted(codes)))
    if len(ranked) > limit:
        lines.append(
            "  ... and " + str(len(ranked) - limit) + " more well(s) with delayed activity, "
            "not listed here."
        )
    lines.append(
        "  TOTAL: " + str(len(ranked)) + " well(s) carry at least one delayed activity in the "
        "returned rows."
    )
    return "\n".join(lines)


def _data_quality(columns: list[str], rows: list[list]) -> str:
    """How many rows carry the data-quality status on each milestone."""
    flagged: list[str] = []
    for scenario in scenarios.SCENARIOS:
        column = scenario.prefix + "_status"
        index = _index(columns, column)
        if index < 0:
            continue
        count = sum(
            1 for row in rows or [] if str(row[index]) == scenarios.STATUS_DATA_QUALITY
        )
        if count:
            flagged.append("  " + column + ": " + str(count) + " well(s)")
    if not flagged:
        return "DATA QUALITY: no milestone reported the data-quality status."
    return "DATA QUALITY (the expected date the deadline needs is absent):\n" + "\n".join(flagged)


def _render_result(key: str, result: dict, has_summary: bool = False) -> str:
    """One query's rows, exact counts, and an honest note when it is not trustworthy."""
    spec = queries.QUERIES_BY_KEY.get(key)
    label = spec.label if spec else key
    columns = result.get("columns") or []
    rows = result.get("rows") or []

    if not columns:
        reason = result.get("error") or result.get("feedback") or "no result was produced"
        return (
            "### " + label + " - NO RESULT\n"
            "This query could not be produced. Reason: " + str(reason) + "\n"
            "Say so plainly in the brief. Do NOT describe this area as clear or on track - it "
            "was not measured."
        )

    parts = [
        "### " + label + " - " + str(len(rows)) + " row(s)"
        + (" (CAPPED: more matched than are shown)" if result.get("truncated") else ""),
        render_rows(columns, rows, limit=EXPLAIN_ROWS),
    ]

    if key == "well_slippage":
        tally = _count_by(
            columns, rows, "well_slippage_status",
            "EXACT COUNTS by headline milestone (every returned row - use these, do not recount):",
        )
        if tally:
            parts.append(tally)
        parts.append(_data_quality(columns, rows))
    elif key == "activity_delay":
        for column, title in (
            ("schedule_risk", "EXACT COUNTS by schedule risk (every returned row):"),
            ("end_status", "EXACT COUNTS by end status (every returned row):"),
            ("execution_status", "EXACT COUNTS by execution state (every returned row):"),
        ):
            tally = _count_by(columns, rows, column, title)
            if tally:
                parts.append(tally)
        top = _top_wbs(columns, rows)
        if top:
            parts.append(top)
        # The per-well rollup is NOT computed here when the summary query ran: that query does
        # the same counting in SQL, over every task rather than over whatever survived the row
        # cap. This Python rollup stays only as the fallback for when the summary is missing.
        if not has_summary:
            per_well = _per_well_activity(columns, rows)
            if per_well:
                parts.append(per_well)

    if not result.get("verified"):
        reason = result.get("feedback", "")
        parts.append(
            "⚠ NOT VERIFIED: the independent review did not approve this query"
            + (" - " + reason if reason else "")
            + ". Say plainly that these particular figures are indicative and unverified."
        )
    return "\n\n".join(parts)


def synthesize_node(state: SlippageState) -> dict:
    results = state.get("results") or {}

    usable = {k: v for k, v in results.items() if (v.get("columns") or v.get("rows"))}
    if not usable:
        log.warning("synthesize: nothing to explain")
        reasons = [
            "- " + k + ": " + str(v.get("error") or v.get("feedback") or "no result")
            for k, v in results.items()
        ]
        return {
            "explanation": (
                "No listing could be produced. Every query was rejected or failed before any "
                "result could be trusted.\n\n" + "\n".join(reasons)
            )
        }

    # When the summary query produced rows, it is the authoritative per-well source: it
    # counts in SQL over every task, not over whatever survived the row cap.
    summary = results.get("activity_summary") or {}
    has_summary = bool(summary.get("rows"))
    parts = [
        _render_result(key, results[key], has_summary=has_summary)
        for key in queries.DEFAULT_KEYS
        if key in results
    ]
    parts.append("Write the brief now, covering every query above.")

    explanation = chat(SYNTHESIZE_SYSTEM, "\n\n".join(parts), agent="synthesize")
    verified = [k for k, v in results.items() if v.get("verified")]
    log.info(
        "synthesize: brief written (%d chars), verified: %s",
        len(explanation), ", ".join(verified) or "none",
    )
    return {"explanation": explanation.strip()}
