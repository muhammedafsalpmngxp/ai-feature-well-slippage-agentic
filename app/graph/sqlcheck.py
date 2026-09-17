"""Deterministic checks on generated SQL and on the result's shape.

Nothing here calls an LLM and nothing here is specific to any database. Every schema rule is
derived at runtime from the block introspection already built, so pointing .env at a different
database makes the checks follow it with no code change.

These findings are ADJUDICATED by the Verifier rather than obeyed. The checks are deliberately
conservative and can flag a query that is actually fine - but they give the Verifier something
factual to rule on, which is what its own prompt demands of it ("do not reject on a hunch").

The contract check is the exception: a column-name mismatch is not a matter of judgement, and it
is the failure this pipeline is least able to notice any other way.
"""
from __future__ import annotations

import re

from app.graph import scenarios
from app.observability import get_logger

log = get_logger()

_TABLE_RE = re.compile(r"^TABLE\s+(\S+)", re.MULTILINE)
# The marker carries explanatory text after the key, so do NOT anchor to end-of-line.
_DUP_RE = re.compile(r"MANY ROWS PER\s+(\w+)")
_FK_RE = re.compile(r"^\s+FK:\s+(\w+)\s*->\s*(\S+)\.(\w+)\s*$", re.MULTILINE)

# A query "collapses" a table when it aggregates or de-duplicates it in any of the usual ways.
_COLLAPSING = (
    "row_number", "group by", "distinct ", "count(distinct",
    "sum(", "avg(", "max(", "min(",
)


def _blocks(schema: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for block in re.split(r"(?=^TABLE\s)", schema or "", flags=re.MULTILINE):
        match = _TABLE_RE.search(block)
        if match:
            out.append((match.group(1), block))
    return out


def check_sql(sql: str, schema: str) -> list[str]:
    """Schema-derived concerns about the query text. Empty means these traps were avoided.

    A clean result is NOT a guarantee of correctness - only that these specific patterns are
    absent.
    """
    if not sql or not schema:
        return []
    low = " ".join(sql.split()).lower()
    concerns: list[str] = []

    for table, block in _blocks(schema):
        if table.lower() not in low:
            continue

        # 1. A history table used without being reduced to one row per key.
        for key in _DUP_RE.findall(block):
            if not any(token in low for token in _COLLAPSING):
                concerns.append(
                    table + " holds many rows per " + key
                    + " but the query does not reduce it to one row per " + key
                )
            break

        # 2. A declared foreign key joined to the wrong target column.
        for col, target_table, target_col in _FK_RE.findall(block):
            if col.lower() + " =" not in low and col.lower() + "=" not in low:
                continue
            if target_table.lower() not in low:
                continue
            if target_col.lower() not in low:
                concerns.append(
                    table + "." + col + " is declared to join " + target_table + "." + target_col
                    + ", but that column does not appear in the query"
                )

    return concerns


def check_rules(sql: str) -> list[str]:
    """Textual checks for the two milestone rules a wrong query most often breaks silently.

    Both are detectable from the SQL alone, and both fail in a way no error ever reveals: the
    query succeeds and the numbers are simply wrong.
    """
    if not sql:
        return []
    low = " ".join(sql.split()).lower()
    concerns: list[str] = []

    # milestone_rules §3: "not due yet" must be NULL, not 0. `ELSE 0` at the end of a variance
    # CASE is exactly the defect that shipped in the sibling's query for three of five milestones.
    if re.search(r"else\s+0\s+end\s+as\s+\w*(variance|delay|lag)", low):
        concerns.append(
            "a variance/delay column ends in ELSE 0 - milestone_rules §3 requires NULL for "
            "'not due yet', because 0 already means 'completed exactly on the deadline' and the "
            "two must stay distinguishable"
        )

    # milestone_rules §4 population: only wells still in progress, so a completion date is always
    # absent inside this query and any branch testing it for being PRESENT is unreachable.
    if re.search(r"eng_completion_date\s+is\s+not\s+null", low):
        concerns.append(
            "the query tests a completion date for being present, but the population filter keeps "
            "only wells where it is absent - that branch is unreachable (milestone_rules §4)"
        )

    return concerns


def check_contract(columns: list[str]) -> list[str]:
    """Compare the returned columns against the output contract, in BOTH directions.

    Checked both ways on purpose: a missing column and an unexpected one each mean something
    moved. Only a name check - it says nothing about whether the values are right.
    """
    expected = scenarios.expected_columns()
    got = [str(c) for c in (columns or [])]
    lower_expected = {c.lower() for c in expected}
    lower_got = {c.lower() for c in got}

    errors: list[str] = []
    missing = [c for c in expected if c.lower() not in lower_got]
    unexpected = [c for c in got if c.lower() not in lower_expected]
    if missing:
        errors.append("missing contract column(s): " + ", ".join(missing))
    if unexpected:
        errors.append("column(s) not in the contract: " + ", ".join(unexpected))
    return errors


def findings(sql: str, schema: str, columns: list[str] | None = None) -> list[str]:
    """Every deterministic finding, for the Verifier to adjudicate."""
    try:
        out = check_sql(sql, schema) + check_rules(sql)
        if columns is not None:
            out += check_contract(columns)
        return list(dict.fromkeys(out))  # de-duplicate, keep order
    except Exception as exc:  # noqa: BLE001 - an advisory signal must never break a run
        log.warning("sqlcheck: skipped (%s)", exc)
        return []
