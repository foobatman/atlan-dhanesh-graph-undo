"""add_edge handler (spec: docs/superpowers/specs/2026-11-19-op-add-edge-design.md)."""

from __future__ import annotations

import json

import pytest

from apply.conftest import BASE, edge, load_store, log_row, node, run, snapshot
from graph_undo.api.context import OpResult
from graph_undo.db.connection import transaction
from graph_undo.db.store import Store
from graph_undo.domain.errors import (
    AlreadyHasParent,
    DuplicateEdge,
    NodeNotFound,
    SelfLoop,
    TypeOrderViolation,
)
from graph_undo.domain.model import Node
from graph_undo.domain.ops import canonical, parse_op

WIDE = BASE + [
    node("s2", "conn/db/sc2", "schema", tags=["b"]), node("v", "conn/db/sc2/v", "table"),
    edge("d", "s2"), edge("s2", "v"),
]


@pytest.fixture
def wide_store(tmp_path):
    s = load_store(tmp_path, WIDE, name="wide.db")
    yield s
    s.close()


# ---- local helpers (repositories only, except read-only SQL) ----

def nid(store: Store, qn: str) -> str:
    return store.nodes.live_by_name(qn).id


def kill_edge(store, f, t, type_="contains", op_id="op-9000"):
    with transaction(store.con):
        e = store.edges.live(nid(store, f), nid(store, t), type_)
        assert store.edges.mark_deleted([e.id], op_id) == 1


def kill_subtree(store, qn, op_id="op-9001"):
    with transaction(store.con):
        ids = store.nodes.live_subtree_ids(nid(store, qn))
        touching = [e.id for e in store.edges.live_touching(ids)]
        store.edges.mark_deleted(touching, op_id)
        store.nodes.mark_deleted(ids, op_id)


def recreate(store, qn, type_, parent, op_id) -> str:
    new_id = Store.mint_node_id(op_id)
    with transaction(store.con):
        store.nodes.insert(Node(new_id, qn, type_, "", "", (), None))
        store.edges.insert(nid(store, parent), new_id, "contains")
    return new_id


def edge_row(store, i):
    return store.con.execute("SELECT * FROM edges WHERE id = ?", (i,)).fetchone()


def etags(store, qn):
    row = store.con.execute("SELECT tags FROM effective_tags WHERE node_id = ?", (nid(store, qn),)).fetchone()
    return json.loads(row[0])


def actor(kind):
    return {"kind": kind, "name": "ann" if kind == "human" else "ingest-crawler"}


def add_fields(f, t, type_="references", kind="human", op_id="op-0100"):
    return {"op_id": op_id, "op": "add_edge", "actor": actor(kind), "from": f, "to": t, "type": type_}


def add(store, f, t, type_="references", kind="human", op_id="op-0100"):
    return run(store, **add_fields(f, t, type_, kind, op_id))


T, U, K, SC, DB = "conn/db/sc/t", "conn/db/sc/u", "conn/db/sc/t/k", "conn/db/sc", "conn/db"
SC2 = "conn/db/sc2"


# ---- happy paths ----

def test_add_reference_happy_path(store):
    r = add(store, U, T)
    assert r == OpResult(op_id="op-0100", effect="applied", edge_id=7)
    assert edge_row(store, 7) == (7, "u", "t", "references", None)
    lg = log_row(store, "op-0100")
    assert (lg.actor_kind, lg.actor_name, lg.op, lg.effect) == ("human", "ann", "add_edge", "applied")
    assert (lg.node_id, lg.edge_id, lg.before) == (None, 7, None)
    assert lg.payload == canonical(add_fields(U, T))
    assert store.log.removals("op-0100") == ([], [])


def test_ingest_add_reference(store):
    r = add(store, U, T, kind="ingest")
    assert r.effect == "applied" and r.edge_id == 7
    lg = log_row(store, "op-0100")
    assert (lg.actor_kind, lg.actor_name) == ("ingest", "ingest-crawler")


def test_reverse_direction_allowed(store):
    assert add(store, U, T).edge_id == 7
    assert edge_row(store, 6)[4] is None and edge_row(store, 7)[4] is None


def test_reference_between_any_types(store):
    assert add(store, K, DB).effect == "applied"


def test_reference_alongside_contains_same_pair(store):
    assert add(store, SC, T, "references").effect == "applied"
    assert edge_row(store, 3)[4] is None


def test_ingest_add_contains_onto_parentless_child(wide_store):
    kill_edge(wide_store, SC, U)
    assert add(wide_store, SC2, U, "contains", kind="ingest").effect == "applied"
    assert wide_store.edges.live_parent("u").from_id == "s2"


# ---- move ----

def test_move_contains_onto_parentless_child(wide_store):
    s = wide_store
    assert etags(s, T) == ["a"] and etags(s, K) == ["a"]
    kill_edge(s, SC, T)
    r = add(s, SC2, T, "contains")
    assert r.effect == "applied" and r.edge_id == 9
    assert s.edges.live_parent("t").from_id == "s2"
    moved = s.nodes.live_by_name(T)
    assert moved.id == "t" and moved.qualified_name == T
    assert etags(s, T) == ["b"] and etags(s, K) == ["b"]
    assert etags(s, U) == ["a"]
    assert edge_row(s, 3)[4] == "op-9000"


# ---- dead edges and recreated nodes ----

def test_readd_identical_to_dead_reference_inserts_new_row(store):
    kill_edge(store, T, U, "references")
    before = edge_row(store, 6)
    assert before[4] == "op-9000"
    assert add(store, T, U).edge_id == 7
    assert edge_row(store, 6) == before
    assert edge_row(store, 7)[4] is None
    assert store.edges.live("t", "u", "references").id == 7


def test_readd_identical_to_dead_contains_inserts_new_row(store):
    kill_edge(store, SC, T)
    assert add(store, SC, T, "contains").edge_id == 7
    assert edge_row(store, 3)[4] == "op-9000"
    assert store.edges.live_parent("t").id == 7


def test_edge_to_recreated_node_uses_new_id(store):
    old = nid(store, T)
    kill_subtree(store, T)
    new = recreate(store, T, "table", SC, "op-0050")
    assert new == Store.mint_node_id("op-0050") != old
    r = add(store, U, T)
    assert r.effect == "applied"
    assert edge_row(store, r.edge_id)[2] == new
    assert store.edges.live_touching([old]) == []


def test_edge_from_recreated_node_uses_new_id(store):
    old = nid(store, T)
    kill_subtree(store, T)
    new = recreate(store, T, "table", SC, "op-0050")
    r = add(store, T, U)
    assert r.effect == "applied"
    assert edge_row(store, r.edge_id)[1] == new
    assert store.edges.live_touching([old]) == []


# ---- rejections ----

def _rejects(store, cls, code, fields, message=None):
    before = snapshot(store)
    with pytest.raises(cls) as ei:
        run(store, **fields)
    assert ei.value.code == code
    if message is not None:
        assert str(ei.value) == message
    assert snapshot(store) == before
    assert log_row(store, fields["op_id"]) is None
    return ei.value


def test_duplicate_live_reference(store):
    _rejects(store, DuplicateEdge, "duplicate_edge", add_fields(T, U),
             "op-0100 duplicate_edge: references edge 'conn/db/sc/t' -> 'conn/db/sc/u' already exists (edge 6)")


def test_ingest_duplicate_reference_is_error(store):
    _rejects(store, DuplicateEdge, "duplicate_edge", add_fields(T, U, kind="ingest"),
             "op-0100 duplicate_edge: references edge 'conn/db/sc/t' -> 'conn/db/sc/u' already exists (edge 6)")


def test_self_loop(store):
    _rejects(store, SelfLoop, "self_loop", add_fields(T, T),
             "op-0100 self_loop: references edge from 'conn/db/sc/t' to itself")
    _rejects(store, SelfLoop, "self_loop", add_fields(T, T, "contains"),
             "op-0100 self_loop: contains edge from 'conn/db/sc/t' to itself")


def test_from_never_existed(store):
    _rejects(store, NodeNotFound, "node_not_found", add_fields("conn/nope", T),
             "op-0100 node_not_found: from 'conn/nope' is not a live node")


def test_to_never_existed(store):
    _rejects(store, NodeNotFound, "node_not_found", add_fields(T, "conn/nope"),
             "op-0100 node_not_found: to 'conn/nope' is not a live node")


def test_endpoint_dead(store):
    kill_subtree(store, U)
    _rejects(store, NodeNotFound, "node_not_found", add_fields(T, U),
             "op-0100 node_not_found: to 'conn/db/sc/u' is not a live node")


def test_both_missing_reports_from_first(store):
    _rejects(store, NodeNotFound, "node_not_found", add_fields("x/1", "x/2"),
             "op-0100 node_not_found: from 'x/1' is not a live node")


def test_self_loop_on_dead_name_is_node_not_found(store):
    kill_subtree(store, U)
    _rejects(store, NodeNotFound, "node_not_found", add_fields(U, U),
             "op-0100 node_not_found: from 'conn/db/sc/u' is not a live node")


@pytest.mark.parametrize("f,t,setup,message", [
    (T, U, None, "op-0100 type_order: contains 'conn/db/sc/t' (table) -> 'conn/db/sc/u' (table): "
                 "a table can only contain a column"),
    (DB, T, (SC, T), "op-0100 type_order: contains 'conn/db' (database) -> 'conn/db/sc/t' (table): "
                     "a database can only contain a schema"),
    (T, SC, None, None),
    (K, U, None, "op-0100 type_order: contains 'conn/db/sc/t/k' (column) -> 'conn/db/sc/u' (table): "
                 "a column cannot contain anything"),
], ids=["same_level", "two_levels_down", "upward", "from_column"])
def test_contains_type_order(store, f, t, setup, message):
    if setup:
        kill_edge(store, *setup)
    _rejects(store, TypeOrderViolation, "type_order", add_fields(f, t, "contains"), message)


def test_contains_onto_child_with_live_parent(wide_store):
    _rejects(wide_store, AlreadyHasParent, "already_has_parent", add_fields(SC2, T, "contains"),
             "op-0100 already_has_parent: 'conn/db/sc/t' already has parent 'conn/db/sc' (edge 3); "
             "delete that contains edge first")


def test_identical_live_contains_is_duplicate_not_already_has_parent(store):
    _rejects(store, DuplicateEdge, "duplicate_edge", add_fields(SC, T, "contains"),
             "op-0100 duplicate_edge: contains edge 'conn/db/sc' -> 'conn/db/sc/t' already exists (edge 3)")


# (name, store fixture, setup(store) or None, op fields, error class, a valid op to run afterwards)
REJECTIONS = [
    ("duplicate", "store", None, add_fields(T, U), DuplicateEdge, add_fields(U, T)),
    ("duplicate_ingest", "store", None, add_fields(T, U, kind="ingest"), DuplicateEdge, add_fields(U, T)),
    ("self_loop", "store", None, add_fields(T, T), SelfLoop, add_fields(U, T)),
    ("self_loop_contains", "store", None, add_fields(T, T, "contains"), SelfLoop, add_fields(U, T)),
    ("from_missing", "store", None, add_fields("conn/nope", T), NodeNotFound, add_fields(U, T)),
    ("to_missing", "store", None, add_fields(T, "conn/nope"), NodeNotFound, add_fields(U, T)),
    ("both_missing", "store", None, add_fields("x/1", "x/2"), NodeNotFound, add_fields(U, T)),
    ("type_order", "store", None, add_fields(T, U, "contains"), TypeOrderViolation, add_fields(U, T)),
    ("from_column", "store", None, add_fields(K, U, "contains"), TypeOrderViolation, add_fields(U, T)),
    ("upward", "store", None, add_fields(T, SC, "contains"), TypeOrderViolation, add_fields(U, T)),
    ("dup_contains", "store", None, add_fields(SC, T, "contains"), DuplicateEdge, add_fields(U, T)),
    ("has_parent", "wide_store", None, add_fields(SC2, T, "contains"), AlreadyHasParent, add_fields(U, T)),
    ("endpoint_dead", "store", lambda s: kill_subtree(s, U), add_fields(T, U), NodeNotFound,
     add_fields(K, T)),
    ("two_down", "store", lambda s: kill_edge(s, SC, T), add_fields(DB, T, "contains"), TypeOrderViolation,
     add_fields(SC, T, "contains")),
]


@pytest.mark.parametrize("name,fixture,setup,fields,cls,valid", REJECTIONS, ids=[r[0] for r in REJECTIONS])
def test_rejected_op_writes_nothing(request, tmp_path, name, fixture, setup, fields, cls, valid):
    s = request.getfixturevalue(fixture)
    if setup:
        setup(s)
    before = snapshot(s)
    with pytest.raises(cls) as ei:
        run(s, **fields)
    assert snapshot(s) == before
    assert log_row(s, fields["op_id"]) is None

    # Same text from a fresh store that received the same setup: no dependence on history.
    fresh = load_store(tmp_path, WIDE if fixture == "wide_store" else BASE, name="fresh.db")
    try:
        if setup:
            setup(fresh)
        with pytest.raises(cls) as fresh_ei:
            from graph_undo.api.registry import apply_op
            apply_op(fresh, parse_op(fields))
        assert str(fresh_ei.value) == str(ei.value)
    finally:
        fresh.close()

    # The rejection left no log row, so the same op_id is still free.
    assert run(s, **valid).effect == "applied"


# ---- re-run ----

def test_rerun_same_op_is_skipped(store):
    assert add(store, U, T).effect == "applied"
    assert add(store, U, T).effect == "skipped"
    assert store.edges.live("u", "t", "references").id == 7
    assert store.con.execute("SELECT COUNT(*) FROM edges WHERE from_id='u' AND to_id='t'").fetchone()[0] == 1
    assert store.con.execute("SELECT COUNT(*) FROM ops").fetchone()[0] == 1
