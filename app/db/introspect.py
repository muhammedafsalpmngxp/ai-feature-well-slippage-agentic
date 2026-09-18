"""Step 1 - detect the database: render its exact schema, and sample real values.

Everything the agents know about this database comes from here. Nothing downstream hardcodes a
table or column name, so pointing .env at a different database changes the queries the agents
write without a code change.

Two artefacts are produced:

  * SCHEMA BLOCK  - every table, its columns with DECLARED TYPES, primary keys, foreign keys, and
                    a "MANY ROWS PER <key>" marker on any table holding more rows than distinct
                    key values. The type matters as much as the name: milestone_rules.md Part 2
                    records that well_id is varchar on the task record and int on the well record,
                    so the two sides of that join must be cast explicitly.
  * VALUE HINTS   - the real coded values in small lookup tables, so a filter is written against
                    a value that exists rather than one that merely sounds plausible.

Both are cached, keyed on a fingerprint of the LIVE database. The fingerprint covers more than
column names, because several things can change without a column changing: a dropped foreign key,
a new CHECK constraint, an altered default. It also folds in the row counts - the value hints are
built from real data, so they go stale when rows are loaded even though the structure is
identical - and the renderer version, so changing the rendering invalidates a cache the database
alone would have kept valid forever.
"""
from __future__ import annotations

import hashlib
import os
import re

from app.config import settings
from app.db.connection import get_connection
from app.observability import get_logger

log = get_logger()

# Bump when _render() changes the TEXT it emits for an unchanged database. Folded into the
# fingerprint, because otherwise a rendering change keeps serving the old cached text forever:
# the database has not changed, so nothing else would ever notice.
_RENDERER_VERSION = "1"

_CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), ".cache")
_SCHEMA_PATH = os.path.join(_CACHE_DIR, "schema.txt")
_FINGERPRINT_PATH = os.path.join(_CACHE_DIR, "schema.fingerprint")
_HINTS_PATH = os.path.join(_CACHE_DIR, "value_hints.txt")

# A column name that must never be rendered, whatever table it sits on.
_SECRET_RE = re.compile(r"password|secret|token|api[_-]?key|salt|hash", re.IGNORECASE)
# Identifiers needing [brackets]: anything that is not a plain word.
_PLAIN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Value hints are only useful for short, repeated, code-like values. A free-text column produces
# a wall of prose that crowds out the schema without helping any query.
_HINT_NAME_RE = re.compile(r"code|type|status|name|category|unit|purpose|reason", re.IGNORECASE)
_HINT_MAX_DISTINCT = 40
_HINT_MAX_LEN = 60

_TEXT_TYPES = ("varchar", "nvarchar", "char", "nchar")


def quote_column(col: str) -> str:
    """Bracket a column name only when T-SQL needs it (e.g. 'PDO Well ID')."""
    return col if _PLAIN_RE.match(col) else "[" + col + "]"


def _is_secret(col: str) -> bool:
    return bool(_SECRET_RE.search(col))


def _is_included_table(schema: str, table: str) -> bool:
    """True when INCLUDED_TABLES is blank (everything) or names this table."""
    if not settings.included_tables:
        return True
    full = (schema + "." + table).lower()
    return full in settings.included_tables or table.lower() in settings.included_tables


def _is_excluded_table(schema: str, table: str) -> bool:
    full = (schema + "." + table).lower()
    return full in settings.excluded_tables or table.lower() in settings.excluded_tables


def _keep_table(schema: str, table: str) -> bool:
    return _is_included_table(schema, table) and not _is_excluded_table(schema, table)


def _is_excluded_column(schema: str, table: str, column: str) -> bool:
    if _is_secret(column):
        return True
    schema, table, column = schema.lower(), table.lower(), column.lower()
    candidates = (schema + "." + table + "." + column, table + "." + column, column)
    return any(candidate in settings.excluded_columns for candidate in candidates)


def _placeholders() -> str:
    return ",".join("?" for _ in settings.allowed_schemas)


# -- Catalogue reads -----------------------------------------------------------


_COLUMNS_SQL = """
    SELECT TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME, DATA_TYPE,
           CHARACTER_MAXIMUM_LENGTH, IS_NULLABLE
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE LOWER(TABLE_SCHEMA) IN ({placeholders})
    ORDER BY TABLE_SCHEMA, TABLE_NAME, ORDINAL_POSITION
"""

_PK_SQL = """
    SELECT s.name, t.name, c.name
    FROM sys.key_constraints kc
    JOIN sys.tables   t ON t.object_id = kc.parent_object_id
    JOIN sys.schemas  s ON s.schema_id = t.schema_id
    JOIN sys.index_columns ic
      ON ic.object_id = kc.parent_object_id AND ic.index_id = kc.unique_index_id
    JOIN sys.columns  c ON c.object_id = ic.object_id AND c.column_id = ic.column_id
    WHERE kc.type = 'PK' AND LOWER(s.name) IN ({placeholders})
"""

_FK_SQL = """
    SELECT ps.name, pt.name, pc.name, rs.name, rt.name, rc.name
    FROM sys.foreign_key_columns fkc
    JOIN sys.tables  pt ON pt.object_id = fkc.parent_object_id
    JOIN sys.schemas ps ON ps.schema_id = pt.schema_id
    JOIN sys.columns pc ON pc.object_id = fkc.parent_object_id
                       AND pc.column_id = fkc.parent_column_id
    JOIN sys.tables  rt ON rt.object_id = fkc.referenced_object_id
    JOIN sys.schemas rs ON rs.schema_id = rt.schema_id
    JOIN sys.columns rc ON rc.object_id = fkc.referenced_object_id
                       AND rc.column_id = fkc.referenced_column_id
    WHERE LOWER(ps.name) IN ({placeholders})
"""

_CONSTRAINT_SQL = """
    SELECT s.name, t.name, o.name, o.type
    FROM sys.objects o
    JOIN sys.tables  t ON t.object_id = o.parent_object_id
    JOIN sys.schemas s ON s.schema_id = t.schema_id
    WHERE o.type IN ('PK', 'F', 'C', 'D', 'UQ') AND LOWER(s.name) IN ({placeholders})
    ORDER BY s.name, t.name, o.name
"""

_ROWCOUNT_SQL = """
    SELECT s.name, t.name, SUM(p.row_count)
    FROM sys.dm_db_partition_stats p
    JOIN sys.tables  t ON t.object_id = p.object_id
    JOIN sys.schemas s ON s.schema_id = t.schema_id
    WHERE p.index_id IN (0, 1) AND LOWER(s.name) IN ({placeholders})
    GROUP BY s.name, t.name
    ORDER BY s.name, t.name
"""


def _run(cur, template: str):
    cur.execute(template.format(placeholders=_placeholders()), *settings.allowed_schemas)
    return cur.fetchall()


def _fetch_columns(cur) -> dict[str, list[tuple]]:
    tables: dict[str, list[tuple]] = {}
    for schema, table, column, dtype, maxlen, nullable in _run(cur, _COLUMNS_SQL):
        if not _keep_table(schema, table) or _is_excluded_column(schema, table, column):
            continue
        tables.setdefault(schema + "." + table, []).append((column, dtype, maxlen, nullable))
    return tables


def _fetch_primary_keys(cur) -> dict[str, set[str]]:
    keys: dict[str, set[str]] = {}
    for schema, table, column in _run(cur, _PK_SQL):
        keys.setdefault(schema + "." + table, set()).add(column)
    return keys


def _fetch_foreign_keys(cur, visible: set[str]) -> dict[str, list[str]]:
    relations: dict[str, list[str]] = {}
    for pschema, ptable, pcol, rschema, rtable, rcol in _run(cur, _FK_SQL):
        parent = pschema + "." + ptable
        target = rschema + "." + rtable
        # A relation pointing at a table the agents cannot see is worse than no relation: it
        # invites a join against something absent from the schema block.
        if parent not in visible or target not in visible:
            continue
        relations.setdefault(parent, []).append(pcol + " -> " + target + "." + rcol)
    return relations


def _detect_duplicate_keys(cur, tables, pks) -> dict[str, str]:
    """Tables holding more rows than distinct key values.

    This is the single most valuable marker in the block. milestone_rules.md §5.1 requires the
    task history to be reduced to one row per task before anything reads it, and warns that
    skipping it corrupts every count downstream *silently*, because the query still succeeds.
    A table with a single-column primary key is one-row-per-key by definition and is skipped.
    """
    found: dict[str, str] = {}
    for table, cols in tables.items():
        table_pk = pks.get(table, set())
        if len(table_pk) == 1:
            continue
        candidates = sorted(table_pk) or [c[0] for c in cols if c[0].lower().endswith("id")]
        if not candidates:
            continue
        key = candidates[0]
        schema, _, bare = table.partition(".")
        try:
            cur.execute(
                "SELECT COUNT(*), COUNT(DISTINCT [" + key + "]) FROM [" + schema + "].[" + bare + "]"
            )
            total, distinct = cur.fetchone()
        except Exception:  # noqa: BLE001 - a table we cannot count must not break introspection
            continue
        if total and distinct and total > distinct:
            found[table] = key
    return found


def _render(tables, pks, fks, dup_keys) -> str:
    lines: list[str] = []
    for table, cols in tables.items():
        lines.append("TABLE " + table)
        table_pk = pks.get(table, set())
        for col, dtype, maxlen, nullable in cols:
            typ = dtype + ("(" + str(maxlen) + ")" if maxlen and maxlen > 0 else "")
            null = "" if nullable == "YES" else " NOT NULL"
            pk = " PK" if col in table_pk else ""
            lines.append("  - " + quote_column(col) + " " + typ + null + pk)
        if table in dup_keys:
            key = dup_keys[table]
            lines.append(
                "  MANY ROWS PER " + key + " - reduce to one row per " + key
                + " (ROW_NUMBER, GROUP BY or COUNT(DISTINCT ...)) before reading it per-" + key
            )
        for relation in fks.get(table, []):
            lines.append("  FK: " + relation)
        lines.append("")
    return "\n".join(lines).strip()


# -- Fingerprint ---------------------------------------------------------------


def _identity() -> str:
    """What .env points at, and how much of it is in scope.

    Pointing .env at another database must not reuse this one's work. The table filters are
    folded in for the same reason - narrowing INCLUDED_TABLES changes what an agent is allowed to
    see without touching the database, so without this the setting would appear to do nothing.
    """
    return "|".join(
        [
            settings.db_server,
            settings.db_name,
            ",".join(settings.allowed_schemas),
            ",".join(sorted(settings.included_tables)),
            ",".join(sorted(settings.excluded_tables)),
        ]
    )


def structure_fingerprint(cur) -> str:
    """Hash of what decides whether an ALREADY-WRITTEN QUERY is still valid.

    ⚠ DELIBERATELY NARROWER THAN _fingerprint BELOW, and the difference is the entire reason
    frozen SQL is worth keeping (see app/frozen.py).

    What is in: the database identity, the table scope, every column with its declared type, and
    the constraints. Each of those can break a written query - a renamed column, a retyped join
    key, a dropped foreign key that the query's join assumed.

    What is OUT, and why:

    * ROW COUNTS. A query written against a schema stays correct when rows are loaded. They are
      in the cache key below because the value HINTS are sampled from live data, but keying
      frozen SQL on them would discard every query on every ingest - in a database that loads
      daily, nothing would ever be reused, which is the exact outcome freezing exists to avoid.
    * THE RENDERER VERSION. It changes the text shown to the agents, not the validity of SQL
      already written. Bumping it must rebuild the schema block without invalidating queries
      that are still perfectly correct.
    """
    parts: list[str] = [_identity()]
    parts += [str(row) for row in _run(cur, _COLUMNS_SQL)]
    # Constraints do not appear in INFORMATION_SCHEMA.COLUMNS, so without this a dropped foreign
    # key or a new CHECK would leave the hash untouched.
    parts += [str(row) for row in _run(cur, _CONSTRAINT_SQL)]
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def live_structure_fingerprint() -> str:
    """The structural fingerprint, on a connection of its own.

    For callers that have no cursor to hand - the API's drift check, and the CLI's report.
    """
    conn = get_connection()
    try:
        return structure_fingerprint(conn.cursor())
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


def _fingerprint(cur) -> str:
    """Hash of everything that should invalidate the CACHED SCHEMA BLOCK AND VALUE HINTS.

    Broader than the structural hash above: it also covers how the block is rendered, and how
    much data the hints were sampled from.
    """
    parts: list[str] = [_RENDERER_VERSION, structure_fingerprint(cur)]
    # Row counts from catalogue statistics - one metadata read, no table scan. This is what makes
    # a rerun pick up newly LOADED DATA and not only structural change.
    try:
        parts += [str(row) for row in _run(cur, _ROWCOUNT_SQL)]
    except Exception as exc:  # noqa: BLE001 - no permission on the DMV is not fatal
        log.debug("introspect: row-count statistics unavailable (%s)", exc)

    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read().strip()
    except OSError:
        return ""


def _write(path: str, text: str) -> None:
    os.makedirs(_CACHE_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


# -- Value hints ---------------------------------------------------------------


def _build_value_hints(cur, tables: dict[str, list[tuple]]) -> str:
    """Real coded values from small lookup tables.

    Only code-like columns with few distinct short values are sampled. The point is to let a
    filter be written against a value that exists - business_rules §2 notes that nationality is
    exactly 'National'/'Expat', and that guessing 'Omani' or 'Local' silently matches nothing.
    """
    lines: list[str] = []
    for table, cols in sorted(tables.items()):
        schema, _, bare = table.partition(".")
        candidates = [
            col
            for col, dtype, maxlen, _ in cols
            if _HINT_NAME_RE.search(col)
            and dtype.lower() in _TEXT_TYPES
            and (not maxlen or 0 < maxlen <= _HINT_MAX_LEN)
        ]
        if not candidates:
            continue

        collected: list[str] = []
        for col in candidates[:4]:
            try:
                cur.execute(
                    "SELECT DISTINCT TOP " + str(_HINT_MAX_DISTINCT + 1) + " [" + col + "]"
                    + " FROM [" + schema + "].[" + bare + "]"
                    + " WHERE [" + col + "] IS NOT NULL AND LEN([" + col + "]) > 0"
                    + " ORDER BY [" + col + "]"
                )
                values = [str(r[0]).strip() for r in cur.fetchall()]
            except Exception:  # noqa: BLE001 - one unreadable column must not stop the rest
                continue
            # More than the cap means it is not a lookup column - listing a truncated sample
            # would imply those are the only values, which is worse than saying nothing.
            if not values or len(values) > _HINT_MAX_DISTINCT:
                continue
            collected.append(col + ": " + "; ".join(values))

        if collected:
            lines.append("- " + table + " - " + " | ".join(collected))

    if not lines:
        return ""
    header = (
        "VALUE HINTS (real values read from this database - filter, join and decode using these "
        "ACTUAL values rather than guessing them):"
    )
    return "\n".join([header] + lines)


# -- Public entry point --------------------------------------------------------


def detect(use_cache: bool = True) -> tuple[str, str]:
    """Return (schema_block, value_hints), rebuilding whenever the live database has moved.

    The cache is trusted ONLY when the live fingerprint still matches the one it was built from.
    A drift check that itself fails falls back to the cache rather than to the database: a
    transient permission or network error should degrade to slightly stale grounding, not to none.
    """
    conn = get_connection()
    try:
        cur = conn.cursor()

        cached_schema, cached_hints = _read(_SCHEMA_PATH), _read(_HINTS_PATH)
        if use_cache and cached_schema:
            try:
                live = _fingerprint(cur)
            except Exception as exc:  # noqa: BLE001
                log.warning("introspect: drift check failed (%s) - using the cached schema", exc)
                return cached_schema, cached_hints
            if live == _read(_FINGERPRINT_PATH):
                log.info("introspect: cache is current (%d chars)", len(cached_schema))
                return cached_schema, cached_hints
            log.info("introspect: the database has changed since the cache was built - rebuilding")

        tables = _fetch_columns(cur)
        if not tables:
            raise RuntimeError(
                "Introspection found no tables. Check ALLOWED_SCHEMAS (currently "
                + ", ".join(settings.allowed_schemas) + ") and INCLUDED_TABLES (currently "
                + (", ".join(settings.included_tables) or "blank") + ") in .env."
            )

        # A typo in the allowlist hides a table silently: the query still runs, the author simply
        # never sees that table and writes around it. Name the entries that matched nothing - and,
        # where the cause is knowable, name the cause.
        if settings.included_tables:
            matched = {t.lower() for t in tables} | {t.split(".", 1)[-1].lower() for t in tables}
            unmatched = [t for t in settings.included_tables if t not in matched]
            if unmatched:
                log.warning(
                    "introspect: INCLUDED_TABLES entries matched no table: %s", ", ".join(unmatched)
                )
                # By far the most likely cause, and the one the bare warning hides: the entry
                # names a schema that ALLOWED_SCHEMAS does not scan, so the table was filtered
                # out before the allowlist was applied. Saying "matched no table" sends you
                # hunting for a typo in a name that is perfectly correct.
                missing = sorted(
                    {
                        entry.split(".", 1)[0]
                        for entry in unmatched
                        if "." in entry and entry.split(".", 1)[0] not in settings.allowed_schemas
                    }
                )
                if missing:
                    log.warning(
                        "introspect: -> schema(s) %s are not in ALLOWED_SCHEMAS (currently %s). "
                        "ALLOWED_SCHEMAS is applied FIRST, so those tables were never read. Add "
                        "them to ALLOWED_SCHEMAS in .env.",
                        ", ".join(missing), ", ".join(settings.allowed_schemas),
                    )
        pks = _fetch_primary_keys(cur)
        fks = _fetch_foreign_keys(cur, visible=set(tables))
        dup_keys = _detect_duplicate_keys(cur, tables, pks)
        schema = _render(tables, pks, fks, dup_keys)
        hints = _build_value_hints(cur, tables)

        # The fingerprint is taken LAST, so it describes the database the artefacts were actually
        # built from. Taken first, a change landing mid-build would be stamped as already captured.
        _write(_SCHEMA_PATH, schema)
        _write(_HINTS_PATH, hints)
        _write(_FINGERPRINT_PATH, _fingerprint(cur))

        log.info(
            "introspect: %d tables, %d with many-rows-per-key, schema %d chars, hints %d chars",
            len(tables), len(dup_keys), len(schema), len(hints),
        )
        return schema, hints
    finally:
        conn.close()


def grounding(schema: str, hints: str) -> str:
    """The schema block and value hints as one prompt section."""
    return schema if not hints else schema + "\n\n" + hints
