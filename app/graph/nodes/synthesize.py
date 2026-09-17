"""Synthesizer - explains what the slippage query returned.

Step 6, and the last. It is given the rows and nothing else that could be mistaken for data: no
schema, no SQL. The brief it writes is the deliverable, so every figure in it has to be one the
database actually produced.
"""
from __future__ import annotations

from app.graph import scenarios
from app.graph.nodes.verifier import render_rows
from app.graph.prompts import SYNTHESIZE_SYSTEM
from app.graph.state import SlippageState
from app.llm import chat
from app.observability import get_logger

log = get_logger()

# Enough rows to describe the fleet without pushing the whole listing through the model. Counts
# are computed below rather than read off these rows, so the sample never becomes the figures.
EXPLAIN_ROWS = 60


def _tally(columns: list[str], rows: list[list]) -> str:
    """Counts computed in Python, not by the model.

    The model is shown a SAMPLE of rows, so any total it worked out by reading them would be a
    total of the sample. Computing them here means the headline figures are exact even when the
    preview is partial.
    """
    names = [str(c).lower() for c in (columns or [])]
    if "well_slippage_status" not in names:
        return ""

    index = names.index("well_slippage_status")
    counts: dict[str, int] = {}
    for row in rows or []:
        value = row[index]
        key = "(not recorded)" if value is None else str(value)
        counts[key] = counts.get(key, 0) + 1
    if not counts:
        return ""

    lines = ["EXACT COUNTS (computed from every returned row - use these figures, do not recount):"]
    for status, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        lines.append("  " + status + ": " + str(count))
    lines.append("  TOTAL: " + str(len(rows or [])))
    return "\n".join(lines)


def _data_quality(columns: list[str], rows: list[list]) -> str:
    """How many wells carry the data-quality status on each milestone."""
    names = [str(c).lower() for c in (columns or [])]
    flagged: list[str] = []
    for scenario in scenarios.SCENARIOS:
        column = scenario.prefix + "_status"
        if column not in names:
            continue
        index = names.index(column)
        count = sum(
            1
            for row in rows or []
            if str(row[index]) == scenarios.STATUS_DATA_QUALITY
        )
        if count:
            flagged.append("  " + column + ": " + str(count) + " well(s)")
    if not flagged:
        return "DATA QUALITY: no milestone reported the data-quality status."
    return "DATA QUALITY (the expected date the deadline needs is absent):\n" + "\n".join(flagged)


def synthesize_node(state: SlippageState) -> dict:
    columns = state.get("columns", [])
    rows = state.get("rows", [])
    verified = bool(state.get("verify_ok"))

    # Nothing survived. Say so plainly rather than writing a confident brief about nothing -
    # and say why, which the rejected attempts do record.
    if not columns and not rows:
        rejected = state.get("rejected", [])
        reason = rejected[-1].get("feedback", "") if rejected else ""
        note = (
            "No slippage listing could be produced. The query was rejected before any result "
            "could be trusted."
        )
        if reason:
            note += "\n\nLast reason given: " + reason
        log.warning("synthesize: nothing to explain")
        return {"explanation": note}

    parts = [
        "SLIPPAGE LISTING - " + str(len(rows)) + " well(s) returned"
        + (" (CAPPED: more wells matched than are shown)" if state.get("truncated") else "")
        + ":",
        render_rows(columns, rows, limit=EXPLAIN_ROWS),
    ]
    tally = _tally(columns, rows)
    if tally:
        parts.append(tally)
    parts.append(_data_quality(columns, rows))

    if not verified:
        # The Synthesizer must know. Its brief has to carry the caveat, and the reason is the
        # only thing that makes the caveat actionable.
        reason = state.get("verify_feedback", "")
        parts.append(
            "⚠ NOT VERIFIED: the independent review did not approve this query"
            + (" - " + reason if reason else "")
            + ". Open your brief by saying plainly that these figures are indicative and have "
            "not been verified, then report them."
        )

    parts.append("Write the slippage brief now.")

    explanation = chat(SYNTHESIZE_SYSTEM, "\n\n".join(parts), temperature=0.2)
    log.info("synthesize: brief written (%d chars, verified=%s)", len(explanation), verified)
    return {"explanation": explanation.strip()}
