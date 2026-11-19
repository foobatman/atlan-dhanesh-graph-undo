"""NodeRepo: every node read and write. Takes and returns domain types; never commits."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping

from graph_undo.db.codec import encode_tags
from graph_undo.domain.model import AUTHORED_FIELDS, Node

_COLS = "id, qualified_name, type, description, owner, tags, deleted_by"


def _node(row) -> Node | None:
    if row is None:
        return None
    i, qn, type_, description, owner, tags, deleted_by = row
    return Node(i, qn, type_, description, owner, tuple(json.loads(tags)), deleted_by)


class NodeRepo:
    def __init__(self, con: sqlite3.Connection):
        self.con = con

    def live_by_name(self, qn: str) -> Node | None:
        return _node(self.con.execute(
            f"SELECT {_COLS} FROM nodes WHERE qualified_name = ? AND deleted_by IS NULL", (qn,)).fetchone())

    def by_id(self, node_id: str) -> Node | None:
        """Live or dead."""
        return _node(self.con.execute(f"SELECT {_COLS} FROM nodes WHERE id = ?", (node_id,)).fetchone())

    def insert(self, node: Node) -> None:
        self.con.execute(
            f"INSERT INTO nodes ({_COLS}) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (node.id, node.qualified_name, node.type, node.description, node.owner, encode_tags(node.tags),
             node.deleted_by))

    def update_fields(self, node_id: str, fields: Mapping[str, object]) -> None:
        """Replace authored fields (description/owner/tags) of a live node. Other keys are a bug: ValueError."""
        bad = sorted(set(fields) - set(AUTHORED_FIELDS))
        if bad:
            raise ValueError(f"update_fields: not an authored field: {bad}")
        if not fields:
            return
        keys = [k for k in AUTHORED_FIELDS if k in fields]
        values = [encode_tags(fields[k]) if k == "tags" else fields[k] for k in keys]
        cur = self.con.execute(
            f"UPDATE nodes SET {', '.join(f'{k} = ?' for k in keys)} WHERE id = ? AND deleted_by IS NULL",
            (*values, node_id))
        if cur.rowcount != 1:
            raise ValueError(f"update_fields: no live node with id {node_id!r}")

    def live_subtree_ids(self, root_id: str) -> list[str]:
        """The live root and every live descendant along live contains edges; root first, then by depth, id."""
        rows = self.con.execute(
            """
            WITH RECURSIVE sub(id, depth) AS (
              SELECT id, 0 FROM live_nodes WHERE id = ?
              UNION ALL
              SELECT e.to_id, sub.depth + 1
                FROM sub JOIN live_edges e ON e.from_id = sub.id AND e.type = 'contains'
                         JOIN live_nodes n ON n.id = e.to_id
            )
            SELECT id FROM sub ORDER BY depth, id
            """, (root_id,))
        return [r[0] for r in rows]

    def mark_deleted(self, node_ids: list[str], op_id: str) -> int:
        """Set deleted_by on the given rows that are still live. Returns the number of rows updated."""
        cur = self.con.execute(
            "UPDATE nodes SET deleted_by = ? "
            "WHERE deleted_by IS NULL AND id IN (SELECT value FROM json_each(?))",
            (op_id, json.dumps(list(node_ids))))
        return cur.rowcount
