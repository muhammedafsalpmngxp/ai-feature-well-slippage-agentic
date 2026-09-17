"""Executor (deterministic) - runs the validated SELECT, capped, timed and logged.

Step 4, and the only node that touches the database during a run. No LLM is involved: by this
point the query has passed the safety gate, and what comes back is whatever the database says.
"""
from __future__ import annotations

import time

from app.config import settings
from app.db.connection import get_connection
from app.graph.state import SlippageState
from app.observability import get_logger

log = get_logger()


def executor_node(state: SlippageState) -> dict:
    sql = state.get("sql", "")
    conn = None
    start = time.perf_counter()

    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(sql)

        if cur.description is None:
            # A statement that returns no result set is not a listing. Treated as an execution
            # error so it routes back for a rewrite rather than being explained as "no wells".
            raise ValueError("The query returned no result set - it must be a SELECT.")

        columns = [d[0] for d in cur.description]
        # One row past the cap, so truncation is detected rather than assumed.
        fetched = [list(r) for r in cur.fetchmany(settings.max_rows + 1)]
        truncated = len(fetched) > settings.max_rows
        rows = fetched[: settings.max_rows]
        elapsed = time.perf_counter() - start

        log.info(
            "exec: %d rows%s x %d cols in %.2fs",
            len(rows), " (capped)" if truncated else "", len(columns), elapsed,
        )
        return {
            "columns": columns,
            "rows": rows,
            "row_count": len(rows),
            "truncated": truncated,
            "elapsed": elapsed,
            "exec_error": "",
        }

    except Exception as exc:  # noqa: BLE001 - the error text is fed back for self-correction
        elapsed = time.perf_counter() - start
        log.warning("exec: failed in %.2fs - %s", elapsed, exc)
        return {"exec_error": str(exc), "retry_count": state.get("retry_count", 0) + 1}

    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
