"""Store: one connection plus the repositories (.nodes, .edges, .log, .undo_log). Also the bulk seed insert used by load."""

from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Iterable
from pathlib import Path

from graph_undo.db.codec import encode_tags
from graph_undo.db.connection import connect
from graph_undo.db.edges import EdgeRepo
from graph_undo.db.nodes import NodeRepo
from graph_undo.db.oplog import OpLogRepo
from graph_undo.db.undolog import UndoLogRepo
from graph_undo.domain.model import Node

NAMESPACE = uuid.UUID("6f1c3a52-0d8e-4c1e-9b7a-2f4d5e6a7b8c")


class Store:
    def __init__(self, con: sqlite3.Connection):
        self.con = con
        self.nodes = NodeRepo(con)
        self.edges = EdgeRepo(con)
        self.log = OpLogRepo(con)
        self.undo_log = UndoLogRepo(con)

    @classmethod
    def open(cls, path: str | Path) -> "Store":
        return cls(connect(path))

    def close(self) -> None:
        self.con.close()

    @staticmethod
    def mint_node_id(op_id: str) -> str:
        """Deterministic id for the node created by op_id: uuid5(NAMESPACE, op_id)."""
        return str(uuid.uuid5(NAMESPACE, op_id))

    def bulk_load(self, nodes: Iterable[Node], edges: Iterable[tuple[str, str, str]]) -> None:
        """Insert seed rows in the given order (edge ids follow `edges` order). Caller owns the transaction."""
        self.con.executemany(
            "INSERT INTO nodes (id, qualified_name, type, description, owner, tags, deleted_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ((n.id, n.qualified_name, n.type, n.description, n.owner, encode_tags(n.tags), n.deleted_by)
             for n in nodes))
        self.con.executemany("INSERT INTO edges (from_id, to_id, type) VALUES (?, ?, ?)", edges)
