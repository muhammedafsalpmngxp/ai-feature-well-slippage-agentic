"""Execute the SQL the agents already wrote and verified.

THE API NEVER CALLS AN LLM. The agent pipeline's job is to produce a query and prove it correct;
once it has, that query is a deterministic artefact. Serving a request means running it, which
takes a couple of seconds and costs nothing, instead of five minutes and a stack of tokens.

That split is the whole point of the design:

    main.py   (slow, occasional)  ->  writes verified SQL to out/
    this API  (fast, every click) ->  runs it

A consequence worth stating: the API is only ever as current as the last verified run. It
reports when that was, so a stale dashboard is visible rather than assumed fresh.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from threading import Lock
from typing import Any

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
        sql = handle.read().strip().rstrip(";").strip()

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
    """True when the live database no longer matches what the cached queries were built against.

    This is the one failure the dashboard could not otherwise see. A renamed or dropped column
    leaves the generated SQL syntactically fine but referencing something gone, so it fails at
    execution as an opaque ODBC "Invalid object name" - and a 500 tells a reader nothing about
    what to do. Comparing fingerprints says exactly what happened: the schema moved, re-run the
    analysis.

    Returns None rather than False when the check itself could not run, so "unknown" is never
    reported as "fine". Cached, because it costs several catalogue reads.
    """

    def check() -> bool | None:
        from app.db.introspect import _fingerprint, _read, _FINGERPRINT_PATH

        recorded = _read(_FINGERPRINT_PATH)
        if not recorded:
            return None
        conn = get_connection()
        try:
            return _fingerprint(conn.cursor()) != recorded
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass

    try:
        return _cached("schema_drift", check)
    except Exception as exc:  # noqa: BLE001 - a status read must never fail the dashboard
        log.warning("status: drift check failed (%s)", exc)
        return None


def status() -> dict:
    """What exists on disk, and how old it is - so a stale dashboard is visible."""
    out: dict[str, Any] = {"out_dir": OUT_DIR, "queries": {}}
    # Note this reflects the STRUCTURE the queries were built against, and the fingerprint also
    # covers row counts - so ordinary data loading flips it too. It means "re-run to be current",
    # not "the queries are broken".
    out["schema_drifted"] = schema_drifted()
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
    out["last_run"] = (
        datetime.fromtimestamp(newest, timezone.utc).isoformat() if newest else None
    )
    out["age_hours"] = round((time.time() - newest) / 3600, 1) if newest else None
    return out
