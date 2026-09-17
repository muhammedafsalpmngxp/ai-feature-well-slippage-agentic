"""FastAPI service for the slippage dashboard.

Read-only by construction: every endpoint runs a SELECT that the agent pipeline already verified,
and the safety gate is re-applied when each file is loaded. Nothing here can write to the
database, and no endpoint calls an LLM.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from api import service
from app.observability import get_logger

log = get_logger()

app = FastAPI(title="Well Slippage API", version="1.0.0")

# The dev server, and a build served from the same host. Narrow on purpose: this service can read
# a client database, so it must not be callable from any origin a browser happens to be on.
_ORIGINS = [o for o in os.environ.get("CORS_ORIGINS", "").split(",") if o.strip()] or [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# Guards the one endpoint that starts work rather than reading it.
_run_lock = threading.Lock()
_run_state: dict = {
    "running": False,
    "started_at": None,
    "finished_at": None,
    "exit_code": None,
    # Last few log lines, so the browser can show progress rather than only "running".
    "tail": [],
}


def _fail(exc: Exception, status: int = 503) -> HTTPException:
    log.warning("api: %s", exc)
    return HTTPException(status_code=status, detail=str(exc))


def _query_failed(exc: Exception, what: str) -> HTTPException:
    """A generated query that will no longer run.

    The usual cause is a schema change: the SQL is fine, but a column it names has been renamed
    or dropped. Saying so - and naming the fix - beats "check the server logs", which tells a
    dashboard reader nothing they can act on.
    """
    log.exception("api: %s failed", what)
    detail = "Query failed. Check the server logs."
    if "invalid column" in str(exc).lower() or "invalid object" in str(exc).lower():
        detail = (
            "This query references something the database no longer has - the schema has "
            "probably changed since it was generated. Re-run the analysis to rebuild it."
        )
    return HTTPException(status_code=500, detail=detail)


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/status")
def status():
    """What the pipeline has produced, and how old it is."""
    return {**service.status(), "run": _run_state}


@app.get("/api/wells")
def wells():
    """Every well that failed at least one contractual milestone."""
    try:
        return {"wells": service.slipped_wells()}
    except service.QueryUnavailable as exc:
        raise _fail(exc) from exc
    except Exception as exc:  # noqa: BLE001
        raise _query_failed(exc, "/api/wells") from exc


@app.get("/api/activity-summary")
def activity_summary():
    """Delayed activity-code count per well."""
    try:
        return {"wells": service.activity_summary()}
    except service.QueryUnavailable as exc:
        raise _fail(exc) from exc
    except Exception as exc:  # noqa: BLE001
        raise _query_failed(exc, "/api/activity-summary") from exc


@app.get("/api/wells/{well_id}/activity")
def well_activity(well_id: str):
    """Per-task delay detail for ONE well, via the verified query's bound parameter."""
    well_id = (well_id or "").strip()
    if not well_id or len(well_id) > 50:
        raise HTTPException(status_code=400, detail="Invalid well id.")
    try:
        return {"well_id": well_id, "tasks": service.activity_for_well(well_id)}
    except service.QueryUnavailable as exc:
        raise _fail(exc) from exc
    except Exception as exc:  # noqa: BLE001
        raise _query_failed(exc, "activity for well " + well_id) from exc


@app.get("/api/brief")
def brief():
    """The most recent generated brief, as markdown."""
    return {"markdown": service.brief()}


@app.get("/api/sql/{key}")
def sql(key: str):
    """The verified SQL behind a panel.

    Exposed deliberately: an operations figure nobody can trace is a figure nobody should act
    on, and this is the whole provenance of every number the dashboard shows.
    """
    try:
        return {"key": key, "sql": service.load_sql(key)}
    except service.QueryUnavailable as exc:
        raise _fail(exc, status=404) from exc


def _run_pipeline(refresh: bool) -> None:
    cmd = [sys.executable, "main.py", "--out", "out"]
    if refresh:
        cmd.append("--refresh")
    started = datetime.now(timezone.utc).isoformat()
    _run_state.update(running=True, started_at=started, finished_at=None, exit_code=None)
    try:
        # STREAMED, not captured. `capture_output=True` swallowed every line until the run
        # finished and then discarded it, so a run triggered from the dashboard was completely
        # invisible in this terminal - the one place someone watching it would look. Reading
        # line by line and echoing means the agent flow appears here as it happens, exactly as
        # it does when main.py is run directly.
        #
        # stderr is merged into stdout because the pipeline logs to stderr; keeping them apart
        # would interleave unpredictably and lose the ordering that makes the trace readable.
        proc = subprocess.Popen(
            cmd,
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            encoding="utf-8",
            errors="replace",
        )
        _run_state["tail"] = []
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip()
            if not line:
                continue
            # NOT echoed to this console. The subprocess already writes every line to the
            # shared log file (see app/observability.py), so echoing here only duplicated it
            # into the API's terminal on top of the HTTP request logs. Follow the run with
            # `Get-Content -Wait logs\pipeline.log` in its own terminal instead.
            #
            # The loop still has to READ the pipe: an unread pipe fills its buffer and blocks
            # the child mid-run, which would hang the pipeline rather than just silence it.
            #
            # A short tail is kept so /api/status can show progress in the browser too.
            tail = _run_state["tail"]
            tail.append(line)
            del tail[:-40]
        code = proc.wait(timeout=1800)
        log.info("api: pipeline finished with exit %s", code)
    except Exception:  # noqa: BLE001
        log.exception("api: pipeline run failed")
        code = -1
    finally:
        _run_state.update(
            running=False, finished_at=datetime.now(timezone.utc).isoformat(), exit_code=code
        )
        service.invalidate()


@app.post("/api/run")
def run(refresh: bool = Query(False, description="also re-introspect the database")):
    """Start a pipeline run in the background.

    Single-flight: a second request while one is in progress is rejected rather than queued. The
    run costs several minutes and real tokens, so a double-clicked button must not buy two.
    """
    if not _run_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="A run is already in progress.")
    try:
        if _run_state["running"]:
            raise HTTPException(status_code=409, detail="A run is already in progress.")
        threading.Thread(target=_run_pipeline, args=(refresh,), daemon=True).start()
        return {"started": True}
    finally:
        _run_lock.release()
