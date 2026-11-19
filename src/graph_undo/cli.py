"""Command-line entry points: `uv run load …` (apply, undo and dump come in later specs)."""

from __future__ import annotations

import argparse
import sys

from graph_undo.store import LoadError, load_seed

DEFAULT_DB = "graph.db"


def load_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="load", description="Create the database from seed.jsonl.")
    parser.add_argument("seed", help="path to seed.jsonl")
    parser.add_argument("--db", default=DEFAULT_DB, help=f"SQLite file to (re)create (default {DEFAULT_DB})")
    args = parser.parse_args(argv)
    try:
        n_nodes, n_edges = load_seed(args.seed, args.db)
    except LoadError as e:
        print(f"{args.seed}:{e.line}: {e.message}", file=sys.stderr)
        return 1
    print(f"loaded {args.db}: nodes={n_nodes} edges={n_edges}", file=sys.stderr)
    return 0
