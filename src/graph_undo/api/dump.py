"""dump: the live graph as sorted JSONL (spec: docs/superpowers/specs/2026-11-19-dump-design.md)."""

from __future__ import annotations

import json
from pathlib import Path

from graph_undo.db.connection import DbError, connect_readonly
from graph_undo.db.dump import live_graph
from graph_undo.db.migrate import MigrationError, pending


class DumpError(Exception):
    """dump cannot run (no output is produced)."""


def _line(obj: dict) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"


def dump_lines(db_path: str | Path) -> list[str]:
    """Node lines sorted by qualified_name, then edge lines sorted by (from_id, to_id, type)."""
    db_path = Path(db_path)
    if not db_path.is_file():
        raise DumpError(f"no database at {db_path}; run: uv run load <seed.jsonl> --db {db_path}")
    try:
        con = connect_readonly(db_path)
    except DbError as e:
        raise DumpError(f"{db_path}: cannot open database: {e}") from e
    try:
        todo = pending(con)
        if todo:
            raise DumpError(f"database needs migrations {todo}; run: uv run migrate --db {db_path}")
        nodes, edges = live_graph(con)
    except MigrationError as e:
        raise DumpError(f"{db_path}: {e.message}") from e
    except DbError as e:
        raise DumpError(f"{db_path}: not a usable SQLite database: {e}") from e
    finally:
        con.close()
    # re-sort in Python: byte order independent of SQLite collation
    node_lines = [_line(n) for n in sorted(nodes, key=lambda n: n["qualified_name"])]
    edge_lines = [_line(e) for e in sorted(edges, key=lambda e: (e["from_id"], e["to_id"], e["type"]))]
    return node_lines + edge_lines
