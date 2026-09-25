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


_CTE_NAME_RE = re.compile(r"(?:^\s*WITH|,)\s*([A-Za-z_]\w*)\s+AS\s*\(", re.IGNORECASE | re.MULTILINE)
# `alias.column` references, and the `FROM/JOIN <cte> AS <alias>` that binds an alias to a CTE.
_QUALIFIED_RE = re.compile(r"\b([A-Za-z_]\w*)\.([A-Za-z_]\w*)\b")
# The source may be schema-qualified, so it must allow dots. Without them `FROM well.task_daily
# AS t` did not match at all, so an alias reused for both a real table and a CTE looked
# unambiguous - and the scope guard above silently stopped protecting anything.
_FROM_ALIAS_RE = re.compile(
    r"\b(?:FROM|JOIN)\s+([A-Za-z_][\w.]*)\s+(?:AS\s+)?([A-Za-z_]\w*)\b", re.IGNORECASE
)


def _balanced_body(sql: str, open_at: int) -> str:
    """The text inside the parentheses starting at `open_at`, respecting nesting."""
    depth, i, n = 0, open_at, len(sql)
    while i < n:
        if sql[i] == "(":
            depth += 1
        elif sql[i] == ")":
            depth -= 1
            if depth == 0:
                return sql[open_at + 1 : i]
        i += 1
    return ""


def _projected_columns(body: str) -> set[str] | None:
    """Output names of a CTE's final SELECT list, or None when they cannot be read.

    Returns None rather than guessing on `SELECT *`, a set-operator, or anything else this
    cannot parse confidently - an unreadable CTE must produce no finding at all, never a wrong
    one. That conservatism is the point: this is advisory evidence, so a false positive costs
    the Verifier a moment, and a missed one costs nothing it was not already missing.
    """
    upper = body.upper()
    if " UNION " in upper or " EXCEPT " in upper or " INTERSECT " in upper:
        return None
    select_at = upper.rfind("SELECT")
    if select_at == -1:
        return None
    # Word-boundary match, not "\nFROM": the FROM is almost always indented, and anchoring to
    # the line start silently made every CTE unreadable - the check found nothing at all.
    from_match = re.search(r"\bFROM\b", upper[select_at:])
    if not from_match:
        return None
    from_at = select_at + from_match.start()
    if "*" in body[select_at:from_at]:
        return None

    names: set[str] = set()
    depth = 0
    item = ""
    for ch in body[select_at + len("SELECT") : from_at] + ",":
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            token = item.strip()
            if token:
                # "expr AS name" -> name; "x.col" -> col; a bare word -> itself.
                parts = re.split(r"\s+AS\s+", token, flags=re.IGNORECASE)
                tail = parts[-1].strip().strip("[]")
                names.add((tail.rsplit(".", 1)[-1] if "." in tail else tail).lower())
            item = ""
            continue
        item += ch
    return names or None


def check_cte_projection(sql: str) -> list[str]:
    """Columns read from a CTE that the CTE never projects.

    This is the "Invalid column name" failure that has cost a rewrite three times: a CTE selects
    what it needs internally, a later stage reads a column it did not carry forward, and the
    mistake only surfaces as an ODBC error after a full database round trip. The information to
    catch it is entirely in the SQL text.
    """
    if not sql or "with" not in sql.lower():
        return []

    projected: dict[str, set[str]] = {}
    for match in _CTE_NAME_RE.finditer(sql):
        open_at = sql.find("(", match.end() - 1)
        if open_at == -1:
            continue
        columns = _projected_columns(_balanced_body(sql, open_at))
        if columns:
            projected[match.group(1).lower()] = columns
    if not projected:
        return []

    # Which alias refers to which CTE.
    #
    # ⚠ ALIASES ARE SCOPED AND THIS CANNOT SEE SCOPE. A real generated query bound `t` to
    # well.task_daily inside one CTE and to latest_task_cte later; a global map takes the last
    # binding and misattributes every earlier reference - four confident, wrong findings on a
    # query that was correct.
    #
    # So an alias is only judged when it is bound to exactly ONE source in the whole statement.
    # A reused alias is skipped entirely: this check exists to catch a specific mistake cheaply,
    # and a false accusation sent to the Verifier costs exactly the rework it is meant to save.
    bindings: dict[str, set[str]] = {}
    for source, alias in _FROM_ALIAS_RE.findall(sql):
        if alias.upper() in ("AS", "ON", "WHERE", "GROUP", "ORDER", "INNER", "LEFT", "JOIN"):
            continue
        bindings.setdefault(alias.lower(), set()).add(source.lower())

    alias_of: dict[str, str] = {
        alias: next(iter(sources))
        for alias, sources in bindings.items()
        if len(sources) == 1 and next(iter(sources)) in projected
    }
    # A CTE referenced by its own name, with no alias, as long as that name is not also reused.
    for name in projected:
        if len(bindings.get(name, {name})) == 1:
            alias_of.setdefault(name, name)

    concerns: list[str] = []
    for alias, column in _QUALIFIED_RE.findall(sql):
        cte = alias_of.get(alias.lower())
        if not cte:
            continue
        if column.lower() not in projected[cte]:
            concern = (
                "`" + alias + "." + column + "` is read from CTE `" + cte + "`, which does not "
                "project a column of that name - add it to that CTE's SELECT list, or the query "
                "fails as \"Invalid column name\""
            )
            if concern not in concerns:
                concerns.append(concern)
    return concerns


def check_rules(sql: str, completion_column: str = "") -> list[str]:
    """Textual checks for the two milestone rules a wrong query most often breaks silently.

    Both are detectable from the SQL alone, and both fail in a way no error ever reveals: the
    query succeeds and the numbers are simply wrong.

    `completion_column` is the column the population filter tests, supplied by the Planner from
    the LIVE schema. It used to be the literal "eng_completion_date", which made this module the
    one place that knew a database column name - and a rename would have retired the check in
    silence, which is worse than never having had it. Blank means that check is skipped.
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
    if completion_column:
        # The  is a word boundary, so a column named `x` cannot match `prefix_x`.
        # The word boundary stops a column named `x` matching `prefix_x`.
        pattern = r"\b" + re.escape(completion_column.lower()) + r"\s+is\s+not\s+null"
        if re.search(pattern, low):
            concerns.append(
                "the query tests " + completion_column + " for being present, but the population "
                "filter keeps only wells where it is absent - that branch is unreachable "
                "(milestone_rules §4)"
            )

    return concerns


def check_contract(columns: list[str], expected: list[str] | None = None) -> list[str]:
    """Compare the returned columns against a query's output contract, in BOTH directions.

    Checked both ways on purpose: a missing column and an unexpected one each mean something
    moved. Only a name check - it says nothing about whether the values are right.

    `expected` defaults to the well-slippage contract so older callers keep working, but every
    caller in the graph passes the contract of the query actually being run.
    """
    expected = expected if expected is not None else scenarios.expected_columns()
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


def findings(
    sql: str,
    schema: str,
    columns: list[str] | None = None,
    expected: list[str] | None = None,
    completion_column: str = "",
) -> list[str]:
    """Every deterministic finding, for the Verifier to adjudicate."""
    try:
        out = (
            check_sql(sql, schema)
            + check_rules(sql, completion_column)
            + check_cte_projection(sql)
        )
        if columns is not None:
            out += check_contract(columns, expected)
        return list(dict.fromkeys(out))  # de-duplicate, keep order
    except Exception as exc:  # noqa: BLE001 - an advisory signal must never break a run
        log.warning("sqlcheck: skipped (%s)", exc)
        return []
