"""delete_node handler (spec: docs/superpowers/specs/2026-11-19-op-delete-node-design.md)."""

from __future__ import annotations

import copy
import time

import pytest

from apply.conftest import edge, live_names, load_store, log_row, node, requires, run, snapshot
from graph_undo.api.context import OpResult
from graph_undo.api.load import load_seed
from graph_undo.api.registry import apply_op
from graph_undo.db.connection import transaction
from graph_undo.db.oplog import LoggedOp
from graph_undo.db.store import Store
from graph_undo.domain.errors import NodeNotFound
from graph_undo.domain.model import Node
from graph_undo.domain.ops import canonical, parse_op
from graph_undo.fixture.ops import generate_ops
from graph_undo.fixture.seed import generate_seed, serialize
from replay import replay

# Fixture graph (edge row ids follow DROWS edge order, 1..15):
#   c conn [conn-tag] > d conn/db [db-tag] > s conn/db/sc [pii] > t (k, k2), u
#                                          > s2 conn/db/sc2 > v [gold], w
#   references t->v, v->u, t->u, w->v;  second tree c9 conn9 > d9 conn9/db, reference d9->d
DROWS = [
    node("c", "conn", tags=["conn-tag"]),
    node("d", "conn/db", "database", tags=["db-tag"]),
    node("s", "conn/db/sc", "schema", tags=["pii"]),
    node("t", "conn/db/sc/t", "table"),
    node("k", "conn/db/sc/t/k", "column"),
    node("k2", "conn/db/sc/t/k2", "column"),
    node("u", "conn/db/sc/u", "table"),
    node("s2", "conn/db/sc2", "schema"),
    node("v", "conn/db/sc2/v", "table", tags=["gold"]),
    node("w", "conn/db/sc2/w", "table"),
    node("c9", "conn9"),
    node("d9", "conn9/db", "database"),
    edge("c", "d"), edge("d", "s"), edge("d", "s2"), edge("s", "t"), edge("s", "u"), edge("t", "k"),
    edge("t", "k2"), edge("s2", "v"), edge("s2", "w"),
    edge("t", "v", "references"), edge("v", "u", "references"), edge("t", "u", "references"),
    edge("w", "v", "references"),
    edge("c9", "d9"), edge("d9", "d", "references"),
]

C, R = "contains", "references"
SUBTREE_EDGES = [("d", "s", C), ("s", "t", C), ("s", "u", C), ("t", "k", C), ("t", "k2", C),
                 ("t", "v", R), ("v", "u", R), ("t", "u", R)]


@pytest.fixture
def dstore(tmp_path):
    s = load_store(tmp_path, DROWS)
    yield s
    s.close()


# ---- helpers ----

def ids(store: Store) -> dict[str, str]:
    """{qualified_name: id} of the live nodes (fixture ids equal the short names)."""
    return {qn: i for i, qn in store.con.execute("SELECT id, qualified_name FROM live_nodes")}


def eid(store: Store, f: str, t: str, type_: str = C) -> int:
    e = store.edges.live(f, t, type_)
    assert e is not None, (f, t, type_)
    return e.id


def eids(store: Store, triples) -> set[int]:
    return {eid(store, *x) for x in triples}


def flagged(store: Store, op_id: str) -> tuple[set[str], set[int]]:
    n = {r[0] for r in store.con.execute("SELECT id FROM nodes WHERE deleted_by = ?", (op_id,))}
    e = {r[0] for r in store.con.execute("SELECT id FROM edges WHERE deleted_by = ?", (op_id,))}
    return n, e


def removals(store: Store, op_id: str) -> tuple[set[str], set[int]]:
    n, e = store.log.removals(op_id)
    return set(n), set(e)


def deleted_by_edge(store: Store, edge_id: int):
    return store.con.execute("SELECT deleted_by FROM edges WHERE id = ?", (edge_id,)).fetchone()[0]


def earlier_op(store: Store, op_id: str, *, kind: str = "delete_edge", dead_nodes=(), dead_edges=(),
               new_nodes=(), new_contains=(), new_refs=()) -> list[int]:
    """Simulate an already-applied op through the repositories only. Returns the new edge ids."""
    with transaction(store.con):
        assert store.edges.mark_deleted(list(dead_edges), op_id) == len(dead_edges)
        assert store.nodes.mark_deleted(list(dead_nodes), op_id) == len(dead_nodes)
        for n in new_nodes:
            store.nodes.insert(n)
        new = [store.edges.insert(f, t, C).id for f, t in new_contains]
        new += [store.edges.insert(f, t, R).id for f, t in new_refs]
        store.log.record(LoggedOp(op_id, "human", "ann", kind, "applied", None, None, None,
                                  canonical({"op_id": op_id})))
        if dead_nodes or dead_edges:
            store.log.add_removals(op_id, list(dead_nodes), list(dead_edges))
    return new


def delete(store: Store, qn: str, op_id: str = "op-0100") -> OpResult:
    return run(store, op="delete_node", node=qn, op_id=op_id)


def assert_no_dangling(store: Store) -> None:
    bad = store.con.execute(
        "SELECT e.id FROM live_edges e WHERE e.from_id NOT IN (SELECT id FROM live_nodes) "
        "OR e.to_id NOT IN (SELECT id FROM live_nodes)").fetchall()
    assert bad == []


def assert_exact(store: Store, op_id: str) -> None:
    """removals(op_id) == rows with deleted_by = op_id, both directions."""
    rn, re_ = removals(store, op_id)
    fn, fe = flagged(store, op_id)
    assert rn == fn and re_ == fe
    assert len(store.log.removals(op_id)[0]) == len(rn)   # no duplicates
    for n in rn:
        assert store.nodes.by_id(n).deleted_by == op_id
    for e in re_:
        assert deleted_by_edge(store, e) == op_id


# ---- cascade shape ----

def test_leaf_delete(dstore):
    before = live_names(dstore)
    tk = eid(dstore, "t", "k")
    r = delete(dstore, "conn/db/sc/t/k")
    assert flagged(dstore, "op-0100") == ({"k"}, {tk})
    assert live_names(dstore) == before - {"conn/db/sc/t/k"}
    assert (r.removed_nodes, r.removed_edges) == (1, 1)


def test_leaf_with_reference(dstore):
    kw = dstore.edges.insert("k", "w", R).id
    dstore.con.commit()
    tk = eid(dstore, "t", "k")
    delete(dstore, "conn/db/sc/t/k")
    assert flagged(dstore, "op-0100") == ({"k"}, {tk, kw})


def test_subtree_delete(dstore):
    gone = eids(dstore, SUBTREE_EDGES)
    kept = eids(dstore, [("c", "d", C), ("d", "s2", C), ("s2", "v", C), ("s2", "w", C), ("w", "v", R),
                         ("c9", "d9", C), ("d9", "d", R)])
    delete(dstore, "conn/db/sc")
    assert flagged(dstore, "op-0100") == ({"s", "t", "u", "k", "k2"}, gone)
    assert set(ids(dstore)) == {"conn", "conn/db", "conn/db/sc2", "conn/db/sc2/v", "conn/db/sc2/w",
                                "conn9", "conn9/db"}
    live_e = {r[0] for r in dstore.con.execute("SELECT id FROM live_edges")}
    assert live_e == kept


def test_parent_edge_removed(dstore):
    ds = eid(dstore, "d", "s")
    delete(dstore, "conn/db/sc")
    assert dstore.edges.live_parent("s") is None
    assert deleted_by_edge(dstore, ds) == "op-0100"


def test_inbound_and_outbound_references_removed(dstore):
    tv, vu = eid(dstore, "t", "v", R), eid(dstore, "v", "u", R)
    delete(dstore, "conn/db/sc")
    assert deleted_by_edge(dstore, tv) == deleted_by_edge(dstore, vu) == "op-0100"
    assert dstore.nodes.by_id("v").deleted_by is None


def test_delete_root_connection(dstore):
    c9d9 = eid(dstore, "c9", "d9")
    d9d = eid(dstore, "d9", "d", R)
    r = delete(dstore, "conn")
    n, e = flagged(dstore, "op-0100")
    assert n == {"c", "d", "s", "t", "k", "k2", "u", "s2", "v", "w"}
    assert d9d in e and c9d9 not in e
    assert live_names(dstore) == {"conn9", "conn9/db"}
    assert {r_[0] for r_ in dstore.con.execute("SELECT id FROM live_edges")} == {c9d9}
    assert (r.removed_nodes, r.removed_edges) == (10, 14)
    assert_no_dangling(dstore)


def test_cross_tree_reference_into_subtree(dstore):
    d9d = eid(dstore, "d9", "d", R)
    delete(dstore, "conn/db")
    assert deleted_by_edge(dstore, d9d) == "op-0100"
    assert {"conn9", "conn9/db"} <= live_names(dstore)


# ---- bookkeeping exactness ----

def test_removals_equal_flagged_both_directions(dstore):
    delete(dstore, "conn/db/sc")
    assert_exact(dstore, "op-0100")
    # direction 1: every removals row names a row flagged by this op
    bad_n = dstore.con.execute(
        "SELECT r.node_id FROM removed_nodes r JOIN nodes n ON n.id = r.node_id "
        "WHERE r.op_id = 'op-0100' AND n.deleted_by IS NOT 'op-0100'").fetchall()
    bad_e = dstore.con.execute(
        "SELECT r.edge_id FROM removed_edges r JOIN edges e ON e.id = r.edge_id "
        "WHERE r.op_id = 'op-0100' AND e.deleted_by IS NOT 'op-0100'").fetchall()
    # direction 2: every row flagged by this op has a removals row
    miss_n = dstore.con.execute(
        "SELECT id FROM nodes WHERE deleted_by = 'op-0100' AND id NOT IN "
        "(SELECT node_id FROM removed_nodes WHERE op_id = 'op-0100')").fetchall()
    miss_e = dstore.con.execute(
        "SELECT id FROM edges WHERE deleted_by = 'op-0100' AND id NOT IN "
        "(SELECT edge_id FROM removed_edges WHERE op_id = 'op-0100')").fetchall()
    assert bad_n == bad_e == miss_n == miss_e == []


def test_deleted_by_values(dstore):
    gone = eids(dstore, SUBTREE_EDGES)
    delete(dstore, "conn/db/sc")
    for i, db in dstore.con.execute("SELECT id, deleted_by FROM nodes"):
        assert db == ("op-0100" if i in {"s", "t", "u", "k", "k2"} else None), i
    for i, db in dstore.con.execute("SELECT id, deleted_by FROM edges"):
        assert db == ("op-0100" if i in gone else None), i


def test_counts_match_removals(dstore):
    r = delete(dstore, "conn/db/sc")
    n, e = dstore.log.removals("op-0100")
    assert r.removed_nodes == len(n) == 5
    assert r.removed_edges == len(e) == 8


def test_op_log_row(dstore):
    earlier_op(dstore, "op-0050", kind="add_edge")
    prev = dstore.con.execute("SELECT max(seq) FROM ops").fetchone()[0]
    delete(dstore, "conn/db/sc")
    row = log_row(dstore, "op-0100")
    assert row == LoggedOp("op-0100", "human", "ann", "delete_node", "applied", "s", None, None,
                           canonical({"op_id": "op-0100", "actor": {"kind": "human", "name": "ann"},
                                      "op": "delete_node", "node": "conn/db/sc"}))
    assert row.payload == ('{"actor":{"kind":"human","name":"ann"},"node":"conn/db/sc",'
                           '"op":"delete_node","op_id":"op-0100"}')
    seq = dstore.con.execute("SELECT seq FROM ops WHERE op_id = 'op-0100'").fetchone()[0]
    assert seq == prev + 1


def test_op_result(dstore):
    assert delete(dstore, "conn/db/sc") == OpResult("op-0100", "applied", node_id="s", edge_id=None,
                                                    removed_nodes=5, removed_edges=8)


# ---- previously dead rows ----

def test_dead_reference_keeps_old_deleted_by(dstore):
    tv = eid(dstore, "t", "v", R)
    others = eids(dstore, SUBTREE_EDGES) - {tv}
    earlier_op(dstore, "op-0050", dead_edges=[tv])
    delete(dstore, "conn/db/sc")
    assert deleted_by_edge(dstore, tv) == "op-0050"
    assert tv not in removals(dstore, "op-0100")[1]
    assert removals(dstore, "op-0050") == (set(), {tv})
    assert flagged(dstore, "op-0100")[1] == others
    assert_exact(dstore, "op-0100")
    assert_no_dangling(dstore)


def test_moved_out_child_survives(dstore):
    su, tu, vu = eid(dstore, "s", "u"), eid(dstore, "t", "u", R), eid(dstore, "v", "u", R)
    (s2u,) = earlier_op(dstore, "op-0050", dead_edges=[su], new_contains=[("s2", "u")])
    delete(dstore, "conn/db/sc")
    assert dstore.nodes.by_id("u").deleted_by is None
    assert dstore.edges.live_parent("u").id == s2u
    assert deleted_by_edge(dstore, su) == "op-0050"
    assert su not in removals(dstore, "op-0100")[1]
    assert deleted_by_edge(dstore, tu) == "op-0100"
    assert deleted_by_edge(dstore, vu) is None
    assert flagged(dstore, "op-0100")[0] == {"s", "t", "k", "k2"}
    assert_exact(dstore, "op-0100")
    assert_no_dangling(dstore)


def test_moved_in_child_removed(dstore):
    s2v, wv = eid(dstore, "s2", "v"), eid(dstore, "w", "v", R)
    (sv,) = earlier_op(dstore, "op-0050", dead_edges=[s2v], new_contains=[("s", "v")])
    delete(dstore, "conn/db/sc")
    assert dstore.nodes.by_id("v").deleted_by == "op-0100"
    rn, re_ = removals(dstore, "op-0100")
    assert "v" in rn and sv in re_ and wv in re_
    assert deleted_by_edge(dstore, sv) == "op-0100"
    assert deleted_by_edge(dstore, s2v) == "op-0050"
    assert deleted_by_edge(dstore, wv) == "op-0100"
    assert dstore.con.execute("SELECT 1 FROM effective_tags WHERE node_id = 'v'").fetchone() is None
    assert not any("gold" in r[0] for r in dstore.con.execute("SELECT tags FROM effective_tags"))
    assert_exact(dstore, "op-0100")
    assert_no_dangling(dstore)


@pytest.mark.parametrize("qn", ["conn/db/sc/t/k", "conn/db/sc/t", "conn/db/sc", "conn/db", "conn",
                                "conn9", "conn/db/sc2/v"])
def test_no_live_edge_to_dead_node(dstore, qn):
    delete(dstore, qn)
    assert_no_dangling(dstore)
    assert_exact(dstore, "op-0100")


# ---- derived state and names ----

def test_survivor_effective_tags_unchanged(dstore):
    q = "SELECT node_id, tags FROM effective_tags ORDER BY node_id"
    before = dict(dstore.con.execute(q).fetchall())
    delete(dstore, "conn/db/sc")
    after = dict(dstore.con.execute(q).fetchall())
    removed = {"s", "t", "u", "k", "k2"}
    assert after == {i: t for i, t in before.items() if i not in removed}


def test_name_freed_repo_level(dstore):
    delete(dstore, "conn/db/sc/t")
    assert dstore.nodes.live_by_name("conn/db/sc/t") is None
    dstore.nodes.insert(Node("t-new", "conn/db/sc/t", "table", "d", "o", ()))
    dstore.con.commit()
    assert dstore.nodes.live_by_name("conn/db/sc/t").id == "t-new"
    assert dstore.nodes.by_id("t").deleted_by == "op-0100"


def test_name_freed_add_node(dstore):
    requires("add_node")
    delete(dstore, "conn/db/sc/t")
    run(dstore, op_id="op-0101", op="add_node", qualified_name="conn/db/sc/t", type="table",
        parent="conn/db/sc", description="d", owner="o", tags=[])
    new = dstore.nodes.live_by_name("conn/db/sc/t")
    assert new is not None and new.id != "t"


def test_second_delete_same_name(dstore):
    delete(dstore, "conn/db/sc")
    with pytest.raises(NodeNotFound) as ei:
        delete(dstore, "conn/db/sc", op_id="op-0101")
    assert str(ei.value) == "op-0101 node_not_found: node 'conn/db/sc' is not a live node"
    assert log_row(dstore, "op-0101") is None


def test_delete_descendant_of_deleted(dstore):
    delete(dstore, "conn/db/sc")
    with pytest.raises(NodeNotFound) as ei:
        delete(dstore, "conn/db/sc/t", op_id="op-0101")
    assert str(ei.value) == "op-0101 node_not_found: node 'conn/db/sc/t' is not a live node"


def test_delete_recreated_node_only_new_rows(dstore):
    delete(dstore, "conn/db/sc/t")
    old_n, old_e = flagged(dstore, "op-0100")
    old_removals = removals(dstore, "op-0100")
    new_edges = earlier_op(
        dstore, "op-0101", kind="add_node",
        new_nodes=[Node("t2", "conn/db/sc/t", "table", "d", "o", ()),
                   Node("k3", "conn/db/sc/t/k", "column", "d", "o", ())],
        new_contains=[("s", "t2"), ("t2", "k3")], new_refs=[("t2", "v")])
    r = delete(dstore, "conn/db/sc/t", op_id="op-0102")
    assert r.node_id == "t2"
    assert flagged(dstore, "op-0102") == ({"t2", "k3"}, set(new_edges))
    assert flagged(dstore, "op-0100") == (old_n, old_e)
    assert {"t", "k", "k2"} == old_n
    assert removals(dstore, "op-0100") == old_removals
    rn, re_ = removals(dstore, "op-0102")
    assert not (rn & old_n) and not (re_ & old_e)
    assert_exact(dstore, "op-0102")


# ---- rejections and atomicity ----

def test_unknown_name_writes_nothing(dstore):
    before = snapshot(dstore)
    with pytest.raises(NodeNotFound) as ei:
        delete(dstore, "conn/db/nope")
    assert str(ei.value) == "op-0100 node_not_found: node 'conn/db/nope' is not a live node"
    assert snapshot(dstore) == before


def test_failure_mid_handler_rolls_back(dstore, monkeypatch):
    before = snapshot(dstore)

    def boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(dstore.log, "add_removals", boom)
    with pytest.raises(RuntimeError):
        delete(dstore, "conn/db/sc")
    assert snapshot(dstore) == before
    assert dstore.nodes.live_by_name("conn/db/sc") is not None


def test_rerun_same_op_skipped(dstore):
    delete(dstore, "conn/db/sc")
    before = snapshot(dstore)
    assert delete(dstore, "conn/db/sc").effect == "skipped"
    assert snapshot(dstore) == before


# ---- performance shape ----

def _count_statements(store: Store, fn) -> int:
    count = [0]
    store.con.set_trace_callback(lambda _sql: count.__setitem__(0, count[0] + 1))
    try:
        fn()
    finally:
        store.con.set_trace_callback(None)
    return count[0]


def test_statement_count_constant(dstore):
    small = _count_statements(dstore, lambda: delete(dstore, "conn/db/sc/t/k"))
    big = _count_statements(dstore, lambda: delete(dstore, "conn", op_id="op-0101"))
    assert flagged(dstore, "op-0101")[0] == {"c", "d", "s", "t", "k2", "u", "s2", "v", "w"}
    assert small == big


# ---- seed-42 integration ----

@pytest.fixture(scope="module")
def seed42():
    nodes, edges = generate_seed(42)
    ops, manifest = generate_ops(42, copy.deepcopy(nodes), copy.deepcopy(edges))
    return nodes, edges, ops, manifest


def _seed_store(tmp_path, nodes, edges) -> Store:
    seed = tmp_path / "seed.jsonl"
    seed.write_text(serialize(nodes, edges))
    db = tmp_path / "g.db"
    load_seed(seed, db)
    return Store.open(db)


def test_seed42_op0001(tmp_path, seed42):
    nodes, edges, ops, _ = seed42
    store = _seed_store(tmp_path, nodes, edges)
    try:
        op = parse_op(ops[0])
        t0 = time.perf_counter()
        r = apply_op(store, op)
        elapsed = time.perf_counter() - t0
        assert (r.removed_nodes, r.removed_edges) == (614, 774)
        assert elapsed < 1.0
        before, after = replay(nodes, edges, []), replay(nodes, edges, [ops[0]])
        name = dict(store.con.execute("SELECT id, qualified_name FROM nodes"))
        removed = {name[i] for i, in store.con.execute("SELECT id FROM nodes WHERE deleted_by = 'op-0001'")}
        assert removed == set(before.nodes) - set(after.nodes)
        refs = {(name[f], name[t]) for f, t in store.con.execute(
            "SELECT from_id, to_id FROM edges WHERE deleted_by = 'op-0001' AND type = 'references'")}
        assert refs == before.refs - after.refs
        assert len(refs) == 160
        assert_exact(store, "op-0001")
        assert_no_dangling(store)
    finally:
        store.close()


def test_seed42_e5_e6_keep_earlier_flags(tmp_path, seed42):
    requires("add_node", "edit_node", "delete_node", "add_edge", "delete_edge")
    nodes, edges, ops, manifest = seed42
    e5, e6 = manifest["E5"][0], manifest["E6"][0]
    store = _seed_store(tmp_path, nodes, edges)
    seed_ids = {n["id"] for n in nodes}
    try:
        q01 = "SELECT id FROM edges WHERE deleted_by = 'op-0001'"
        for raw in ops:
            if raw["op_id"] == e5:
                e01_before = {r[0] for r in store.con.execute(q01)}
            apply_op(store, parse_op(raw))
            if raw["op_id"] == e5:
                assert {r[0] for r in store.con.execute(q01)} == e01_before
                r5n, r5e = removals(store, e5)
                r1n, r1e = removals(store, "op-0001")
                assert not (r5n & r1n) and not (r5e & r1e)
        r6n, _ = removals(store, e6)
        assert r6n and not (r6n & seed_ids)
        r1n, r1e = removals(store, "op-0001")
        assert (len(r1n), len(r1e)) == (614, 774)
        assert_exact(store, e5)
        assert_exact(store, e6)
    finally:
        store.close()
