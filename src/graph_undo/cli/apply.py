"""`uv run apply ops.jsonl [--db graph.db]`: apply operations in order, printing and skipping invalid ones."""

from __future__ import annotations

import argparse
import sys

from graph_undo.api.apply import ApplyError, apply_file
from graph_undo.domain.errors import OpError

DEFAULT_DB = "graph.db"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="apply", description="Apply ops.jsonl to the database.")
    parser.add_argument("ops", help="path to ops.jsonl")
    parser.add_argument("--db", default=DEFAULT_DB, help=f"SQLite file to apply to (default {DEFAULT_DB})")
    args = parser.parse_args(argv)

    def on_reject(line: int, e: OpError) -> None:
        print(f"{args.ops}:{line}: {e}", file=sys.stderr, flush=True)

    try:
        s = apply_file(args.ops, args.db, on_reject)
    except ApplyError as e:
        print(str(e), file=sys.stderr)
        return 1
    print(f"applied {args.ops} to {args.db}: applied={s.applied} noop={s.noop} skipped={s.skipped} "
          f"rejected={s.rejected}", file=sys.stderr)
    return 0
