"""One configured logger for the whole run.

Every process that imports this gets the same two destinations: the console it was started
from, and a shared log file. That second one is the point - a run triggered from the dashboard
is a subprocess of the API, so its output would otherwise only ever appear in the API's own
terminal, mixed in with request logs. Writing to a file as well means the pipeline can be
followed from a separate terminal regardless of who started it.
"""
from __future__ import annotations

import logging
import os
import sys
from datetime import datetime

_CONFIGURED = False

# Shown once per process, so a tailed file makes it obvious where one run ends and the next
# begins - otherwise two runs appended together read as one long confusing sequence.
_BANNER = "=" * 78


def _add_file_handler(log: logging.Logger, path: str) -> None:
    """Attach a file handler, or carry on quietly without one.

    Logging is a diagnostic aid; it must never be the reason a run fails. An unwritable path
    (a read-only directory, a file held open elsewhere) degrades to console-only.
    """
    try:
        directory = os.path.dirname(os.path.abspath(path))
        if directory:
            os.makedirs(directory, exist_ok=True)
        handler = logging.FileHandler(path, mode="a", encoding="utf-8")
        # The file keeps the DATE as well as the time. A tailed file outlives the run that
        # produced it, so "12:31:36" alone is ambiguous by the next morning.
        handler.setFormatter(
            logging.Formatter("%(asctime)s  %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S")
        )
        log.addHandler(handler)
        log.info(_BANNER)
        log.info("run started  pid %s  %s", os.getpid(), " ".join(sys.argv))
    except OSError as exc:
        log.warning("logging: could not open %s (%s) - console only", path, exc)


def get_logger() -> logging.Logger:
    global _CONFIGURED
    log = logging.getLogger("slippage")
    if not _CONFIGURED:
        from app.config import settings

        console = logging.StreamHandler(sys.stderr)
        console.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-7s %(message)s", "%H:%M:%S"))
        log.addHandler(console)
        log.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))
        log.propagate = False
        # Set BEFORE the file handler is attached: _add_file_handler logs through this same
        # logger, and without the flag that re-entered get_logger() and configured it twice.
        _CONFIGURED = True

        if settings.log_file:
            _add_file_handler(log, settings.log_file)
    return log
