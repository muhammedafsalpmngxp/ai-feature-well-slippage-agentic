"""The frozen query store - verified SQL on disk under sql/, reused until the schema moves.

WHY IT EXISTS. Authoring and verifying the three queries costs a few minutes of LLM latency and
real tokens. What it produces is a deterministic artefact: a SELECT that was proved correct
against one specific schema. Nothing about that query changes when a row is loaded, so paying
for it again on the next run buys exactly nothing. The pipeline's expensive half becomes
occasional, and the cheap half - run the SQL, read the numbers - becomes every run:

    schema moved   ->  author, verify, freeze   (minutes, tokens)
    schema stable  ->  load from sql/, execute  (seconds, free)

WHAT INVALIDATES A FREEZE is `introspect.structure_fingerprint` - identity, table scope, columns
with their declared types, constraints - and deliberately NOT the broader fingerprint that
introspection keys its cached schema block on. That one also folds in row counts, because the
value hints are sampled from live data. Keying the freeze on it would discard every query on
every ingest, so in a database that loads daily nothing would ever be reused. See the note on
`structure_fingerprint` itself.

WHY sql/ IS COMMITTED, unlike out/. These files are the reviewed artefact: what a fresh clone
needs to serve the dashboard without an API key, and the thing to read in a diff when someone
asks what changed about how slippage is calculated. out/ holds the products of one run - rows,
a brief - which are reproducible from these plus the database.

A CHANGED OUTPUT CONTRACT INVALIDATES A FREEZE TOO. When a query's spec gains or loses a column
(activity_delay gaining crew_id and crew_type_id, say), the frozen SQL still runs perfectly - and
returns the old columns. So a freeze is only current when the columns it was frozen with are the
columns its spec now requires (FrozenQuery.matches_contract); otherwise it is re-authored.

HAND EDITING IS ALLOWED AND REPORTED. The manifest records a hash of each body, so an edit made
after freezing is visible (`python main.py --frozen`) instead of passing silently as something
the Verifier approved. It is not overwritten or refused: a person correcting a query is doing
something legitimate, and destroying that work to protect a label would be the worse failure.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock

from app.observability import get_logger

log = get_logger()

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SQL_DIR = os.environ.get("SLIPPAGE_SQL_DIR") or os.path.join(_ROOT, "sql")
MANIFEST_PATH = os.path.join(SQL_DIR, "manifest.json")

MANIFEST_VERSION = 1

# Everything after this line is the query body, and the manifest's hash covers exactly that.
# An explicit sentinel rather than "skip the leading comments": a body may legitimately open with
# a comment of its own, and stripping it would change the hash and report a hand edit that never
# happened.
_SENTINEL = "-- ---- frozen SQL below ------------------------------------------------------"

# Read-modify-write of one shared file. Freezing happens on one thread today, but the API reads
# the manifest on request threads, and a half-written manifest is indistinguishable from a
# corrupt one.
_LOCK = Lock()


@dataclass(frozen=True)
class FrozenQuery:
    key: str
    sql: str
    fingerprint: str
    frozen_at: str
    columns: tuple[str, ...]
    row_count: int
    # True when the file's body no longer hashes to what was frozen, i.e. someone edited it.
    hand_edited: bool

    def is_current(self, live_fingerprint: str) -> bool:
        """Whether this query was verified against the schema that is live right now."""
        return bool(live_fingerprint) and self.fingerprint == live_fingerprint

    def matches_contract(self, expected) -> bool:
        """Whether this query was frozen with the columns its spec requires NOW.

        A spec that gained or lost a column leaves the frozen SQL runnable but wrong: it returns
        the old columns. That makes the freeze stale exactly as a schema change does.

        No recorded columns (a file dropped in by hand) is not treated as a mismatch: it cannot
        be judged here, and the reuse step re-checks the columns it actually gets back, so
        nothing with the wrong shape can slip through either way.
        """
        if not self.columns:
            return True
        return {c.lower() for c in self.columns} == {str(c).lower() for c in expected}


def path_for(key: str) -> str:
    return os.path.join(SQL_DIR, key + ".sql")


def _body_hash(sql: str) -> str:
    """Hash of the query text, insensitive to trailing whitespace and line endings.

    Normalised because git on Windows rewrites line endings on checkout: without this, a clone
    with `core.autocrlf=true` would report every frozen query as hand-edited.
    """
    normalised = "\n".join(line.rstrip() for line in sql.strip().splitlines())
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()


def _header(key: str, label: str, fingerprint: str, frozen_at: str,
            columns: tuple[str, ...], row_count: int) -> str:
    """Provenance, in the file itself, for whoever opens it in six months."""
    lines = [
        "-- " + key + " - " + label,
        "--",
        "-- Written and verified by the agent pipeline, then FROZEN for reuse. It is regenerated",
        "-- only when the database's structural fingerprint changes; a data load does not",
        "-- invalidate it. To force a rewrite: python main.py --regenerate",
        "--",
        "-- Frozen at:  " + frozen_at,
        "-- Schema:     " + fingerprint,
        "-- Rows then:  " + str(row_count),
    ]
    # Twenty column names on one line is unreadable in a diff.
    if columns:
        wrapped, current = [], "-- Contract:   "
        for i, col in enumerate(columns):
            piece = col + ("," if i < len(columns) - 1 else "")
            if len(current) + len(piece) + 1 > 96:
                wrapped.append(current)
                current = "--             "
            current += piece + " "
        wrapped.append(current.rstrip())
        lines += wrapped
    lines += [
        "--",
        "-- Editing this by hand is allowed, but it voids the verification: the hash in",
        "-- manifest.json stops matching and `python main.py --frozen` reports the file as",
        "-- hand-edited rather than as approved.",
        _SENTINEL,
    ]
    return "\n".join(lines)


def _read_manifest() -> dict:
    try:
        with open(MANIFEST_PATH, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {"version": MANIFEST_VERSION, "queries": {}}
    if not isinstance(data, dict) or not isinstance(data.get("queries"), dict):
        log.warning("frozen: manifest.json is not in the expected shape - treating it as empty")
        return {"version": MANIFEST_VERSION, "queries": {}}
    return data


def _write_manifest(data: dict) -> None:
    """Write via a temporary file, so a crash mid-write cannot leave a truncated manifest."""
    os.makedirs(SQL_DIR, exist_ok=True)
    temp = MANIFEST_PATH + ".tmp"
    with open(temp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False, sort_keys=True)
        handle.write("\n")
    os.replace(temp, MANIFEST_PATH)


def body_of(text: str) -> str:
    """The query body: everything after the sentinel, or the whole file when there is none.

    The fallback is what lets a query frozen by hand - or one copied out of out/ - be picked up
    without ceremony.
    """
    marker = text.find(_SENTINEL)
    if marker == -1:
        return text.strip()
    return text[marker + len(_SENTINEL):].strip()


def load(key: str) -> FrozenQuery | None:
    """One frozen query, or None when it has never been frozen or cannot be read.

    Returns None rather than raising: a missing freeze is the ordinary state on a first run, and
    the caller's response is the same either way - author it.
    """
    path = path_for(key)
    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
    except OSError:
        return None

    body = body_of(text)
    if not body:
        log.warning("frozen: %s is empty - it will be regenerated", path)
        return None

    record = _read_manifest()["queries"].get(key) or {}
    recorded_hash = str(record.get("sql_sha256", ""))
    hand_edited = bool(recorded_hash) and recorded_hash != _body_hash(body)
    if hand_edited:
        # Debug, not a warning: load() runs from the router AND from the node that uses the
        # query, so warning here printed the same line twice a run. The one place it matters is
        # where the query is actually put to use, and reuse_node warns there.
        log.debug("frozen: %s has been edited since it was frozen", os.path.basename(path))
    if not recorded_hash:
        # A .sql file with no manifest entry: dropped in by hand, or left from an older layout.
        # Usable, but nothing here can claim it was ever verified.
        log.info("frozen: %s has no manifest entry - using it, unverified", os.path.basename(path))

    return FrozenQuery(
        key=key,
        sql=body,
        fingerprint=str(record.get("fingerprint", "")),
        frozen_at=str(record.get("frozen_at", "")),
        columns=tuple(record.get("columns") or ()),
        row_count=int(record.get("row_count") or 0),
        hand_edited=hand_edited,
    )


def load_all(keys) -> dict[str, FrozenQuery]:
    """Every requested key that is frozen, keyed by key. Absent ones are simply missing."""
    found = {}
    for key in keys:
        query = load(key)
        if query is not None:
            found[key] = query
    return found


def freeze(key: str, label: str, sql: str, fingerprint: str,
           columns, row_count: int) -> str:
    """Write one VERIFIED query to sql/ and record what it was verified against.

    ⚠ Only ever call this for a result the Verifier approved. Freezing an unverified query would
    launder it into the store that everything downstream treats as reviewed, and the next run
    would reuse it without ever asking again.
    """
    if not sql or not sql.strip():
        raise ValueError("refusing to freeze an empty query for " + key)
    if not fingerprint:
        raise ValueError(
            "refusing to freeze " + key + " with no schema fingerprint - there would be nothing "
            "to decide later whether it is still valid, so it would be reused forever"
        )

    body = sql.strip()
    frozen_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    columns = tuple(str(c) for c in (columns or ()))

    os.makedirs(SQL_DIR, exist_ok=True)
    path = path_for(key)
    text = _header(key, label, fingerprint, frozen_at, columns, row_count) + "\n" + body + "\n"
    temp = path + ".tmp"
    with open(temp, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.replace(temp, path)

    with _LOCK:
        data = _read_manifest()
        data["version"] = MANIFEST_VERSION
        data["queries"][key] = {
            "fingerprint": fingerprint,
            "frozen_at": frozen_at,
            "sql_sha256": _body_hash(body),
            "columns": list(columns),
            "row_count": int(row_count),
        }
        _write_manifest(data)

    log.info("frozen: wrote %s (%d chars, schema %s)", path, len(body), fingerprint[:12])
    return path


def partition(keys, live_fingerprint: str) -> tuple[list[str], list[str]]:
    """Split the requested keys into (reusable, needs_authoring).

    A key is reusable only when it is on disk AND was verified against the fingerprint that is
    live now. An empty `live_fingerprint` makes everything need authoring: not knowing what the
    database looks like is not a reason to trust yesterday's answer.
    """
    from app.graph import queries as query_specs

    reusable, stale = [], []
    frozen = load_all(keys)
    for key in keys:
        query = frozen.get(key)
        if (
            query is not None
            and query.is_current(live_fingerprint)
            and query.matches_contract(query_specs.QUERIES_BY_KEY[key].contract)
        ):
            reusable.append(key)
        else:
            stale.append(key)
    return reusable, stale


def describe(live_fingerprint: str = "") -> list[dict]:
    """One row per frozen query, for the CLI report and the API's status.

    `state` is the single word a reader needs: current, stale, hand-edited or missing.
    """
    from app.graph import queries as query_specs

    rows: list[dict] = []
    for spec in query_specs.QUERIES:
        query = load(spec.key)
        if query is None:
            rows.append({
                "key": spec.key, "label": spec.label, "state": "missing",
                "frozen_at": None, "fingerprint": None, "columns": [], "row_count": 0,
                "hand_edited": False, "contract_changed": False,
            })
            continue
        # Knowable without the database, so it is judged before the fingerprint: a query whose
        # spec now wants different columns is stale whatever the schema says.
        contract_changed = not query.matches_contract(spec.contract)
        if query.hand_edited:
            state = "hand-edited"
        elif not query.fingerprint:
            state = "unverified"
        elif contract_changed:
            state = "stale"
        elif not live_fingerprint:
            state = "unknown"
        elif query.is_current(live_fingerprint):
            state = "current"
        else:
            state = "stale"
        rows.append({
            "key": spec.key,
            "label": spec.label,
            "state": state,
            "frozen_at": query.frozen_at or None,
            "fingerprint": query.fingerprint or None,
            "columns": list(query.columns),
            "row_count": query.row_count,
            "hand_edited": query.hand_edited,
            "contract_changed": contract_changed,
        })
    return rows
