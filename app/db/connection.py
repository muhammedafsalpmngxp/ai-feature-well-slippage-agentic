"""Read-only-intent MS SQL connection.

Nothing here issues a write. Combined with the Validator's SELECT-only gate, that keeps the
database safe even when the configured login is privileged.
"""
from __future__ import annotations

import datetime
import struct

import pyodbc

from app.config import settings

# pyodbc has no decoder for SQL Server's `datetimeoffset` (ODBC type -155): fetching ANY column
# of that type raises "ODBC SQL type -155 is not yet supported", even when every value is NULL.
# No SQL can avoid it — it is a driver gap. This is the converter Microsoft's own ODBC samples
# document: the driver hands back fixed-layout bytes, unpacked here into an aware datetime.
_SQL_SS_TIMESTAMPOFFSET = -155

_DRIVER_CANDIDATES = [
    "ODBC Driver 18 for SQL Server",
    "ODBC Driver 17 for SQL Server",
    "SQL Server",
]


def _decode_datetimeoffset(raw: bytes) -> datetime.datetime:
    year, month, day, hour, minute, second, frac_100ns, tz_hour, tz_minute = struct.unpack(
        "<6hI2h", raw
    )
    return datetime.datetime(
        year, month, day, hour, minute, second, frac_100ns // 1000,
        tzinfo=datetime.timezone(datetime.timedelta(hours=tz_hour, minutes=tz_minute)),
    )


def _pick_driver() -> str:
    if settings.db_driver:
        return settings.db_driver
    installed = {d.strip() for d in pyodbc.drivers()}
    for candidate in _DRIVER_CANDIDATES:
        if candidate in installed:
            return candidate
    if installed:
        return sorted(installed)[-1]
    raise RuntimeError("No ODBC driver found. Install 'ODBC Driver 18 for SQL Server' (or 17).")


def _connection_string() -> str:
    missing = [
        name
        for name, value in (
            ("DB_SERVER", settings.db_server),
            ("DB_NAME", settings.db_name),
            ("DB_USER", settings.db_user),
            ("DB_PASSWORD", settings.db_password),
        )
        if not value
    ]
    if missing:
        raise ValueError("Missing database settings in .env: " + ", ".join(missing))

    return (
        f"DRIVER={{{_pick_driver()}}};"
        f"SERVER={settings.db_server},{settings.db_port};"
        f"DATABASE={settings.db_name};"
        f"UID={settings.db_user};"
        f"PWD={settings.db_password};"
        f"Encrypt={settings.db_encrypt};"
        f"TrustServerCertificate={settings.db_trust_cert};"
    )


def get_connection() -> pyodbc.Connection:
    """Open a fresh autocommit connection with the configured query timeout.

    The converter is registered per-connection rather than globally because every call opens a
    new connection — there is no pool to configure once.
    """
    conn = pyodbc.connect(_connection_string(), autocommit=True, timeout=settings.query_timeout)
    conn.timeout = settings.query_timeout
    conn.add_output_converter(_SQL_SS_TIMESTAMPOFFSET, _decode_datetimeoffset)
    return conn


def ping() -> tuple[bool, str]:
    """Health check used before a run starts. Returns (ok, message)."""
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT DB_NAME()")
        name = cur.fetchone()[0]
        conn.close()
        return True, f"Connected to {name}"
    except Exception as exc:  # noqa: BLE001 - surface any driver/network error to the caller
        return False, str(exc)
