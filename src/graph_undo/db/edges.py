"""EdgeRepo: every edge read and write. Takes and returns domain types; never commits."""

from __future__ import annotations

import json
import sqlite3

from graph_undo.domain.model import Edge

_COLS = "id, from_id, to_id, type, deleted_by"


def _edge(row) -> Edge | None:
    return None if row is None else Edge(*row)


class EdgeRepo:
    def __init__(self, con: sqlite3.Connection):
        self.con = con

    def live(self, from_id: str, to_id: str, type: str) -> Edge | None:
        return _edge(self.con.execute(
            f"SELECT {_COLS} FROM edges WHERE from_id = ? AND to_id = ? AND type = ? AND deleted_by IS NULL",
            (from_id, to_id, type)).fetchone())

    def live_parent(self, child_id: str) -> Edge | None:
        """The live contains edge into child_id, if any."""
        return _edge(self.con.execute(
            f"SELECT {_COLS} FROM edges WHERE to_id = ? AND type = 'contains' AND deleted_by IS NULL",
            (child_id,)).fetchone())

    def live_touching(self, node_ids: list[str]) -> list[Edge]:
        """Every live edge with either end in node_ids, any type, ordered by id."""
        ids = json.dumps(list(node_ids))
        rows = self.con.execute(
            f"SELECT {_COLS} FROM edges WHERE deleted_by IS NULL "
            "AND (from_id IN (SELECT value FROM json_each(?1)) OR to_id IN (SELECT value FROM json_each(?1))) "
            "ORDER BY id", (ids,))
        return [Edge(*r) for r in rows]

    def insert(self, from_id: str, to_id: str, type: str) -> Edge:
        cur = self.con.execute("INSERT INTO edges (from_id, to_id, type) VALUES (?, ?, ?)",
                               (from_id, to_id, type))
        return Edge(cur.lastrowid, from_id, to_id, type, None)

    def mark_deleted(self, edge_ids: list[int], op_id: str) -> int:
        """Set deleted_by on the given rows that are still live. Returns the number of rows updated."""
        cur = self.con.execute(
            "UPDATE edges SET deleted_by = ? "
            "WHERE deleted_by IS NULL AND id IN (SELECT value FROM json_each(?))",
            (op_id, json.dumps([int(i) for i in edge_ids])))
        return cur.rowcount
