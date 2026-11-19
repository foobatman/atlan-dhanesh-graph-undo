"""`uv run migrate [--db graph.db]`: apply pending schema migrations."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from graph_undo.api.migrate import MigrationError, migrate_db

DEFAULT_DB = "graph.db"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="migrate", description="Apply pending schema migrations.")
    parser.add_argument("--db", default=DEFAULT_DB, help=f"SQLite file to migrate (default {DEFAULT_DB})")
    args = parser.parse_args(argv)
    if not Path(args.db).is_file():
        print(f"no database at {args.db}; run: uv run load <seed.jsonl> --db {args.db}", file=sys.stderr)
        return 1
    try:
        before, after = migrate_db(args.db)
    except MigrationError as e:
        print(f"{args.db}: {e}", file=sys.stderr)
        return 1
    if before == after:
        print(f"{args.db} is up to date ({after})", file=sys.stderr)
    else:
        print(f"migrated {args.db}: {before} -> {after}", file=sys.stderr)
    return 0
