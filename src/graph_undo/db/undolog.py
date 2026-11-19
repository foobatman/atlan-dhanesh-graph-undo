"""UndoLogRepo: the undos and undo_items tables. Never commits."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class UndoRecord:
    undo_seq: int
    op_id: str
    after_op_id: str      # the last applied op when the undo ran
    report: str           # the exact JSONL printed


@dataclass(frozen=True)
class UndoItem:
    kind: str             # 'node' | 'edge'
    item_id: str
    outcome: str          # 'restored' | 'merged' | 'skipped'
    reason_code: str | None
    new_edge_id: int | None


class UndoLogRepo:
    def __init__(self, con: sqlite3.Connection):
        self.con = con

    def get(self, op_id: str) -> UndoRecord | None:
        row = self.con.execute(
            "SELECT u.undo_seq, u.op_id, o.op_id, u.report FROM undos u JOIN ops o ON o.seq = u.after_seq "
            "WHERE u.op_id = ?", (op_id,)).fetchone()
        return None if row is None else UndoRecord(*row)

    def record(self, op_id: str, after_seq: int, report: str) -> int:
        """Insert the undos row; returns undo_seq. A second row for op_id raises IntegrityError."""
        cur = self.con.execute("INSERT INTO undos (op_id, after_seq, report) VALUES (?, ?, ?)",
                               (op_id, after_seq, report))
        return cur.lastrowid

    def add_items(self, op_id: str, items: list[UndoItem]) -> None:
        rows = [[i.kind, i.item_id, i.outcome, i.reason_code, i.new_edge_id] for i in items]
        self.con.execute(
            "INSERT INTO undo_items (op_id, kind, item_id, outcome, reason_code, new_edge_id) "
            "SELECT ?, value->>0, value->>1, value->>2, value->>3, value->>4 FROM json_each(?)",
            (op_id, json.dumps(rows)))

    def counts(self, op_id: str) -> dict[str, int]:
        """restored_nodes, restored_edges, merged, skipped for op_id's undo."""
        row = self.con.execute(
            "SELECT coalesce(sum(kind = 'node' AND outcome = 'restored'), 0), "
            "coalesce(sum(kind = 'edge' AND outcome = 'restored'), 0), "
            "coalesce(sum(outcome = 'merged'), 0), coalesce(sum(outcome = 'skipped'), 0) "
            "FROM undo_items WHERE op_id = ?", (op_id,)).fetchone()
        return dict(zip(("restored_nodes", "restored_edges", "merged", "skipped"), row))
