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

    def by_ids(self, edge_ids: list[int]) -> list[Edge]:
        """Rows (live or dead) for the given ids, ordered by id. Unknown ids are absent."""
        rows = self.con.execute(
            f"SELECT {_COLS} FROM edges WHERE id IN (SELECT value FROM json_each(?)) ORDER BY id",
            (json.dumps([int(i) for i in edge_ids]),))
        return [Edge(*r) for r in rows]

    def revive(self, edge_ids: list[int], op_id: str) -> int:
        """Make live again the given rows whose deleted_by is op_id. Returns the number of rows updated."""
        cur = self.con.execute(
            "UPDATE edges SET deleted_by = NULL "
            "WHERE deleted_by = ? AND id IN (SELECT value FROM json_each(?))",
            (op_id, json.dumps([int(i) for i in edge_ids])))
        return cur.rowcount

    def live_triples(self, triples: list[tuple[str, str, str]]) -> set[tuple[str, str, str]]:
        """The given (from_id, to_id, type) triples that exist as live edges."""
        rows = self.con.execute(
            "SELECT e.from_id, e.to_id, e.type FROM edges e JOIN json_each(?) j "
            "ON e.from_id = j.value->>0 AND e.to_id = j.value->>1 AND e.type = j.value->>2 "
            "WHERE e.deleted_by IS NULL", (json.dumps([list(t) for t in triples]),))
        return {tuple(r) for r in rows}

    def live_parents(self, child_ids: list[str]) -> dict[str, Edge]:
        """The live contains edge into each given child that has one, keyed by child id."""
        rows = self.con.execute(
            f"SELECT {_COLS} FROM edges WHERE type = 'contains' AND deleted_by IS NULL "
            "AND to_id IN (SELECT value FROM json_each(?))", (json.dumps(list(child_ids)),))
        return {e.to_id: e for e in (Edge(*r) for r in rows)}
