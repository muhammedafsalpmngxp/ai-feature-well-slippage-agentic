"""One configured logger for the whole run."""
from __future__ import annotations

import logging
import sys

_CONFIGURED = False


def get_logger() -> logging.Logger:
    global _CONFIGURED
    log = logging.getLogger("slippage")
    if not _CONFIGURED:
        from app.config import settings

        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-7s %(message)s", "%H:%M:%S"))
        log.addHandler(handler)
        log.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))
        log.propagate = False
        _CONFIGURED = True
    return log
