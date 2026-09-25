"""Suggestion agent - why is a well (or one of its tasks) late, and how could it be recovered?

Two questions, one agent:
  * suggest_well(): the button above the task list - why is this WELL delayed, and how to overcome it.
  * suggest():      the button on a late task - is THIS task late because of another delay, and how
                    could it be recovered.

⚠ THE ONE PLACE THE API CALLS A MODEL. Every other endpoint serves SQL the pipeline verified, and
that stays true: this module never writes or runs SQL of its own. It reads the same frozen queries
the dashboard does and hands a model the evidence. Three things keep a model's opinion from being
mistaken for a verified figure:

  * THE EVIDENCE IS SELECTED HERE, IN PYTHON. The task, the other late tasks on the well, the
    well's milestones and the crews of the matching type all come from verified queries. The model
    reasons over them; it does not fetch or count anything.
  * THE ANSWER IS RETURNED BESIDE ITS EVIDENCE, so the page shows the two apart and a reader can
    check one against the other.
  * IT MAY ONLY NAME CREWS IT WAS GIVEN. A crew id in an answer that is not among the candidates is
    flagged on the action (`crew_unverified`) and in the caveats, never passed off as advice.

An answer is reused only while the data it rests on is unchanged (see _answer): each costs a
model call, and one whose data has not moved is not worth paying for twice - while one whose data
HAS moved must not be served beside newer figures.
"""
from __future__ import annotations

import hashlib
import json
import time
from concurrent.futures import Future
from datetime import datetime, timezone
from threading import Lock
from typing import Any

from api import service
from app.config import settings
from app.graph import queries, scenarios
from app.graph.prompts import ADVISOR_SYSTEM, ADVISOR_WELL_SYSTEM
from app.llm import chat
from app.observability import get_logger
from app.utils import extract_json

log = get_logger()

# How much the model sees. Enough to reason with; bounded so a well with a hundred late tasks does
# not become a hundred-row prompt on every click.
MAX_RELATED = 12
MAX_CANDIDATES = 8

# Stored answers, each with the fingerprint of the data it was reasoned over. No time limit: an
# answer is exactly as current as that data, so it expires when the data changes, not by the clock.
_cache: dict[tuple[str, str], tuple[str, dict]] = {}
# Answers being computed right now, keyed (cache key, data fingerprint). See _answer().
_inflight: dict[tuple[tuple[str, str], str], Future] = {}
_lock = Lock()

_VERDICTS = ("yes", "possibly", "no", "unknown")

# The task fields the model is shown. task_code stays so evidence can be cited unambiguously; the
# prompt tells the model to NAME the work by its WBS.
_TASK_FIELDS = (
    "task_code", "wbs", "activity_code", "crew_code", "crew_id", "crew_type_id",
    "target_start", "target_end", "actual_start", "actual_end",
    "start_status", "start_variance_days", "end_status", "end_variance_days",
    "execution_status", "schedule_risk", "progress_percent",
)


class AdvisorUnavailable(RuntimeError):
    """The agent cannot run at all - no model is configured."""


class TaskNotFound(LookupError):
    """The task is not among the well's current tasks.

    Its own class rather than a bare LookupError, which KeyError and IndexError also are: a bug
    raising one of those must surface as a failure, not be reported as "task not found".
    """


def invalidate() -> None:
    """Drop every stored answer. Called when a pipeline run finishes.

    No longer what expires an answer - the data fingerprint does that, including for runs started
    from the command line, which never reach this process. Kept because it costs nothing and a
    run that rewrote the SQL is a reasonable moment to start clean.
    """
    with _lock:
        _cache.clear()


def _canon(value: Any) -> Any:
    """An order-free form of a value, for fingerprinting: dict keys and list items sorted.

    ⚠ ORDER MUST NOT COUNT AS A CHANGE. The activity query returns tied rows (same risk, same
    variances, same dates) in a different order on each run - measured: three gathers of well
    35552 with no data change gave three different prompts. Fingerprinting the prompt text would
    have made every click look like new data and paid for a model call each time.
    """
    if isinstance(value, dict):
        return {str(k): _canon(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (list, tuple)):
        items = [_canon(v) for v in value]
        return sorted(items, key=lambda v: json.dumps(v, sort_keys=True, default=str))
    return value


def _fingerprint(*parts: Any) -> str:
    """A hash of the DATA an answer rests on - rows, not the prompt rendered from them."""
    blob = json.dumps([_canon(p) for p in parts], sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _crews_for(tasks: list[dict], crews: list[dict] | None) -> list[dict]:
    """The crew rows an answer about these tasks can depend on, for its fingerprint.

    The crews assigned to them, and every crew of a crew type they need - the candidates are
    drawn from those. A change to a crew of an unrelated type cannot alter the answer, so it must
    not expire it.
    """
    if not crews:
        return []
    types = {_key(t.get("crew_type_id")) for t in tasks} - {None}
    ids = {_key(t.get("crew_id")) for t in tasks} - {None}
    return [c for c in crews if _key(c.get("crew_type_id")) in types or _key(c.get("crew_id")) in ids]


def _answer(key: tuple[str, str], fresh: bool, gather, ask) -> dict:
    """Serve a stored answer while its data is unchanged, join one already being computed, or ask.

    EXPIRY IS BY DATA, NOT BY TIME. `gather()` runs the verified queries on every request - the
    cheap part, a few seconds against ~35s for the model - and returns (evidence, prompt,
    fingerprint). A stored answer is served only while the fingerprint is the same, so an answer
    is never older than the figures beside it, and one whose data has not moved is never paid
    for twice. It replaces a 6-hour TTL plus "clear on a dashboard-started run", which let an
    answer outlive the data by hours: new daily records cleared nothing, and neither did a run
    started from the command line.

    ⚠ WHY THE JOIN, AND WHY HERE. A stored answer only helps AFTER it exists, and asking takes
    ~35s. Until then every request for the same key missed it and paid for its own call -
    measured: one click in `next dev` logged two advisor calls for the same well in the same
    second (React Strict Mode runs the panel's fetch effect twice), both billed, one thrown away.
    Production has no Strict Mode, but the same gap is hit by a double-click, by closing and
    reopening a panel mid-answer, and by two people on one well. Only the server sees all of
    those callers, so the fix is here rather than in the page. A call is joined only when it is
    for the SAME data: a request that sees newer data waits for nothing and asks about that.

    `fresh` skips the stored answer, not a call in flight: that call started after the stored
    answer was refused, so it is already the fresh answer being asked for.

    A failure is not stored: every waiting caller gets the same exception, and the next request
    tries again. A missing task raises from gather(), before anything is in flight. No wait
    timeout is needed, because the call being waited on is bounded by LLM_TIMEOUT.
    """
    evidence, prompt, version = gather()
    flight = (key, version)
    with _lock:
        hit = _cache.get(key)
        if not fresh and hit and hit[0] == version:
            return {**hit[1], "cached": True}
        pending = _inflight.get(flight)
        leader = pending is None
        if leader:
            pending = _inflight[flight] = Future()

    if not leader:
        log.info("advisor: %s is already being answered - waiting for that call, not starting another",
                 "/".join(key))
        # Generated for this request's moment, so it is NOT reported as cached.
        return dict(pending.result())
    if hit and not fresh:
        log.info("advisor: %s - the data has changed since the stored answer, asking again", "/".join(key))

    try:
        result = ask(evidence, prompt)
    except BaseException as exc:
        with _lock:
            _inflight.pop(flight, None)
        pending.set_exception(exc)
        raise
    with _lock:
        _cache[key] = (version, result)
        _inflight.pop(flight, None)
    pending.set_result(result)
    return result


# -- Small helpers ----------------------------------------------------------------


def _clean(value: Any) -> Any:
    """Collapse whitespace in text - WBS names carry embedded newlines - and leave the rest alone."""
    return " ".join(value.split()) if isinstance(value, str) else value


def _day(value: Any) -> str | None:
    """A date as YYYY-MM-DD: comparable as a string, and compact in a prompt."""
    if value in (None, ""):
        return None
    return str(value)[:10]


def _same(a: Any, b: Any) -> bool:
    """Id equality across the shapes the JSON layer produces: 12, "12" and 12.0 are one crew."""
    if a in (None, "") or b in (None, ""):
        return False
    try:
        return float(a) == float(b)
    except (TypeError, ValueError):
        return str(a).strip() == str(b).strip()


def _count(value: Any) -> float:
    """A count for sorting. Missing sorts last - unknown load is not zero load."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("inf")


def _as_list(value: Any) -> list:
    if isinstance(value, list):
        return value
    return [] if value in (None, "") else [value]


def _slim(task: dict) -> dict:
    return {f: _clean(task.get(f)) for f in _TASK_FIELDS if f in task}


def _is_late(task: dict) -> bool:
    risk = str(task.get("schedule_risk") or "")
    return risk == queries.RISK_RED or risk.startswith("AMBER")


# -- Evidence -----------------------------------------------------------------------


def _related(task: dict, tasks: list[dict]) -> tuple[list[dict], list[dict]]:
    """Other LATE tasks on the well that could plausibly have held this one up.

    The data holds no dependency links, so this is timing only - and the prompt says so:
      * earlier: planned to FINISH on or before this task's planned START (its planned end when it
        has no planned start). Nearest first: the task due to finish just before this one was due
        to begin is the likeliest to have held it up.
      * same WBS: other late work in the same WBS, which usually shares a sequence and a crew type.
    """
    code = task.get("task_code")
    anchor = _day(task.get("target_start")) or _day(task.get("target_end"))
    wbs = _clean(task.get("wbs"))
    others = [t for t in tasks if t.get("task_code") != code and _is_late(t)]

    earlier: list[dict] = []
    if anchor:
        earlier = [t for t in others if _day(t.get("target_end")) and _day(t.get("target_end")) <= anchor]
        earlier.sort(key=lambda t: _day(t.get("target_end")) or "", reverse=True)
    earlier = earlier[:MAX_RELATED]

    shown = {t.get("task_code") for t in earlier}
    same_wbs = [
        t for t in others
        if wbs and _clean(t.get("wbs")) == wbs and t.get("task_code") not in shown
    ][:MAX_RELATED]
    return [_slim(t) for t in earlier], [_slim(t) for t in same_wbs]


def _well(well_id: str) -> dict:
    """The well's contractual milestones.

    `listed` is three-valued on purpose. False means the well is not in the slippage listing, which
    holds only wells that FAILED a milestone - so none has failed. None means the listing could not
    be read, so nothing is known. Collapsing the two would tell the agent a well is on track when
    it was simply not measured.
    """
    try:
        rows = service.slipped_wells()
    except service.QueryUnavailable:
        return {"listed": None, "headline": None, "milestones": [],
                "note": "The well slippage listing has not been generated, so the milestones are unknown."}
    for row in rows:
        if _same(row.get("well_id"), well_id):
            return {
                "listed": True,
                "headline": row.get("well_slippage_status"),
                "milestones": [
                    {
                        "milestone": s.label,
                        "owner": s.owner,
                        "deadline": _day(row.get(s.prefix + "_deadline")),
                        "actual": _day(row.get(s.prefix + "_actual")),
                        "status": row.get(s.prefix + "_status"),
                        "variance_days": row.get(s.prefix + "_variance_days"),
                    }
                    for s in scenarios.SCENARIOS
                ],
                "note": None,
            }
    return {"listed": False, "headline": None, "milestones": [], "note": None}


def _crews(task: dict, crews: list[dict] | None) -> dict:
    """The crew side: the assigned crew's own load, and free crews of the SAME type.

    `crews` is the crew availability table, or None when it has not been generated. Passed in
    rather than read here so the caller fingerprints the same rows this reasons over.

    Crew type decides who can do the work - a crew of another type is not a substitute - so the
    candidates are filtered on crew_type_id before anything else, and the assigned crew is left out
    of its own candidate list.
    """
    out: dict[str, Any] = {
        "crew_type_id": task.get("crew_type_id"),
        "assigned_crew_id": task.get("crew_id"),
        "assigned": None,
        "available": [],
        "available_count": 0,
        "busy_count": 0,
        "type_total": 0,
        "note": None,
    }
    if "crew_type_id" not in task:
        # The frozen activity query predates the crew columns. It is only the API reading an old
        # file: the next pipeline run re-authors it, because its output contract changed.
        out["note"] = ("This well's task data predates the crew columns, so the task's crew type is "
                       "unknown. Re-run the analysis to regenerate the activity query.")
        return out
    if crews is None:
        out["note"] = "Crew availability has not been generated yet. Re-run the analysis to build it."
        return out

    out["assigned"] = next((c for c in crews if _same(c.get("crew_id"), task.get("crew_id"))), None)

    crew_type = task.get("crew_type_id")
    if crew_type in (None, ""):
        out["note"] = "This task records no crew type, so no crew can be matched to it."
        return out

    same_type = [c for c in crews if _same(c.get("crew_type_id"), crew_type)]
    free = [
        c for c in same_type
        if c.get("availability_status") == queries.CREW_AVAILABLE
        and not _same(c.get("crew_id"), task.get("crew_id"))
    ]
    # Most room first: least open work of its own, then fewest overdue, then most recently recorded
    # - a crew no one has recorded for months may not be working at all. Every listed crew holds
    # some open work (see queries.CREW_AVAILABILITY). Two stable sorts give that order.
    free.sort(key=lambda c: _day(c.get("latest_action_on")) or "", reverse=True)
    free.sort(key=lambda c: (_count(c.get("open_tasks")), _count(c.get("overdue_tasks"))))

    out["available"] = free[:MAX_CANDIDATES]
    out["available_count"] = len(free)
    out["busy_count"] = sum(1 for c in same_type if c.get("availability_status") != queries.CREW_AVAILABLE)
    out["type_total"] = len(same_type)
    if not same_type:
        out["note"] = "No crew of this type holds open work on any well still in progress."
    return out


# -- Prompt and reply ------------------------------------------------------------------


def _lines(rows: list[dict]) -> str:
    return "\n".join(json.dumps(r, ensure_ascii=False, default=str) for r in rows)


def _render(well_id: str, evidence: dict) -> str:
    well, crew = evidence["well"], evidence["crew"]
    parts = [
        "WELL " + well_id,
        "",
        "THE TASK IN QUESTION:",
        json.dumps(evidence["task"], ensure_ascii=False, default=str),
        "",
        "OTHER LATE TASKS ON THIS WELL PLANNED TO FINISH BEFORE THIS ONE STARTS "
        "(nearest first; timing only - the data holds no dependency links):",
        _lines(evidence["earlier_late_tasks"]) or "(none)",
        "",
        "OTHER LATE TASKS IN THE SAME WBS:",
        _lines(evidence["same_wbs_late_tasks"]) or "(none)",
        "",
    ]
    if well["listed"] is True:
        parts += ["WELL MILESTONES (headline: " + str(well["headline"]) + "):",
                  _lines(well["milestones"]), ""]
    elif well["listed"] is False:
        parts += ["WELL MILESTONES: this well is NOT in the slippage listing, which holds only wells "
                  "that failed a milestone - so no contractual milestone has failed.", ""]
    else:
        parts += ["WELL MILESTONES: unknown - " + str(well["note"]), ""]

    parts += [
        "ASSIGNED CREW (crew_id " + str(crew["assigned_crew_id"]) + ") - ITS WORKLOAD ACROSS THE FLEET:",
        json.dumps(crew["assigned"], ensure_ascii=False, default=str) if crew["assigned"]
        else "(not found in crew availability, or the task records no crew)",
        "",
        "AVAILABLE CREWS OF THIS TYPE (crew_type_id " + str(crew["crew_type_id"]) + "): "
        + str(crew["available_count"]) + " available, " + str(crew["busy_count"]) + " busy, of "
        + str(crew["type_total"]) + ". The best candidates - name ONLY these:",
        _lines(crew["available"]) or "(none)",
    ]
    if crew["note"]:
        parts += ["", "NOTE: " + crew["note"]]
    parts += ["", "Answer both questions now, as the JSON."]
    return "\n".join(parts)


def _normalise(parsed: Any, crew: dict) -> dict | None:
    """Coerce the reply into the shape the page renders, and flag any crew it was not given.

    None when the reply is not the JSON asked for - the caller then returns the raw text, so the
    page shows what the agent said rather than an empty panel.
    """
    if not isinstance(parsed, dict) or not (parsed.get("summary") or parsed.get("actions")):
        return None

    allowed = [c.get("crew_id") for c in crew.get("available") or []]
    caveats = [str(c) for c in _as_list(parsed.get("caveats"))]

    actions = []
    for item in _as_list(parsed.get("actions")):
        if not isinstance(item, dict):
            item = {"action": str(item)}
        crew_id = item.get("crew_id")
        if crew_id in ("", "null", "None"):
            crew_id = None
        entry: dict[str, Any] = {
            "action": str(item.get("action") or ""),
            "crew_id": crew_id,
            "rationale": str(item.get("rationale") or ""),
        }
        if crew_id is not None and not any(_same(crew_id, a) for a in allowed):
            entry["crew_unverified"] = True
            caveats.append(
                "Crew " + str(crew_id) + " was not among the available crews of this type the agent "
                "was given. Do not act on it without checking."
            )
        actions.append(entry)

    causes = []
    for item in _as_list(parsed.get("causes")):
        if isinstance(item, dict):
            causes.append({
                "cause": str(item.get("cause") or ""),
                "evidence": str(item.get("evidence") or ""),
                "confidence": str(item.get("confidence") or "").lower(),
            })
        else:
            causes.append({"cause": str(item), "evidence": "", "confidence": ""})

    verdict = str(parsed.get("caused_by_other_delay") or "unknown").strip().lower()
    return {
        "summary": str(parsed.get("summary") or ""),
        "caused_by_other_delay": verdict if verdict in _VERDICTS else "unknown",
        "causes": causes,
        "actions": actions,
        "checks": [str(c) for c in _as_list(parsed.get("checks"))],
        "caveats": caveats,
    }


# -- Entry point ----------------------------------------------------------------------


def suggest(well_id: str, task_code: str, fresh: bool = False) -> dict:
    """Advise on one late task. Reused while its data is unchanged, unless `fresh`."""
    if not settings.openai_api_key:
        raise AdvisorUnavailable("OPENAI_API_KEY is not set in .env, so the suggestion agent cannot run.")

    key = (str(well_id).strip(), str(task_code).strip())
    return _answer(key, fresh, lambda: _task_evidence(key), lambda ev, prompt: _task_answer(key, ev, prompt))


def _task_evidence(key: tuple[str, str]) -> tuple[dict, str, str]:
    """(evidence, prompt, data fingerprint) for one task. Database reads only - no model."""
    tasks = service.activity_for_well(key[0])
    task = next((t for t in tasks if str(t.get("task_code") or "").strip() == key[1]), None)
    if task is None:
        raise TaskNotFound("Task " + key[1] + " is not among well " + key[0] + "'s current tasks.")
    try:
        crews = service.crew_availability()
    except service.QueryUnavailable:
        crews = None

    earlier, same_wbs = _related(task, tasks)
    well = _well(key[0])
    evidence = {
        "task": _slim(task),
        "earlier_late_tasks": earlier,
        "same_wbs_late_tasks": same_wbs,
        "well": well,
        "crew": _crews(task, crews),
    }
    # Every task on the well, not just the ones shown: the related-task lists are cut to a limit,
    # and which rows make the cut depends on the rest.
    version = _fingerprint(ADVISOR_SYSTEM, tasks, well, _crews_for([task], crews))
    return evidence, _render(key[0], evidence), version


def _task_answer(key: tuple[str, str], evidence: dict, prompt: str) -> dict:
    """Ask the model once about one task, over evidence already gathered."""
    started = time.perf_counter()
    raw = chat(ADVISOR_SYSTEM, prompt, agent="advisor")
    advice = _normalise(extract_json(raw, default={}), evidence["crew"])
    log.info(
        "advisor: well %s task %s answered in %.1fs (%s, %d candidate crew(s))",
        key[0], key[1], time.perf_counter() - started,
        "parsed" if advice else "UNPARSED - returning raw text", len(evidence["crew"]["available"]),
    )

    result = {
        "well_id": key[0],
        "task_code": key[1],
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model": settings.openai_model,
        "cached": False,
        "advice": advice,
        # Only when the reply could not be read as the JSON asked for.
        "raw": None if advice else (raw or "").strip(),
        "evidence": evidence,
    }
    return result


# -- Well-level suggestion --------------------------------------------------------------

MAX_URGENT = 15            # most urgent late tasks shown in full
MAX_GROUPS = 8             # WBS groups and crew-type groups
MAX_TYPE_CANDIDATES = 4    # free crews listed per crew type
MAX_ASSIGNED = 6           # assigned crews whose fleet load is shown, per crew type

# The cache is keyed (well, task); this sentinel is the "task" of a well-level answer. Task codes
# never start with an underscore, so it cannot collide with one.
_WELL_KEY = "__well__"
_WELL_VERDICTS = ("yes", "at_risk", "no", "unknown")


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _key(value: Any) -> str | None:
    """One comparable form of an id: 287, "287" and 287.0 all become "287"; empty becomes None."""
    if value in (None, ""):
        return None
    n = _number(value)
    return str(int(n)) if n is not None and n == int(n) else str(value).strip()


def _nullish(value: Any) -> Any:
    return None if value in (None, "", "null", "None") else value


def _worst(values) -> float | None:
    """The largest overrun. Nulls are skipped, never read as 0 - unmeasured is not on time."""
    nums = [n for n in (_number(v) for v in values) if n is not None]
    return max(nums) if nums else None


def _tally(tasks: list[dict]) -> dict:
    def count(test) -> int:
        return sum(1 for t in tasks if test(t))

    return {
        "total": len(tasks),
        "late": count(_is_late),
        "red": count(lambda t: t.get("schedule_risk") == queries.RISK_RED),
        "amber": count(lambda t: str(t.get("schedule_risk") or "").startswith("AMBER")),
        "not_started_late": count(lambda t: t.get("execution_status") == queries.EXEC_NOT_STARTED_LATE),
        "in_progress": count(lambda t: t.get("execution_status") == queries.EXEC_IN_PROGRESS),
        "completed": count(lambda t: t.get("execution_status") == queries.EXEC_COMPLETED),
        "due_today": count(lambda t: t.get("end_status") == queries.END_DUE_TODAY),
    }


def _late_by_wbs(late: list[dict]) -> list[dict]:
    """Where the late work sits, worst WBS first."""
    groups: dict[str, dict] = {}
    for t in late:
        name = _clean(t.get("wbs")) or "(unmapped)"
        g = groups.setdefault(name, {
            "wbs": name, "late_tasks": 0, "red": 0, "not_started_late": 0,
            "_overruns": [], "crew_type_ids": [],
        })
        g["late_tasks"] += 1
        g["red"] += int(t.get("schedule_risk") == queries.RISK_RED)
        g["not_started_late"] += int(t.get("execution_status") == queries.EXEC_NOT_STARTED_LATE)
        g["_overruns"].append(t.get("end_variance_days"))
        crew_type = _key(t.get("crew_type_id"))
        if crew_type and crew_type not in g["crew_type_ids"]:
            g["crew_type_ids"].append(crew_type)
    out = []
    for g in groups.values():
        g["worst_overrun_days"] = _worst(g.pop("_overruns"))
        out.append(g)
    out.sort(key=lambda g: (-g["red"], -g["late_tasks"], g["wbs"]))
    return out[:MAX_GROUPS]


def _late_by_crew_type(late: list[dict], crews: list[dict] | None) -> list[dict]:
    """The late work grouped by crew TYPE - the unit that decides who can take it over - with, for
    each type, the fleet load of the crews assigned to it and the best free crews of that type.

    Late tasks with no crew type recorded are kept as their own group, so the answer can say how
    much of the late work cannot be matched to any crew at all.
    """
    groups: dict[str | None, dict] = {}
    for t in late:
        crew_type = _key(t.get("crew_type_id"))
        g = groups.setdefault(crew_type, {
            "crew_type_id": crew_type, "late_tasks": 0, "_overruns": [], "wbs": [],
            "_crew_ids": [], "unassigned_late_tasks": 0,
        })
        g["late_tasks"] += 1
        g["_overruns"].append(t.get("end_variance_days"))
        wbs = _clean(t.get("wbs"))
        if wbs and wbs not in g["wbs"]:
            g["wbs"].append(wbs)
        crew = _key(t.get("crew_id"))
        if crew is None:
            g["unassigned_late_tasks"] += 1
        elif crew not in g["_crew_ids"]:
            g["_crew_ids"].append(crew)

    out = []
    for crew_type, g in groups.items():
        assigned_ids = set(g.pop("_crew_ids"))
        entry = {
            **g,
            "worst_overrun_days": _worst(g.pop("_overruns")),
            "assigned_crews": [],
            "available_count": 0,
            "busy_count": 0,
            "type_total": 0,
            "candidates": [],
        }
        entry.pop("_overruns", None)
        if crews is not None and crew_type is not None:
            same_type = [c for c in crews if _key(c.get("crew_type_id")) == crew_type]
            entry["assigned_crews"] = [
                c for c in crews if _key(c.get("crew_id")) in assigned_ids
            ][:MAX_ASSIGNED]
            free = [
                c for c in same_type
                if c.get("availability_status") == queries.CREW_AVAILABLE
                and _key(c.get("crew_id")) not in assigned_ids
            ]
            # Same ranking as the task-level agent: most room, then most recently recorded.
            free.sort(key=lambda c: _day(c.get("latest_action_on")) or "", reverse=True)
            free.sort(key=lambda c: (_count(c.get("open_tasks")), _count(c.get("overdue_tasks"))))
            entry["candidates"] = free[:MAX_TYPE_CANDIDATES]
            entry["available_count"] = len(free)
            entry["busy_count"] = sum(
                1 for c in same_type if c.get("availability_status") != queries.CREW_AVAILABLE
            )
            entry["type_total"] = len(same_type)
        out.append(entry)

    # Most late work first; the "no crew type recorded" group last, whatever its size.
    out.sort(key=lambda g: (g["crew_type_id"] is None, -g["late_tasks"]))
    return out[:MAX_GROUPS]


def _crew_table(tasks: list[dict]) -> tuple[list[dict] | None, str | None]:
    """Crew availability, or None with the reason it cannot be used."""
    if tasks and "crew_type_id" not in tasks[0]:
        return None, ("This well's task data predates the crew columns, so late work cannot be "
                      "matched to crews. Re-run the analysis to regenerate the activity query.")
    try:
        return service.crew_availability(), None
    except service.QueryUnavailable:
        return None, "Crew availability has not been generated yet. Re-run the analysis to build it."


def _render_well(well_id: str, evidence: dict) -> str:
    well = evidence["well"]
    parts = ["WELL " + well_id, ""]
    if well["listed"] is True:
        parts += ["CONTRACTUAL MILESTONES (headline: " + str(well["headline"]) + "):",
                  _lines(well["milestones"]), ""]
    elif well["listed"] is False:
        parts += ["CONTRACTUAL MILESTONES: this well is NOT in the slippage listing, which holds only "
                  "wells that failed a milestone - so no contractual milestone has failed.", ""]
    else:
        parts += ["CONTRACTUAL MILESTONES: unknown - " + str(well["note"]), ""]

    parts += [
        "TASK TALLY (latest record per task):",
        json.dumps(evidence["tasks"]),
        "",
        "LATE WORK BY WBS (worst first):",
        _lines(evidence["late_by_wbs"]) or "(no late tasks)",
        "",
        "LATE WORK BY CREW TYPE - with the assigned crews' fleet load and the best FREE crews of the "
        "same type (name ONLY these candidates, under their own type):",
        _lines(evidence["late_by_crew_type"]) or "(no late tasks)",
        "",
        "MOST URGENT LATE TASKS (most urgent first):",
        _lines(evidence["most_urgent"]) or "(none)",
    ]
    if evidence["crew_note"]:
        parts += ["", "NOTE: " + evidence["crew_note"]]
    parts += ["", "Answer both questions now, as the JSON."]
    return "\n".join(parts)


def _normalise_well(parsed: Any, evidence: dict) -> dict | None:
    """Coerce the reply into the shape the page renders, flagging crews and types it was not given."""
    if not isinstance(parsed, dict) or not (parsed.get("description") or parsed.get("actions")):
        return None

    groups = evidence["late_by_crew_type"]
    known_types = {g["crew_type_id"] for g in groups if g["crew_type_id"] is not None}
    known_crews = {
        _key(c.get("crew_id")) for g in groups for c in g["candidates"] + g["assigned_crews"]
    }
    caveats = [str(c) for c in _as_list(parsed.get("caveats"))]

    actions = []
    for index, item in enumerate(_as_list(parsed.get("actions")), 1):
        if not isinstance(item, dict):
            item = {"action": str(item)}
        crew_id, crew_type = _nullish(item.get("crew_id")), _nullish(item.get("crew_type_id"))
        priority = item.get("priority")
        entry: dict[str, Any] = {
            "priority": priority if isinstance(priority, (int, float)) else index,
            "action": str(item.get("action") or ""),
            "crew_type_id": crew_type,
            "crew_id": crew_id,
            "rationale": str(item.get("rationale") or ""),
        }
        if crew_id is not None and _key(crew_id) not in known_crews:
            entry["crew_unverified"] = True
            caveats.append("Crew " + str(crew_id) + " was not among the crews the agent was given. "
                           "Do not act on it without checking.")
        if crew_type is not None and _key(crew_type) not in known_types:
            entry["type_unverified"] = True
            caveats.append("Crew type " + str(crew_type) + " does not carry any of this well's late "
                           "work in the evidence. Check before acting.")
        actions.append(entry)

    reasons = []
    for item in _as_list(parsed.get("why_delayed")):
        if isinstance(item, dict):
            reasons.append({
                "reason": str(item.get("reason") or ""),
                "evidence": str(item.get("evidence") or ""),
                "owner": str(item.get("owner") or "unknown"),
                "confidence": str(item.get("confidence") or "").lower(),
            })
        else:
            reasons.append({"reason": str(item), "evidence": "", "owner": "unknown", "confidence": ""})

    verdict = str(parsed.get("delayed") or "unknown").strip().lower()
    return {
        "description": str(parsed.get("description") or ""),
        "delayed": verdict if verdict in _WELL_VERDICTS else "unknown",
        "why_delayed": reasons,
        "actions": actions,
        "checks": [str(c) for c in _as_list(parsed.get("checks"))],
        "caveats": caveats,
    }


def suggest_well(well_id: str, fresh: bool = False) -> dict:
    """Why is this well delayed, and how could it be overcome. Reused while its data is unchanged."""
    if not settings.openai_api_key:
        raise AdvisorUnavailable("OPENAI_API_KEY is not set in .env, so the suggestion agent cannot run.")

    key = (str(well_id).strip(), _WELL_KEY)
    return _answer(key, fresh, lambda: _well_evidence(key), lambda ev, prompt: _well_answer(key, ev, prompt))


def _well_evidence(key: tuple[str, str]) -> tuple[dict, str, str]:
    """(evidence, prompt, data fingerprint) for the well-level question. Database reads only."""
    # Ordered most urgent first by the verified query itself, so the head of `late` is the worst.
    tasks = service.activity_for_well(key[0])
    late = [t for t in tasks if _is_late(t)]
    crews, crew_note = _crew_table(tasks)
    well = _well(key[0])
    evidence = {
        "well": well,
        "tasks": _tally(tasks),
        "late_by_wbs": _late_by_wbs(late),
        "late_by_crew_type": _late_by_crew_type(late, crews),
        "most_urgent": [_slim(t) for t in late[:MAX_URGENT]],
        "crew_note": crew_note,
    }
    # The rows, not the rendered evidence: which late tasks make the MAX_URGENT cut, and the order
    # of the groups, move with the query's tie order even when no data has changed (see _canon).
    version = _fingerprint(ADVISOR_WELL_SYSTEM, tasks, well, _crews_for(late, crews), crew_note)
    return evidence, _render_well(key[0], evidence), version


def _well_answer(key: tuple[str, str], evidence: dict, prompt: str) -> dict:
    """Ask the model once about the well, over evidence already gathered."""
    started = time.perf_counter()
    raw = chat(ADVISOR_WELL_SYSTEM, prompt, agent="advisor")
    advice = _normalise_well(extract_json(raw, default={}), evidence)
    log.info(
        "advisor: well %s (well-level) answered in %.1fs (%s, %d late task(s))",
        key[0], time.perf_counter() - started,
        "parsed" if advice else "UNPARSED - returning raw text", evidence["tasks"]["late"],
    )

    result = {
        "well_id": key[0],
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model": settings.openai_model,
        "cached": False,
        "advice": advice,
        "raw": None if advice else (raw or "").strip(),
        "evidence": evidence,
    }
    return result
