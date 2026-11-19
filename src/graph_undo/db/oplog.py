"""OpLogRepo: the ops table and the removals (removed_nodes, removed_edges). Never commits."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from graph_undo.domain.ops import Op, canonical

_COLS = "op_id, actor_kind, actor_name, op, effect, node_id, edge_id, before, payload"


@dataclass(frozen=True)
class LoggedOp:
    """One ops row, minus seq (assigned by SQLite in apply order)."""
    op_id: str
    actor_kind: str
    actor_name: str
    op: str
    effect: str               # 'applied' | 'noop'
    node_id: str | None
    edge_id: int | None
    before: str | None        # JSON (canonical) of edit_node's previous values
    payload: str              # canonical(op.raw)

    @classmethod
    def of(cls, op: Op, effect: str, *, node_id: str | None = None, edge_id: int | None = None,
           before: str | None = None) -> "LoggedOp":
        """The row for `op`: actor, kind and payload (= canonical(op.raw)) filled in from the op."""
        return cls(op.op_id, op.actor.kind, op.actor.name, op.kind, effect, node_id, edge_id, before,
                   canonical(op.raw))


class OpLogRepo:
    def __init__(self, con: sqlite3.Connection):
        self.con = con

    def get(self, op_id: str) -> LoggedOp | None:
        row = self.con.execute(f"SELECT {_COLS} FROM ops WHERE op_id = ?", (op_id,)).fetchone()
        return None if row is None else LoggedOp(*row)

    def record(self, entry: LoggedOp) -> None:
        self.con.execute(
            f"INSERT INTO ops ({_COLS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (entry.op_id, entry.actor_kind, entry.actor_name, entry.op, entry.effect, entry.node_id,
             entry.edge_id, entry.before, entry.payload))

    def add_removals(self, op_id: str, node_ids: list[str], edge_ids: list[int]) -> None:
        """Record what op_id removed. The ops row must already exist (foreign key)."""
        self.con.execute(
            "INSERT INTO removed_nodes (op_id, node_id) SELECT ?, value FROM json_each(?)",
            (op_id, json.dumps(list(node_ids))))
        self.con.execute(
            "INSERT INTO removed_edges (op_id, edge_id) SELECT ?, value FROM json_each(?)",
            (op_id, json.dumps([int(i) for i in edge_ids])))

    def removals(self, op_id: str) -> tuple[list[str], list[int]]:
        """(node ids, edge ids) removed by op_id, in the order they were recorded."""
        nodes = [r[0] for r in self.con.execute(
            "SELECT node_id FROM removed_nodes WHERE op_id = ? ORDER BY rowid", (op_id,))]
        edges = [r[0] for r in self.con.execute(
            "SELECT edge_id FROM removed_edges WHERE op_id = ? ORDER BY rowid", (op_id,))]
        return nodes, edges

    def seq(self, op_id: str) -> int | None:
        row = self.con.execute("SELECT seq FROM ops WHERE op_id = ?", (op_id,)).fetchone()
        return None if row is None else row[0]

    def last_op_id(self) -> str | None:
        """The op_id with the highest seq, or None if nothing was applied."""
        row = self.con.execute("SELECT op_id FROM ops ORDER BY seq DESC LIMIT 1").fetchone()
        return None if row is None else row[0]

    def creators(self, node_ids: list[str]) -> dict[str, tuple[str, str]]:
        """node id -> (op_id, actor_kind) of the applied add_node that created it. Seed nodes are absent."""
        rows = self.con.execute(
            "SELECT node_id, op_id, actor_kind FROM ops WHERE op = 'add_node' AND effect = 'applied' "
            "AND node_id IN (SELECT value FROM json_each(?))", (json.dumps(list(node_ids)),))
        return {n: (o, k) for n, o, k in rows}
