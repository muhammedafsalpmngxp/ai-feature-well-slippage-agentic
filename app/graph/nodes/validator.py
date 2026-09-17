"""Validator (deterministic) - the hard SELECT-only safety gate.

This is the boundary that guarantees the pipeline can never modify the database, regardless of
what privileges the configured login happens to have.
"""
from __future__ import annotations

import re

from app.graph import queries
from app import rework
from app.graph.state import SlippageState
from app.observability import get_logger

log = get_logger()

# Whole-word keywords that must never appear in a query we run.
_FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|MERGE|DROP|ALTER|CREATE|TRUNCATE|EXEC|EXECUTE|"
    r"GRANT|REVOKE|BACKUP|RESTORE|SHUTDOWN|RECONFIGURE|INTO)\b",
    re.IGNORECASE,
)
_PROC = re.compile(r"\b(sp_|xp_)\w+", re.IGNORECASE)

# Read-side dangers. All legal inside a SELECT, so the rules above let them through - but they
# reach OUTSIDE the approved schemas: the server's filesystem, a linked server, or the login
# catalogue (sys.sql_logins exposes password hashes). They cannot modify data; they leak it.
_UNSAFE = re.compile(
    r"\b(OPENROWSET|OPENQUERY|OPENDATASOURCE|OPENXML|BULK|WAITFOR)\b"
    r"|\bfn_(get_audit_file|trace_gettable)\b"
    r"|\bsys\.(sql_logins|server_principals|credentials|master_key_passwords)\b",
    re.IGNORECASE,
)

# schema.table straight after FROM/JOIN. The dot is required on purpose: a bare word there is
# almost always a CTE name or a subquery alias, and real tables are always schema-qualified.
_TABLE_REF = re.compile(r"\b(?:FROM|JOIN)\s+([A-Za-z_]\w*\.[A-Za-z_]\w*)", re.IGNORECASE)
_TABLE_DECL = re.compile(r"^TABLE (\S+)", re.MULTILINE)


def scrub(sql: str) -> str:
    """Blank out string literals, bracketed identifiers and comments in ONE pass.

    Only this inspection copy is scrubbed - what executes is always the original.

    A single character scan rather than two regex passes, for two reasons:

    * a keyword inside DATA is not a command. `WHERE remarks LIKE '%update required%'` would be
      rejected as an UPDATE, failing a legitimate query and burning both retries;
    * doing strings and comments separately is exploitable. Strip comments first and a `--`
      inside a string breaks the literal; blank strings first and a lone quote inside a comment
      can swallow a real statement, e.g.

          SELECT a -- '
          DROP TABLE t -- '

      would mask the DROP while SQL Server still executes it as a second statement.
    """
    out: list[str] = []
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]

        if sql.startswith("--", i):
            newline = sql.find("\n", i)
            i = n if newline == -1 else newline
            out.append(" ")
            continue

        if sql.startswith("/*", i):
            end = sql.find("*/", i + 2)
            i = n if end == -1 else end + 2
            out.append(" ")
            continue

        if ch == "'":  # string literal; '' escapes a quote inside it
            i += 1
            while i < n:
                if sql[i] == "'":
                    if i + 1 < n and sql[i + 1] == "'":
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            out.append("''")
            continue

        # Bracketed identifier, e.g. [Delete Count]. Only when properly closed, so an unmatched
        # '[' can never swallow the rest of the statement.
        if ch == "[":
            end = sql.find("]", i + 1)
            if end != -1:
                out.append("[x]")
                i = end + 1
                continue

        out.append(ch)
        i += 1
    return "".join(out)


def is_select_only(sql: str) -> tuple[bool, str]:
    if not sql or not sql.strip():
        return False, "Empty query."
    clean = scrub(sql).strip().rstrip(";").strip()

    if ";" in clean:
        return False, "Multiple statements are not allowed - write a single SELECT."

    lowered = clean.lower()
    if not (lowered.startswith("select") or lowered.startswith("with")):
        return False, "Query must start with SELECT (or WITH ... SELECT)."

    match = _FORBIDDEN.search(clean)
    if match:
        return False, "Forbidden keyword '" + match.group(0) + "' - only read-only SELECT is permitted."
    if _PROC.search(clean):
        return False, "Stored-procedure calls (sp_/xp_) are not allowed."
    match = _UNSAFE.search(clean)
    if match:
        return False, (
            "'" + match.group(0) + "' is not allowed - a query may only read the approved "
            "schemas, never remote servers, files on disk, or login/credential data."
        )

    return True, ""


def unknown_tables(sql: str, schema_text: str) -> list[str]:
    """schema.table references the SQL makes that are not in the detected schema.

    Catches a table-name slip before the query ever reaches the database, where it would
    otherwise surface only as an ODBC "Invalid object name" after a full round trip.

    Returns [] when the schema block carries no TABLE lines: having nothing to check against
    must never cause a false rejection.
    """
    valid = {m.lower() for m in _TABLE_DECL.findall(schema_text or "")}
    if not valid:
        return []
    referenced = {m.lower() for m in _TABLE_REF.findall(scrub(sql))}
    return sorted(referenced - valid)


def count_parameters(sql: str) -> int:
    """Parameter markers in the statement, ignoring any `?` inside a literal or comment.

    Counted on the scrubbed copy for exactly that reason: a question mark in a string, say
    `LIKE '%?%'`, is data and must not read as a placeholder.
    """
    return scrub(sql).count("?")


def _reject(state: SlippageState, reason: str) -> dict:
    log.warning("validate: rejected - %s", reason)
    rework.record(
        rework.KIND_VALIDATION,
        query=state.get("current_query", ""),
        reason=reason,
        sql=state.get("sql", ""),
        attempt=state.get("retry_count", 0) + 1,
    )
    return {"validation_error": reason, "retry_count": state.get("retry_count", 0) + 1}


def validator_node(state: SlippageState) -> dict:
    sql = state.get("sql", "")

    ok, reason = is_select_only(sql)
    if not ok:
        return _reject(state, reason)

    unknown = unknown_tables(sql, state.get("schema", ""))
    if unknown:
        reason = (
            "Unknown table(s): " + ", ".join(unknown) + " - not in the schema block. Use only the "
            "exact schema.table names listed there."
        )
        return _reject(state, reason)

    # A per-well query must bind its well, not inline it. Checked HERE rather than left to the
    # database: an inlined id executes perfectly and answers about whichever well the pipeline
    # happened to target, so the API could never re-run it for another one. Letting pyodbc catch
    # the mismatch instead costs a full retry cycle and reports it as an opaque driver error.
    spec = queries.QUERIES_BY_KEY.get(state.get("current_query", ""))
    if spec and spec.needs_well_id:
        markers = count_parameters(sql)
        if markers != 1:
            reason = (
                "This query must filter its well with EXACTLY ONE `?` parameter marker; found "
                + str(markers) + ". Do not write the well id as a literal and do not use "
                "DECLARE. Compare against `?` directly, e.g. "
                "CONVERT(varchar(50), <task well column>) = CONVERT(varchar(50), ?)."
            )
            return _reject(state, reason)

    log.info("validate: ok - single read-only SELECT")
    return {"validation_error": ""}
