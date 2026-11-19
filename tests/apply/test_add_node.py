"""add_node handler (spec: docs/superpowers/specs/2026-11-19-op-add-node-design.md)."""

from __future__ import annotations

import json
import uuid

import pytest

from apply.conftest import load_store, log_row, run, snapshot
from graph_undo.db.connection import transaction
from graph_undo.domain.errors import NameTaken, NodeNotFound, OpError, TypeOrderViolation
from graph_undo.domain.model import Node

NS = uuid.UUID("6f1c3a52-0d8e-4c1e-9b7a-2f4d5e6a7b8c")   # literal; do not import from src


def human(op_id, qn, type, parent, description="d", owner="alice", tags=()):
    return {"op_id": op_id, "actor": {"kind": "human", "name": "alice"}, "op": "add_node",
            "qualified_name": qn, "type": type, "parent": parent,
            "description": description, "owner": owner, "tags": list(tags)}


def ingest(op_id, qn, type, parent):
    return {"op_id": op_id, "actor": {"kind": "ingest", "name": "crawler"}, "op": "add_node",
            "qualified_name": qn, "type": type, "parent": parent}


ACTORS = {"human": human, "ingest": ingest}


def graph_snapshot(store) -> dict:
    snap = snapshot(store)
    snap.pop("ops")
    return snap


def kill(store, qn, op_id="op-0000"):
    """Mark the live subtree of qn and every live edge touching it dead, via repos only."""
    root = store.nodes.live_by_name(qn)
    with transaction(store.con):
        ids = store.nodes.live_subtree_ids(root.id)
        edges = store.edges.live_touching(ids)
        store.nodes.mark_deleted(ids, op_id)
        store.edges.mark_deleted([e.id for e in edges], op_id)


def node_row(store, qn) -> Node:
    return store.nodes.live_by_name(qn)


def eff_tags(store, node_id):
    row = store.con.execute("SELECT tags FROM effective_tags WHERE node_id = ?", (node_id,)).fetchone()
    return json.loads(row[0])


def mint(op_id):
    return str(uuid.uuid5(NS, op_id))


# ---- Task 1: create path ----

def test_human_add_creates_node_and_edge(store):
    r = run(store, **human("op-0001", "conn/db/sc/x", "table", "conn/db/sc", description="hand", owner="bob",
                           tags=["pii", "gold", "pii"]))
    assert r.effect == "applied"
    assert r.node_id == mint("op-0001")
    assert node_row(store, "conn/db/sc/x") == Node(r.node_id, "conn/db/sc/x", "table", "hand", "bob",
                                                   ("gold", "pii"), None)
    e = store.edges.live_parent(r.node_id)
    assert (e.id, e.from_id, e.type) == (r.edge_id, "s", "contains")


def test_human_add_effective_tags_include_ancestors(store):
    with transaction(store.con):
        store.nodes.update_fields("d", {"tags": ("gold",)})
    r = run(store, **human("op-0001", "conn/db/sc/x", "table", "conn/db/sc", tags=["pii"]))
    # schema s carries "a" in BASE
    assert eff_tags(store, r.node_id) == ["a", "gold", "pii"]
    assert node_row(store, "conn/db/sc/x").tags == ("pii",)


def test_human_add_log_row(store):
    d = human("op-0001", "conn/db/sc/x", "table", "conn/db/sc", tags=["pii", "gold", "pii"])
    r = run(store, **d)
    row = log_row(store, "op-0001")
    assert (row.op, row.effect, row.actor_kind, row.actor_name) == ("add_node", "applied", "human", "alice")
    assert (row.node_id, row.edge_id, row.before) == (r.node_id, r.edge_id, None)
    assert json.loads(row.payload) == d


def test_ingest_add_creates_blank_node(store):
    r = run(store, **ingest("op-0002", "conn/db/sc/t/z", "column", "conn/db/sc/t"))
    n = node_row(store, "conn/db/sc/t/z")
    assert (n.id, n.type, n.description, n.owner, n.tags) == (r.node_id, "column", "", "", ())
    e = store.edges.live_parent(r.node_id)
    assert (e.from_id, e.type) == ("t", "contains")
    row = log_row(store, "op-0002")
    assert (row.effect, row.actor_kind, row.edge_id) == ("applied", "ingest", r.edge_id)
    assert row.edge_id is not None


def test_add_under_new_node(store):
    a = run(store, **human("op-0001", "conn/db/sc/x", "table", "conn/db/sc"))
    b = run(store, **human("op-0002", "conn/db/sc/x/c", "column", "conn/db/sc/x"))
    assert store.edges.live_parent(b.node_id).from_id == a.node_id


# ---- Task 2: ingest upsert noop ----

def _assert_noop(store, r, op_id, node_id):
    assert r.effect == "noop"
    assert r.node_id == node_id
    assert r.edge_id is None
    row = log_row(store, op_id)
    assert (row.effect, row.node_id, row.edge_id, row.before) == ("noop", node_id, None, None)


def test_ingest_noop_on_live_name(store):
    before = graph_snapshot(store)
    r = run(store, **ingest("op-0003", "conn/db/sc/t", "table", "conn/db/sc"))
    _assert_noop(store, r, "op-0003", "t")
    assert graph_snapshot(store) == before


def test_ingest_noop_even_when_type_and_parent_differ(store):
    before = graph_snapshot(store)
    r = run(store, **ingest("op-0003", "conn/db/sc/t", "column", "conn/db/sc/u"))
    _assert_noop(store, r, "op-0003", "t")
    assert graph_snapshot(store) == before
    assert node_row(store, "conn/db/sc/t").type == "table"
    assert store.edges.live_parent("t").from_id == "s"
    assert store.nodes.live_subtree_ids("u") == ["u"]


def test_ingest_noop_even_when_parent_missing(store):
    r = run(store, **ingest("op-0003", "conn/db/sc/t", "table", "nope/nowhere"))
    _assert_noop(store, r, "op-0003", "t")


def test_ingest_noop_keeps_human_fields(store):
    a = run(store, **human("op-0001", "conn/db/sc/x", "table", "conn/db/sc", owner="bob", tags=["certified"]))
    r = run(store, **ingest("op-0002", "conn/db/sc/x", "table", "conn/db/sc"))
    _assert_noop(store, r, "op-0002", a.node_id)
    n = node_row(store, "conn/db/sc/x")
    assert (n.id, n.owner, n.tags) == (a.node_id, "bob", ("certified",))


# ---- Task 3: rejections and validation order ----

def test_human_on_live_name_name_taken(store):
    with pytest.raises(NameTaken) as ei:
        run(store, **human("op-0001", "conn/db/sc/t", "table", "conn/db/sc"))
    assert str(ei.value) == "op-0001 name_taken: conn/db/sc/t is already live as node t"


@pytest.mark.parametrize("actor", ["human", "ingest"])
def test_parent_never_existed_node_not_found(store, actor):
    with pytest.raises(NodeNotFound) as ei:
        run(store, **ACTORS[actor]("op-0001", "conn/db/zz/x", "table", "conn/db/zz"))
    assert str(ei.value) == "op-0001 node_not_found: parent 'conn/db/zz' is not a live node"


@pytest.mark.parametrize("actor", ["human", "ingest"])
def test_parent_dead_node_not_found(store, actor):
    kill(store, "conn/db/sc/u")
    with pytest.raises(NodeNotFound) as ei:
        run(store, **ACTORS[actor]("op-0001", "conn/db/sc/u/c", "column", "conn/db/sc/u"))
    assert str(ei.value) == "op-0001 node_not_found: parent 'conn/db/sc/u' is not a live node"


def test_type_order_same_level(store):
    with pytest.raises(TypeOrderViolation) as ei:
        run(store, **human("op-0001", "conn/db/sc/t2", "table", "conn/db/sc/t"))
    assert str(ei.value) == ("op-0001 type_order: table conn/db/sc/t2 cannot go under table conn/db/sc/t: "
                             "contains must go exactly one level down")


def test_type_order_skip_level(store):
    with pytest.raises(TypeOrderViolation):
        run(store, **human("op-0001", "conn/db/sc/c", "column", "conn/db/sc"))


def test_type_order_column_as_parent(store):
    with pytest.raises(TypeOrderViolation):
        run(store, **human("op-0001", "conn/db/sc/t/k/c", "column", "conn/db/sc/t/k"))


def test_type_order_upward(store):
    with pytest.raises(TypeOrderViolation):
        run(store, **human("op-0001", "conn/db/sc/t/s2", "schema", "conn/db/sc/t"))


@pytest.mark.parametrize("actor", ["human", "ingest"])
def test_connection_cannot_be_added(store, actor):
    with pytest.raises(TypeOrderViolation) as ei:
        run(store, **ACTORS[actor]("op-0001", "conn2", "connection", "conn"))
    assert str(ei.value) == ("op-0001 type_order: connection conn2 cannot go under connection conn: "
                             "contains must go exactly one level down")


def test_ingest_type_order_enforced_when_name_free(store):
    with pytest.raises(TypeOrderViolation):
        run(store, **ingest("op-0001", "conn/db/sc/t2", "table", "conn/db/sc/t"))


def test_human_name_taken_wins_over_bad_parent(store):
    with pytest.raises(NameTaken):
        run(store, **human("op-0001", "conn/db/sc/t", "table", "nope"))


def test_parent_check_wins_over_type_order(store):
    with pytest.raises(NodeNotFound):
        run(store, **human("op-0001", "nope/db2", "database", "nope"))


REJECTED = [
    human("op-0001", "conn/db/sc/t", "table", "nope"),               # name_taken
    human("op-0001", "nope/db2", "database", "nope"),                # node_not_found
    human("op-0001", "conn/db/sc/t2", "table", "conn/db/sc/t"),      # type_order
]


@pytest.mark.parametrize("d", REJECTED, ids=["name_taken", "node_not_found", "type_order"])
def test_rejected_op_writes_nothing(store, d):
    before = snapshot(store)
    with pytest.raises(OpError):
        run(store, **d)
    assert snapshot(store) == before
    assert log_row(store, d["op_id"]) is None
    assert store.nodes.by_id(mint(d["op_id"])) is None


def test_rejected_op_id_can_be_reused(store):
    with pytest.raises(NameTaken):
        run(store, **human("op-0001", "conn/db/sc/t", "table", "conn/db/sc"))
    r = run(store, **human("op-0001", "conn/db/sc/x", "table", "conn/db/sc"))
    assert r.effect == "applied"


# ---- Task 4: dead rows, recreated parents, determinism ----

@pytest.mark.parametrize("actor", ["human", "ingest"])
def test_recreate_name_whose_only_row_is_dead(store, actor):
    kill(store, "conn/db/sc/t")
    dead = store.nodes.by_id("t")
    assert dead.deleted_by == "op-0000"
    dead_edge = store.con.execute("SELECT id, deleted_by FROM edges WHERE from_id='s' AND to_id='t'").fetchone()
    r = run(store, **ACTORS[actor]("op-0005", "conn/db/sc/t", "table", "conn/db/sc"))
    assert r.effect == "applied"
    assert r.node_id != "t"
    assert store.nodes.by_id("t") == dead
    assert store.con.execute("SELECT id, deleted_by FROM edges WHERE id = ?", (dead_edge[0],)).fetchone() \
        == (dead_edge[0], "op-0000")
    new_edge = store.edges.live_parent(r.node_id)
    assert new_edge.from_id == "s" and new_edge.id != dead_edge[0]
    n = node_row(store, "conn/db/sc/t")
    assert n.id == r.node_id
    if actor == "human":
        assert (n.description, n.owner) == ("d", "alice")
    else:
        assert (n.description, n.owner, n.tags) == ("", "", ())


def test_parent_resolved_to_recreated_node(store):
    kill(store, "conn/db/sc")
    sc = run(store, **ingest("op-0005", "conn/db/sc", "schema", "conn/db"))
    t = run(store, **ingest("op-0006", "conn/db/sc/t", "table", "conn/db/sc"))
    assert store.edges.live_parent(t.node_id).from_id == sc.node_id
    assert sc.node_id != "s"


def test_ingest_after_recreate_is_noop(store):
    kill(store, "conn/db/sc/t")
    a = run(store, **ingest("op-0005", "conn/db/sc/t", "table", "conn/db/sc"))
    b = run(store, **ingest("op-0006", "conn/db/sc/t", "table", "conn/db/sc"))
    assert b.effect == "noop" and b.node_id == a.node_id


def test_same_op_same_id_on_two_fresh_dbs(tmp_path):
    results = []
    for sub in ("a", "b"):
        (tmp_path / sub).mkdir()
        s = load_store(tmp_path / sub)
        try:
            r = run(s, **human("op-0001", "conn/db/sc/x", "table", "conn/db/sc"))
            results.append((r.node_id, r.edge_id))
        finally:
            s.close()
    assert results[0] == results[1]
    assert results[0][0] == mint("op-0001")


def test_rerun_same_op_is_skipped(store):
    d = human("op-0001", "conn/db/sc/x", "table", "conn/db/sc")
    run(store, **d)
    r = run(store, **d)
    assert r.effect == "skipped"
    assert store.con.execute("SELECT COUNT(*) FROM live_nodes WHERE qualified_name = 'conn/db/sc/x'"
                             ).fetchone()[0] == 1
    assert store.con.execute("SELECT COUNT(*) FROM ops WHERE op_id = 'op-0001'").fetchone()[0] == 1
