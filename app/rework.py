"""Record every rejection and execution failure, for later prompt work.

WHY THIS EXISTS. Rework is the single largest cost in a run - measured at 38% of wall-clock on
a three-query run, six SQL-author calls for three queries. And it is not random: the same
handful of mistakes recur. Over one working session the varchar/int well-key cast cost an
attempt on five separate runs, and the "(unmapped) counted as an activity code" defect was
rejected five times. Each was re-discovered from scratch every time, because nothing kept a
record.

This writes one JSON object per failure to logs/rework.jsonl. JSONL rather than prose so the
file can be counted, grouped and sorted later - the whole point is to answer "which mistake
costs us most?" rather than "what went wrong that one time".

It is append-only and never read during a run: nothing here can change what the agents do.
A failure to write is swallowed, because a diagnostic aid must never be why a run fails.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone

from app.observability import get_logger

log = get_logger()

# The three query pipelines run concurrently, so appends must not interleave. A partial
# line is worse than a missing one: it makes the whole corpus fail to parse at that point.
_WRITE_LOCK = threading.Lock()

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.environ.get("REWORK_LOG") or os.path.join(_ROOT, "logs", "rework.jsonl")

# One id per process, so every record from a run groups together. A run's records are
# interleaved with nothing else, but the id is what lets "how many attempts did THAT run take"
# be answered without relying on timestamps.
RUN_ID = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + str(os.getpid())

# The kinds of rework worth distinguishing. They have different fixes: a validation error means
# the prompt let something unsafe through, an execution error means the SQL was wrong about the
# database, a contract error means the columns drifted, and a verifier rejection means the SQL
# ran and returned data that was still wrong - the most expensive kind, and the most interesting.
KIND_VALIDATION = "validation_error"
KIND_EXECUTION = "execution_error"
KIND_CONTRACT = "contract_error"
KIND_VERIFY = "verify_reject"


def record(
    kind: str,
    query: str,
    reason: str,
    sql: str = "",
    attempt: int = 0,
    **extra,
) -> None:
    """Append one rework event. Never raises."""
    try:
        from app.config import settings
        from app.llm import tuning_for

        entry = {
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "run_id": RUN_ID,
            "kind": kind,
            "query": query,
            "attempt": attempt,
            # The reason is the payload. It is what a future prompt change has to answer, so it
            # is stored whole rather than truncated - these are a few hundred characters each.
            "reason": (reason or "").strip(),
            # The model and effort at the time. Rework rate is the main thing the effort setting
            # trades against, so a corpus without it cannot answer whether a change helped.
            "model": settings.openai_model,
            "effort": tuning_for("sql_author").effort,
            # The SQL that was rejected, so a recurring mistake can be recognised by shape and
            # not only by the reviewer's wording.
            "sql": sql or "",
            **extra,
        }
        os.makedirs(os.path.dirname(PATH), exist_ok=True)
        line = json.dumps(entry, ensure_ascii=False) + "\n"
        # Serialised: three query pipelines record concurrently, and two interleaved writes
        # would produce a half-line that makes the corpus unparseable from that point on.
        with _WRITE_LOCK:
            with open(PATH, "a", encoding="utf-8") as handle:
                handle.write(line)
    except Exception as exc:  # noqa: BLE001 - never let bookkeeping break a run
        log.debug("rework: could not record (%s)", exc)


def load() -> list[dict]:
    """Every recorded event. Bad lines are skipped rather than failing the whole read."""
    if not os.path.exists(PATH):
        return []
    out: list[dict] = []
    with open(PATH, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


# Recurring mistakes worth naming, so the report groups by CAUSE rather than by the reviewer's
# exact wording - the same defect gets described a dozen different ways across runs.
#
# Each entry is (label, the substrings that identify it). Add to this as new patterns emerge;
# anything unmatched is reported under "other", which is where the next pattern will show up.
_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("well-key varchar/int cast", ("conversion failed", "0000f", "data type int")),
    ("unmapped counted as a code", ("unmapped", "phantom", "false distinct")),
    ("history not reduced to latest", ("latest", "row_number", "many rows per", "de-duplicate")),
    ("missing-data guard absent", ("data_quality", "missing-data", "is null first", "guard")),
    ("variance 0 instead of NULL", ("else 0", "not due yet", "instead of null")),
    ("invented placeholder date", ("1900", "placeholder", "sentinel")),
    ("wrong ordering", ("order by", "ordering", "urgency")),
    ("invalid column or table", ("invalid column", "invalid object")),
    ("output contract mismatch", ("contract column", "not in the contract")),
    ("well not bound as parameter", ("parameter marker", "bound parameter")),
    ("crew from the wrong lookup", ("crew_code", "new_crew_code")),
    ("unreachable branch", ("unreachable", "always null", "population filter guarantees")),
)


def classify(reason: str) -> str:
    """The named cause a reason matches, or 'other'."""
    low = (reason or "").lower()
    for label, needles in _PATTERNS:
        if sum(1 for n in needles if n in low) >= 1:
            return label
    return "other"


def report(limit: int = 12) -> str:
    """A summary of what rework has actually cost, worst first.

    This is the point of the whole module: it turns a pile of rejections into the one question
    worth acting on - which mistake recurs enough to be worth a prompt change, a deterministic
    check, or a line in the rule documents.
    """
    events = load()
    if not events:
        return (
            "No rework recorded yet (" + PATH + ").\n"
            "Run the pipeline; every rejection and execution failure is appended there."
        )

    runs = {e.get("run_id") for e in events}
    by_cause: dict[str, list[dict]] = {}
    by_kind: dict[str, int] = {}
    for e in events:
        by_cause.setdefault(classify(e.get("reason", "")), []).append(e)
        by_kind[e.get("kind", "?")] = by_kind.get(e.get("kind", "?"), 0) + 1

    lines = [
        "REWORK REPORT  -  " + str(len(events)) + " event(s) across " + str(len(runs)) + " run(s)",
        "  file: " + PATH,
        "",
        "BY KIND",
    ]
    for kind, n in sorted(by_kind.items(), key=lambda kv: -kv[1]):
        lines.append("  " + str(n).rjust(4) + "  " + kind)

    lines += ["", "BY CAUSE (worst first - these are the prompt-change candidates)"]
    for cause, group in sorted(by_cause.items(), key=lambda kv: -len(kv[1]))[:limit]:
        queries = sorted({e.get("query", "?") for e in group})
        affected = len({e.get("run_id") for e in group})
        lines.append(
            "  " + str(len(group)).rjust(4) + "  " + cause
            + "   (" + str(affected) + " run(s); " + ", ".join(queries) + ")"
        )
        # One real example, so the cause is recognisable rather than just labelled.
        example = group[0].get("reason", "")
        if example:
            lines.append("        e.g. " + example[:150].replace("\n", " "))

    lines += [
        "",
        "A cause appearing in most runs is no longer bad luck: it is either a missing "
        "deterministic check (see app/graph/sqlcheck.py) or a rule the prompt states too "
        "quietly to land.",
    ]
    return "\n".join(lines)
