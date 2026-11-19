"""Read the live graph for `dump`: one read transaction over the live views."""

from __future__ import annotations

import json
import sqlite3


def live_graph(con: sqlite3.Connection) -> tuple[list[dict], list[dict]]:
    """(nodes with effective_tags, edges) of the live graph, as seen at one moment."""
    con.execute("BEGIN")
    try:
        nodes = [
            {"id": i, "qualified_name": qn, "type": t, "description": d, "owner": o,
             "tags": json.loads(tags), "effective_tags": json.loads(eff)}
            for i, qn, t, d, o, tags, eff in con.execute(
                "SELECT n.id, n.qualified_name, n.type, n.description, n.owner, n.tags, e.tags "
                "FROM live_nodes n JOIN effective_tags e ON e.node_id = n.id ORDER BY n.qualified_name")
        ]
        edges = [{"from_id": f, "to_id": t, "type": ty} for f, t, ty in con.execute(
            "SELECT from_id, to_id, type FROM live_edges ORDER BY from_id, to_id, type")]
    finally:
        con.execute("COMMIT")
    return nodes, edges
