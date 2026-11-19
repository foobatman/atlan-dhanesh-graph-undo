"""Store: one connection plus the repositories. Also the bulk seed insert used by load."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from pathlib import Path

from graph_undo.db.connection import connect
from graph_undo.domain.model import Node


def encode_tags(tags: Iterable[str]) -> str:
    """Tags as stored: compact JSON array, sorted and duplicate-free."""
    return json.dumps(sorted(set(tags)), separators=(",", ":"), ensure_ascii=False)


class Store:
    def __init__(self, con: sqlite3.Connection):
        self.con = con

    @classmethod
    def open(cls, path: str | Path) -> "Store":
        return cls(connect(path))

    def close(self) -> None:
        self.con.close()

    def bulk_load(self, nodes: Iterable[Node], edges: Iterable[tuple[str, str, str]]) -> None:
        """Insert seed rows in the given order (edge ids follow `edges` order). Caller owns the transaction."""
        self.con.executemany(
            "INSERT INTO nodes (id, qualified_name, type, description, owner, tags, deleted_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ((n.id, n.qualified_name, n.type, n.description, n.owner, encode_tags(n.tags), n.deleted_by)
             for n in nodes))
        self.con.executemany("INSERT INTO edges (from_id, to_id, type) VALUES (?, ?, ?)", edges)
