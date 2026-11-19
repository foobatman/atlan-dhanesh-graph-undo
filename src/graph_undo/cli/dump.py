"""`uv run dump [--db graph.db]`: print the live graph as sorted JSONL on stdout."""

from __future__ import annotations

import argparse
import sys

from graph_undo.api.dump import DumpError, dump_lines

DEFAULT_DB = "graph.db"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dump", description="Print the live graph as sorted JSONL.")
    parser.add_argument("--db", default=DEFAULT_DB, help=f"SQLite file to read (default {DEFAULT_DB})")
    args = parser.parse_args(argv)
    try:
        lines = dump_lines(args.db)
    except DumpError as e:
        print(str(e), file=sys.stderr)
        return 1
    out = sys.stdout
    if hasattr(out, "reconfigure"):
        out.reconfigure(encoding="utf-8")
    try:
        out.write("".join(lines))
        out.flush()
    except BrokenPipeError:          # e.g. `uv run dump | head`: the reader left; not an error
        import os
        os.dup2(os.open(os.devnull, os.O_WRONLY), out.fileno())
    return 0
