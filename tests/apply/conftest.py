"""Shared fixtures and helpers for apply tests (spec: apply-foundation, "Testing approach").

Import the helpers in a test module with:  from apply.conftest import BASE, run, live_names, ...

BASE tree (ids in brackets; edges get row ids 1..6 in this order):
    conn [c] -> conn/db [d] -> conn/db/sc [s, tags ["a"]] -> conn/db/sc/t [t] -> conn/db/sc/t/k [k] (column)
                                                          -> conn/db/sc/u [u]
    contains c>d=1  d>s=2  s>t=3  s>u=4  t>k=5;   references t->u=6
Every node has description "d", owner "o" and tags [] unless stated.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

from graph_undo.api.load import load_seed
from graph_undo.db.store import Store


def node(i, qn, type_="connection", tags=None, description="d", owner="o"):
    return {"id": i, "qualified_name": qn, "type": type_, "description": description, "owner": owner,
            "tags": list(tags) if tags is not None else []}


def edge(f, t, type_="contains"):
    return {"from_id": f, "to_id": t, "type": type_}


BASE = [
    node("c", "conn"), node("d", "conn/db", "database"), node("s", "conn/db/sc", "schema", tags=["a"]),
    node("t", "conn/db/sc/t", "table"), node("u", "conn/db/sc/u", "table"),
    node("k", "conn/db/sc/t/k", "column"),
    edge("c", "d"), edge("d", "s"), edge("s", "t"), edge("s", "u"), edge("t", "k"),
    edge("t", "u", "references"),
]


def load_store(directory: Path, rows=BASE, name="g.db") -> Store:
    """Write `rows` as a seed file in `directory`, load it with the real `load_seed`, return an open Store."""
    seed = Path(directory) / f"{name}.seed.jsonl"
    seed.write_text("".join(json.dumps(r) + "\n" for r in rows))
    db = Path(directory) / name
    load_seed(seed, db)
    return Store.open(db)


@pytest.fixture
def store(tmp_path):
    s = load_store(tmp_path)
    yield s
    s.close()
