"""Execute the SQL the agents already wrote and verified.

THE API NEVER CALLS AN LLM. The agent pipeline's job is to produce a query and prove it correct;
once it has, that query is a deterministic artefact. Serving a request means running it, which
takes a couple of seconds and costs nothing, instead of five minutes and a stack of tokens.

That split is the whole point of the design:

    main.py   (slow, occasional)  ->  freezes verified SQL into sql/
    this API  (fast, every click) ->  runs it

Since the freeze landed, main.py is usually not slow either: it re-authors only when the
database's structure changes, and otherwise just executes what is already in sql/ (app/frozen.py).

A consequence worth stating: the API is only ever as current as the last run. It reports when
that was, so a stale dashboard is visible rather than assumed fresh.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from threading import Lock
from typing import Any

from app import frozen
from app.db.connection import get_connection
from app.graph import queries
from app.graph.nodes.validator import is_select_only
from app.observability import get_logger

log = get_logger()

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.environ.get("SLIPPAGE_OUT_DIR") or os.path.join(_ROOT, "out")

# Fleet queries change only when the pipeline reruns, so their results are cached for a short
# while. Small enough that a rerun shows up promptly, large enough that a dashboard rendering
# five panels does not run the same query five times.
_TTL_SECONDS = 60
_cache: dict[str, tuple[float, Any]] = {}
_lock = Lock()


class QueryUnavailable(RuntimeError):
    """Raised when a query has not been produced by any pipeline run yet."""


def sql_path(key: str) -> str:
    """Where this query's SQL is read from.

    sql/ is the frozen store - the reviewed artefact, committed, and the source of truth. out/
    is the previous location and is still honoured so an existing checkout keeps working after
    the freeze landed; it is a fallback, never preferred.
    """
    path = frozen.path_for(key)
    if os.path.exists(path):
        return path
    return os.path.join(OUT_DIR, key + ".sql")


def load_sql(key: str) -> str:
    """Read one verified query off disk, re-checking that it is still read-only.

    The safety gate is applied AGAIN here, not trusted from the pipeline run. These files sit on
    an ordinary filesystem; anything that can edit them could otherwise turn a dashboard request
    into a write. Verifying on every load costs microseconds.
    """
    path = sql_path(key)
    if not os.path.exists(path):
        raise QueryUnavailable(
            key + " has not been generated yet. Run `python main.py --out out` first."
        )
    with open(path, encoding="utf-8") as handle:
        text = handle.read()

    # A frozen file opens with a provenance header. Strip it here rather than executing it: the
    # comment is harmless to SQL Server, but `activity_for_well` tests the statement for a bound
    # `?` and a header that happened to contain one would read as a parameter that is not there.
    sql = frozen.body_of(text).rstrip(";").strip()

    ok, reason = is_select_only(sql)
    if not ok:
        raise QueryUnavailable(key + " on disk is not a safe read-only SELECT: " + reason)
    return sql


def run_sql(sql: str, params: list | None = None, limit: int = 20000) -> dict:
    """Execute a statement read-only and return {columns, rows, elapsed_ms}.

    `params` are BOUND, never interpolated, so a well id from a URL cannot alter the statement.
    """
    conn = None
    start = time.perf_counter()
    try:
        conn = get_connection()
        cur = conn.cursor()
        if params:
            cur.execute(sql, *params)
        else:
            cur.execute(sql)
        if cur.description is None:
            return {"columns": [], "rows": [], "elapsed_ms": 0}
        columns = [d[0] for d in cur.description]
        rows = [
            [_jsonable(v) for v in row]
            for row in cur.fetchmany(limit)
        ]
        return {
            "columns": columns,
            "rows": rows,
            "elapsed_ms": round((time.perf_counter() - start) * 1000),
        }
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


def _jsonable(value: Any) -> Any:
    """Dates to ISO strings, Decimals to floats, everything else untouched.

    NaN and infinity become None rather than tokens no JSON parser accepts - a chart reading
    "NaN" as a number is worse than a gap.
    """
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, bool):
        return value
    if hasattr(value, "__float__") and not isinstance(value, (str, int)):
        try:
            number = float(value)
        except (TypeError, ValueError):
            return str(value)
        if number != number or number in (float("inf"), float("-inf")):
            return None
        return number
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return value.hex()
    return value


def _cached(key: str, producer) -> Any:
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < _TTL_SECONDS:
            return hit[1]
    value = producer()
    with _lock:
        _cache[key] = (now, value)
    return value


def invalidate() -> None:
    with _lock:
        _cache.clear()


def as_records(result: dict) -> list[dict]:
    columns = result.get("columns") or []
    return [dict(zip(columns, row)) for row in (result.get("rows") or [])]


# -- Public reads --------------------------------------------------------------


def slipped_wells() -> list[dict]:
    return _cached("well_slippage", lambda: as_records(run_sql(load_sql("well_slippage"))))


def activity_summary() -> list[dict]:
    return _cached("activity_summary", lambda: as_records(run_sql(load_sql("activity_summary"))))


def activity_for_well(well_id: str) -> list[dict]:
    """The per-task detail for ONE well, via the bound parameter in the verified query.

    Not cached: it is per-well and already fast, and caching it would need a key per well.
    """
    sql = load_sql("activity_delay")
    if "?" not in sql:
        # The generated query inlined the well instead of binding it, so it answers about
        # whichever well the pipeline happened to target. Refusing is the only honest response -
        # returning it would show one well's tasks under another well's heading.
        raise QueryUnavailable(
            "activity_delay was generated without a bound parameter, so it cannot be run for "
            "another well. Re-run the pipeline to regenerate it."
        )
    return as_records(run_sql(sql, params=[str(well_id).strip()]))


def brief() -> str:
    path = os.path.join(OUT_DIR, "brief.md")
    if not os.path.exists(path):
        return ""
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def schema_drifted() -> bool | None:
    """True when re-running the pipeline would actually author new SQL.

    This is the one failure the dashboard could not otherwise see. A renamed or dropped column
    leaves the frozen SQL syntactically fine but referencing something gone, so it fails at
    execution as an opaque ODBC "Invalid object name" - and a 500 tells a reader nothing about
    what to do. Comparing fingerprints says exactly what happened: the schema moved, re-run.

    ⚠ COMPARED AGAINST THE FROZEN QUERIES' STRUCTURAL FINGERPRINT, not introspection's cache
    key. The cache key also folds in row counts, so it flipped on every ordinary data load - and
    the banner it drove told a reader to re-run when a re-run would now reuse the same frozen SQL
    and change nothing on the page. A prompt to act that does nothing teaches people to ignore
    the banner, which costs the one time it is real. Staleness of the FIGURES is a separate
    signal, already carried by `age_hours`.

    Returns None rather than False when the check itself could not run, so "unknown" is never
    reported as "fine". Cached, because it costs several catalogue reads.
    """

    live = live_fingerprint()
    if not live:
        return None
    stamped = {
        row["fingerprint"] for row in frozen.describe()
        if row["state"] != "missing" and row["fingerprint"]
    }
    if not stamped:
        # Nothing frozen carries a fingerprint, so there is nothing to compare. Unknown, not
        # fine: the queries on disk may predate the freeze entirely.
        return None
    return any(f != live for f in stamped)


def live_fingerprint() -> str:
    """The live structural fingerprint, cached, or "" when it could not be read.

    Shared by the drift check and by status() so one request reads the catalogue ONCE. They used
    to compute it separately, and status() simply did not - which is why every query reported its
    freeze as "unknown" on a request that had just established the schema had not drifted at all.
    Two answers to the same question in one payload, one of them needlessly vague.
    """

    def read() -> str:
        from app.config import settings
        from app.db.introspect import live_structure_fingerprint

        try:
            return live_structure_fingerprint(timeout=settings.status_fingerprint_timeout)
        except Exception as exc:  # noqa: BLE001 - a status read must never fail the dashboard
            # ⚠ CAUGHT INSIDE THE PRODUCER SO THE FAILURE IS CACHED TOO.
            #
            # Letting it escape meant nothing was stored, so every subsequent request re-ran the
            # catalogue read and paid the timeout again. With a slow catalogue that turned one
            # slow read into every /api/status call blocking, for as long as the condition
            # lasted. A failure is an answer - "unknown" - and is worth remembering for the same
            # 60 seconds as a success.
            log.warning("status: could not read the live schema fingerprint (%s)", exc)
            return ""

    return _cached("live_fingerprint", read)


def status(check_schema: bool = False) -> dict:
    """What exists on disk, and how old it is - so a stale dashboard is visible.

    `check_schema` decides whether this read touches the database at all.

    ⚠ IT DEFAULTS TO FALSE, AND EVERYTHING ELSE HERE IS PURE FILESYSTEM.

    The drift check is the only part that needs a live catalogue read, and that read is the one
    expensive thing in this whole module - INFORMATION_SCHEMA on a database with many objects can
    take minutes. Doing it on every status request meant every dashboard mount paid for a banner
    that is almost always hidden. It is now asked for explicitly, by the one action where the
    answer changes what happens next: starting a run.
    """
    out: dict[str, Any] = {"out_dir": OUT_DIR, "sql_dir": frozen.SQL_DIR, "queries": {}}

    # Whether a check was even attempted, so a reader can tell "we did not look" from "we looked
    # and could not tell". Both leave schema_drifted as null, and they mean different things.
    out["schema_checked"] = bool(check_schema)
    # True only when a re-run would actually author new SQL - see schema_drifted. Ordinary data
    # loading does not flip it. Null when unchecked, or when the check could not run.
    out["schema_drifted"] = schema_drifted() if check_schema else None
    # Per-query freeze state. Without a live fingerprint the states that need no database still
    # come through - hand-edited, unverified, missing - and the rest report "unknown".
    fingerprint = live_fingerprint() if check_schema else ""
    out["frozen"] = {row["key"]: row for row in frozen.describe(fingerprint)}
    newest = 0.0
    for spec in queries.QUERIES:
        path = sql_path(spec.key)
        exists = os.path.exists(path)
        mtime = os.path.getmtime(path) if exists else 0.0
        newest = max(newest, mtime)
        out["queries"][spec.key] = {
            "label": spec.label,
            "available": exists,
            "needs_well_id": spec.needs_well_id,
            "generated_at": (
                datetime.fromtimestamp(mtime, timezone.utc).isoformat() if exists else None
            ),
        }
    # ⚠ THE LAST RUN IS NOT THE SQL'S MTIME ANY MORE.
    #
    # It used to be, and that was fine while every run rewrote every .sql file. Now a run on an
    # unchanged schema reuses the frozen SQL and touches nothing in sql/, so keying "last
    # analysis" off those files would have the dashboard report figures as days old minutes
    # after producing them - and the staleness warning is only worth anything if it is true.
    #
    # brief.md is written by every run that passed --out, which is how the API invokes it, so its
    # mtime is when the numbers on the page were last computed. The SQL mtimes remain the right
    # answer for `generated_at` above: that genuinely is when the query was written.
    brief_path = os.path.join(OUT_DIR, "brief.md")
    last_run = os.path.getmtime(brief_path) if os.path.exists(brief_path) else newest
    out["last_run"] = (
        datetime.fromtimestamp(last_run, timezone.utc).isoformat() if last_run else None
    )
    out["age_hours"] = round((time.time() - last_run) / 3600, 1) if last_run else None
    return out
