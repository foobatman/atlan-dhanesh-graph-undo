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


# ---- helpers (plain functions; import them from apply.conftest) ----

DEFAULT_OP_ID = "op-0100"
DEFAULT_ACTOR = {"kind": "human", "name": "ann"}
OP_KINDS = ("add_node", "edit_node", "delete_node", "add_edge", "delete_edge")


def op_dict(**fields) -> dict:
    """An op dict with a default op_id ("op-0100") and actor (human "ann"); `fields` override them.
    Use **{"from": ...} for the edge endpoints."""
    return {"op_id": DEFAULT_OP_ID, "actor": dict(DEFAULT_ACTOR), **fields}


def run(store: Store, **fields):
    """parse_op + apply_op for one op built by op_dict(**fields). Returns the OpResult or raises the OpError."""
    from graph_undo.api.registry import apply_op
    from graph_undo.domain.ops import parse_op

    return apply_op(store, parse_op(op_dict(**fields)))


def live_names(store: Store) -> set[str]:
    return {r[0] for r in store.con.execute("SELECT qualified_name FROM live_nodes")}


def log_row(store: Store, op_id: str):
    """The LoggedOp for op_id, or None."""
    return store.log.get(op_id)


def snapshot(store: Store) -> dict:
    """Every row of every table (plus user_version), for "nothing changed" assertions."""
    con = store.con
    tables = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    out = {t: con.execute(f"SELECT * FROM {t} ORDER BY rowid").fetchall() for t in tables}
    out["user_version"] = con.execute("PRAGMA user_version").fetchone()[0]
    return out


def is_stub(kind: str) -> bool:
    """True while graph_undo.api.<kind> is still the foundation stub (STUB = True) or is not registered."""
    from graph_undo.api.registry import HANDLERS

    mod = importlib.import_module(f"graph_undo.api.{kind}")
    return getattr(mod, "STUB", False) or kind not in HANDLERS


def requires(*kinds: str) -> None:
    """Call at the top of a test that needs other op handlers: skips it while any of them is a stub."""
    missing = [k for k in kinds if is_stub(k)]
    if missing:
        pytest.skip(f"handler not implemented yet: {', '.join(missing)}")
