"""Run the well slippage and activity delay detection pipeline.

    python main.py                  detect, then print the brief
    python main.py --refresh        re-introspect the database, ignoring the cache
    python main.py --sql            also print every query the agents wrote
    python main.py --out DIR        write the brief, each query's SQL and its rows to DIR
    python main.py --only KEY       run just one query
    python main.py --well 33785     drill the detail query into this well
    python main.py --rework         summarise what rework has cost, worst cause first
    python main.py --detect-only    just introspect and show what was detected
    python main.py --frozen         report which queries in sql/ are still valid, then stop
    python main.py --regenerate     re-author every query, ignoring the frozen SQL in sql/

The verified SQL is FROZEN into sql/ and reused until the database's structure changes, so an
ordinary run costs a few seconds rather than a few minutes and touches no LLM at all. See
app/frozen.py for what counts as a change.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys

from app.config import settings
from app.db.connection import ping
from app.graph import queries
from app.observability import get_logger

log = get_logger()


def _write_outputs(directory: str, state: dict) -> list[str]:
    """Write the brief once, then each query's SQL and rows under its own name."""
    os.makedirs(directory, exist_ok=True)
    written: list[str] = []

    brief = os.path.join(directory, "brief.md")
    with open(brief, "w", encoding="utf-8") as handle:
        handle.write(state.get("explanation", "") + "\n")
    written.append(brief)

    for key, result in (state.get("results") or {}).items():
        if result.get("sql"):
            path = os.path.join(directory, key + ".sql")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(result["sql"] + "\n")
            written.append(path)

        columns, rows = result.get("columns") or [], result.get("rows") or []
        if columns:
            path = os.path.join(directory, key + ".csv")
            # newline="" per the csv module's contract - without it Windows writes a blank
            # line between every record.
            with open(path, "w", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(columns)
                writer.writerows(rows)
            written.append(path)

    if state.get("column_plan"):
        path = os.path.join(directory, "column_plan.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(state["column_plan"], handle, indent=2, ensure_ascii=False, default=str)
        written.append(path)

    return written


def _report_frozen() -> int:
    """Print what is in sql/ and whether it still matches the live database."""
    from app import frozen

    live = ""
    note = ""
    try:
        from app.db.introspect import live_structure_fingerprint

        live = live_structure_fingerprint()
    except Exception as exc:  # noqa: BLE001 - the report is useful without the database
        note = "Could not read the live schema (" + str(exc).split("\n")[0] + "), so nothing " \
               "below can be judged current."

    rows = frozen.describe(live)
    print("Frozen SQL in " + frozen.SQL_DIR)
    if live:
        print("Live structural fingerprint: " + live[:16])
    print("")
    width = max((len(r["key"]) for r in rows), default=3)
    for row in rows:
        stamp = (row["frozen_at"] or "")[:19].replace("T", " ")
        detail = ""
        if row["state"] == "current":
            detail = "frozen " + stamp + ", " + str(row["row_count"]) + " rows then"
        elif row["state"] == "stale":
            detail = "frozen " + stamp + " against schema " + (row["fingerprint"] or "?")[:16]
        elif row["state"] == "hand-edited":
            detail = "edited since it was frozen - no longer the verified text"
        elif row["state"] == "unverified":
            detail = "no manifest entry, so nothing records that it was ever verified"
        elif row["state"] == "missing":
            detail = "never frozen"
        print("  " + row["key"].ljust(width) + "  " + row["state"].ljust(12) + "  " + detail)

    if note:
        print("\n" + note)
    stale = [r["key"] for r in rows if r["state"] in ("stale", "missing")]
    if stale:
        print("\nThe next run will author: " + ", ".join(stale))
    elif live:
        print("\nEvery query is current - the next run costs no tokens and no agent time.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Detect well slippage and activity delay.")
    parser.add_argument("--refresh", action="store_true",
                        help="re-introspect the database, ignoring the cached schema")
    parser.add_argument("--sql", action="store_true", help="print every generated query")
    parser.add_argument("--out", metavar="DIR", help="write brief, SQL and rows to DIR")
    parser.add_argument("--only", metavar="KEY", choices=queries.DEFAULT_KEYS,
                        help="run only this query: " + " | ".join(queries.DEFAULT_KEYS))
    parser.add_argument("--well", metavar="ID",
                        help="well to drill into for the per-well detail query; "
                             "defaults to the worst well the summary finds")
    parser.add_argument("--rework", action="store_true",
                        help="summarise recorded rejections and execution failures, then stop")
    parser.add_argument("--detect-only", action="store_true",
                        help="introspect and report what was detected, then stop")
    parser.add_argument("--frozen", action="store_true",
                        help="report the state of the frozen SQL in sql/, then stop")
    parser.add_argument("--regenerate", action="store_true",
                        help="re-author every query with the agents, ignoring sql/")
    args = parser.parse_args()

    # Before the database check: the corpus is a local file, and a report should be readable
    # when the database is unreachable - that is often exactly when you want it.
    if args.rework:
        from app import rework

        print(rework.report())
        return 0

    # Also before it, for the same reason: which queries are frozen is a fact about sql/, and
    # only judging them CURRENT needs the database. Without it they report as "unknown", which
    # is the honest answer rather than a failure.
    if args.frozen:
        return _report_frozen()

    ok, message = ping()
    if not ok:
        print("Cannot reach the database: " + message, file=sys.stderr)
        print("Check DB_SERVER / DB_NAME / DB_USER / DB_PASSWORD in .env.", file=sys.stderr)
        return 2
    log.info("db: %s", message)

    if args.detect_only:
        from app.db.introspect import detect

        schema, hints = detect(use_cache=not args.refresh)
        tables = schema.count("TABLE ")
        markers = schema.count("MANY ROWS PER")
        print("Detected " + str(tables) + " tables (" + str(markers) + " many-rows-per-key).")
        print("Schema block: " + str(len(schema)) + " chars.  Value hints: " + str(len(hints)) + " chars.")
        print("Cached under .cache/ - rerun with --refresh to rebuild.")
        return 0

    if not settings.openai_api_key:
        print("OPENAI_API_KEY is not set in .env - the agents cannot run.", file=sys.stderr)
        return 2

    from app.graph.build import build_graph

    initial: dict = {"refresh_schema": args.refresh, "force_regenerate": args.regenerate}
    # --only narrows the Planner's worklist. The Planner still runs either way: the activity
    # query needs its task bindings and the slippage query needs its scenario bindings.
    if args.only:
        initial["only"] = args.only
    if args.well:
        initial["target_well_id"] = str(args.well).strip()

    graph = build_graph()
    # recursion_limit bounds the rewrite loops as a backstop. The retry budgets already do, but
    # a routing mistake must fail loudly rather than spin against a paid API. Two queries, each
    # with its own budgets, so this is roughly double the single-query ceiling.
    state = graph.invoke(initial, {"recursion_limit": 80})

    if args.sql:
        for key, result in (state.get("results") or {}).items():
            if not result.get("sql"):
                continue
            print("\n--- " + key + " " + "-" * max(4, 56 - len(key)))
            print(result["sql"])
            print("-" * 62 + "\n")

    print(state.get("explanation", "(no brief was produced)"))

    results = state.get("results") or {}
    unverified = [k for k, v in results.items() if not v.get("verified")]
    if unverified:
        print(
            "\n[!] NOT approved by the independent review: " + ", ".join(unverified),
            file=sys.stderr,
        )

    # Say which half of the pipeline actually ran. Without this the only visible difference
    # between a 15-second reuse and a 200-second authoring run is the wall clock.
    reused = list(state.get("reused_queries") or [])
    authored = [k for k in results if k not in reused]
    if reused:
        print("reused from sql/: " + ", ".join(reused), file=sys.stderr)
    if authored:
        print("authored and frozen: " + ", ".join(authored), file=sys.stderr)

    if args.out:
        for path in _write_outputs(args.out, state):
            print("wrote " + path, file=sys.stderr)

    # Exit non-zero unless EVERY query produced a verified result, so a scheduled run fails
    # visibly rather than quietly reporting on half the picture.
    return 0 if results and not unverified else 1


if __name__ == "__main__":
    raise SystemExit(main())
