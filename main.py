"""Run the well slippage detection pipeline.

    python main.py                  detect slippage and print the brief
    python main.py --refresh        re-introspect the database, ignoring the cache
    python main.py --sql            also print the query the agents wrote
    python main.py --out out/       write the brief, the SQL and the rows to files
    python main.py --detect-only    just introspect and show what was detected
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys

from app.config import settings
from app.db.connection import ping
from app.observability import get_logger

log = get_logger()


def _write_outputs(directory: str, state: dict) -> list[str]:
    os.makedirs(directory, exist_ok=True)
    written: list[str] = []

    brief = os.path.join(directory, "slippage_brief.md")
    with open(brief, "w", encoding="utf-8") as handle:
        handle.write(state.get("explanation", "") + "\n")
    written.append(brief)

    if state.get("sql"):
        path = os.path.join(directory, "slippage.sql")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(state["sql"] + "\n")
        written.append(path)

    columns, rows = state.get("columns") or [], state.get("rows") or []
    if columns:
        path = os.path.join(directory, "slipped_wells.csv")
        # newline="" per the csv module's contract - without it Windows writes a blank line
        # between every record.
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Detect well slippage.")
    parser.add_argument("--refresh", action="store_true",
                        help="re-introspect the database, ignoring the cached schema")
    parser.add_argument("--sql", action="store_true", help="print the generated query")
    parser.add_argument("--out", metavar="DIR", help="write brief, SQL and rows to DIR")
    parser.add_argument("--detect-only", action="store_true",
                        help="introspect and report what was detected, then stop")
    args = parser.parse_args()

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

    graph = build_graph()
    # recursion_limit bounds the rewrite loops as a backstop. The retry budgets already do, but a
    # routing mistake must fail loudly rather than spin against a paid API.
    state = graph.invoke({"refresh_schema": args.refresh}, {"recursion_limit": 40})

    if args.sql and state.get("sql"):
        print("\n--- generated SQL " + "-" * 44)
        print(state["sql"])
        print("-" * 62 + "\n")

    print(state.get("explanation", "(no brief was produced)"))

    if not state.get("verify_ok"):
        print("\n[!] This listing was NOT approved by the independent review.", file=sys.stderr)

    if args.out:
        for path in _write_outputs(args.out, state):
            print("wrote " + path, file=sys.stderr)

    # Exit non-zero when nothing trustworthy came back, so a scheduled run fails visibly.
    return 0 if state.get("verify_ok") and state.get("columns") else 1


if __name__ == "__main__":
    raise SystemExit(main())
