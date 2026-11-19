"""`uv run undo <op_id> [--db graph.db]`: undo one delete op and print the JSONL report."""

from __future__ import annotations

import argparse
import sys

from graph_undo.api.apply import ApplyError
from graph_undo.api.undo import undo_db
from graph_undo.domain.errors import OpError

DEFAULT_DB = "graph.db"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="undo", description="Undo one delete op (delete_node or delete_edge).")
    parser.add_argument("op_id", help="the op to undo, e.g. op-0001")
    parser.add_argument("--db", default=DEFAULT_DB, help=f"SQLite file (default {DEFAULT_DB})")
    args = parser.parse_args(argv)
    try:
        result = undo_db(args.db, args.op_id)
    except (ApplyError, OpError) as e:
        print(str(e), file=sys.stderr)
        return 1
    sys.stdout.write("".join(result.lines))
    sys.stdout.flush()
    n, e = result.summary()["node"], result.summary()["edge"]
    print(f"undid {args.op_id} on {args.db}: nodes restored={n['restored']} skipped={n['skipped']}; "
          f"edges restored={e['restored']} merged={e['merged']} skipped={e['skipped']}", file=sys.stderr)
    return 0
