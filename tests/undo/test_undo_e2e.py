"""Seed 42 end to end: load, apply, undo op-0001; invariants 1-6 (spec: undo-design, "Testing").

Expectations are re-derived here from the database, independently of domain/undo.py.
"""

import json
import time
from collections import defaultdict

import pytest

from graph_undo.api.apply import apply_file
from graph_undo.api.load import load_seed
from graph_undo.api.undo import undo_op
from graph_undo.db.store import Store
from graph_undo.domain.errors import AlreadyUndone
from graph_undo.fixture.ops import generate_ops
from graph_undo.fixture.seed import generate_seed, serialize

BIG = "op-0001"


@pytest.fixture(scope="module")
def fx(tmp_path_factory):
    d = tmp_path_factory.mktemp("undo42")
    nodes, edges = generate_seed(42)
    ops, manifest = generate_ops(42, nodes, edges)
    (d / "seed.jsonl").write_text(serialize(nodes, edges))
    (d / "ops.jsonl").write_text(serialize(ops, []))
    return d, {n["id"]: n for n in nodes}, ops, manifest


def build(d, name) -> Store:
    db = d / name
    load_seed(d / "seed.jsonl", db)
    summary = apply_file(d / "ops.jsonl", db)
    assert summary.rejected == 0
    return Store.open(db)


def live_rows(store):
    return (set(store.con.execute("SELECT * FROM live_nodes")),
            set(store.con.execute("SELECT * FROM live_edges")))


@pytest.fixture(scope="module")
def undone(fx):
    d, seed, ops, manifest = fx
    store = build(d, "main.db")
    before = live_rows(store)
    live_before = {qn: (i, t) for i, qn, t in store.con.execute("SELECT id, qualified_name, type FROM live_nodes")}
    result = undo_op(store, BIG)
    yield store, result, before, live_before
    store.close()


def removed(store, op_id=BIG):
    node_ids, edge_ids = store.log.removals(op_id)
    return store.nodes.by_ids(node_ids), store.edges.by_ids(edge_ids)


def test_inv1_human_changes_kept(undone):
    store, _, (nodes_before, edges_before), _ = undone
    nodes_after, edges_after = live_rows(store)
    assert nodes_before <= nodes_after
    assert edges_before <= edges_after


def test_inv2_free_names_restored(undone, fx):
    store, _, _, live_before = undone
    seed = fx[1]
    n, e = removed(store)
    parent = {x.to_id: x.from_id for x in e if x.type == "contains"}
    root = store.log.get(BIG).node_id
    ok: dict[str, bool] = {}

    def parent_ok(p_id):
        if p_id in n:
            if ok[p_id]:
                return True
            p = n[p_id]
        else:
            p = store.nodes.by_id(p_id)
            if p.qualified_name in live_before and live_before[p.qualified_name][0] == p_id:
                return True
        h = live_before.get(p.qualified_name)
        return h is not None and h[1] == p.type

    order, frontier = [], [root]
    kids = defaultdict(list)
    for c, p in parent.items():
        kids[p].append(c)
    while frontier:
        order += frontier
        frontier = [c for f in frontier for c in kids[f] if c in n]
    assert set(order) == set(n)
    for i in order:
        free = n[i].qualified_name not in live_before
        ok[i] = free and (i not in parent or parent_ok(parent[i]))
    restored = {i for i, node in store.nodes.by_ids(list(n)).items() if node.deleted_by is None}
    assert restored == {i for i, v in ok.items() if v}
    for i in restored:
        row = store.nodes.by_id(i)
        s = seed[i]
        assert (row.description, row.owner, list(row.tags)) == (s["description"], s["owner"], sorted(s["tags"]))


def test_inv3_unique_names(undone):
    store = undone[0]
    assert store.con.execute(
        "SELECT qualified_name FROM live_nodes GROUP BY qualified_name HAVING count(*) > 1").fetchall() == []


def test_inv4_report_accounts_once(undone):
    store, result, _, _ = undone
    node_ids, edge_ids = store.log.removals(BIG)
    objs = [json.loads(line) for line in result.lines]
    got_nodes = [o["id"] for o in objs if o["kind"] == "node"]
    got_edges = [o["id"] for o in objs if o["kind"] == "edge"]
    assert sorted(got_nodes) == sorted(node_ids) and len(set(got_nodes)) == len(got_nodes)
    assert sorted(got_edges) == sorted(edge_ids) and len(set(got_edges)) == len(got_edges)
    assert len(result.lines) == len(node_ids) + len(edge_ids)
    assert {o["outcome"] for o in objs} <= {"restored", "merged", "skipped"}
    assert all("reason" in o for o in objs if o["outcome"] != "restored")


def test_inv5_effective_tags(undone):
    store = undone[0]
    tags = {i: set(json.loads(t)) for i, t in store.con.execute("SELECT id, tags FROM live_nodes")}
    parent = dict(store.con.execute("SELECT to_id, from_id FROM live_edges WHERE type = 'contains'"))
    view = {i: json.loads(t) for i, t in store.con.execute("SELECT node_id, tags FROM effective_tags")}
    for i in tags:
        want, cur = set(), i
        while cur is not None:
            want |= tags[cur]
            cur = parent.get(cur)
        assert view[i] == sorted(want), i


def test_inv6_second_undo_refused(undone):
    store = undone[0]
    before = live_rows(store)
    with pytest.raises(AlreadyUndone):
        undo_op(store, BIG)
    assert live_rows(store) == before


def test_scale(undone):
    store, result, _, _ = undone
    objs = [json.loads(line) for line in result.lines]
    root = store.log.get(BIG).node_id
    assert next(o for o in objs if o["id"] == root)["reason_code"] == "name_held"
    assert result.counts["restored_nodes"] >= 500
    assert result.counts["merged"] >= 1


def test_e6_table_restored_under_holder(undone, fx):
    store = undone[0]
    ops, manifest = fx[2], fx[3]
    e6 = next(o for o in ops if o["op_id"] == manifest["E6"][0])["node"]
    n, _ = removed(store)
    original = next(x for x in n.values() if x.qualified_name == e6)
    assert store.nodes.by_id(original.id).deleted_by is None
    holder_of_r = store.nodes.live_by_name(store.nodes.by_id(store.log.get(BIG).node_id).qualified_name)
    assert store.edges.live_parent(original.id).from_id == holder_of_r.id


def test_undo_e6_then_op0001(fx):
    d, _, ops, manifest = fx
    store = build(d, "e6first.db")
    e6_op = manifest["E6"][0]
    e6 = next(o for o in ops if o["op_id"] == e6_op)["node"]
    undo_op(store, e6_op)
    t_prime = store.nodes.live_by_name(e6)
    undo_op(store, BIG)
    n, _ = removed(store)
    original = next(x for x in n.values() if x.qualified_name == e6)
    assert store.nodes.by_id(original.id).deleted_by == BIG             # T's name is held by T′ again
    assert store.nodes.live_by_name(e6).id == t_prime.id
    # Seed 42 recreated every column of T, and undoing E6 brought those back, so each original column's
    # name is held again: all skipped, and the live column of each name hangs under T′.
    cols = [x for x in n.values() if x.qualified_name.startswith(e6 + "/")]
    assert cols
    for c in cols:
        assert store.nodes.by_id(c.id).deleted_by == BIG
        assert store.edges.live_parent(store.nodes.live_by_name(c.qualified_name).id).from_id == t_prime.id
    store.close()


def test_e7_free_columns_reattach_under_recreated_table(undone):
    store, _, _, live_before = undone
    n, e = removed(store)
    parent = {x.to_id: x.from_id for x in e if x.type == "contains"}
    cases = [(c, n[parent[c.id]]) for c in n.values() if c.type == "column"
             and c.qualified_name not in live_before and n[parent[c.id]].qualified_name in live_before]
    assert cases, "seed 42 has E7: free-named columns whose table name was recreated"
    for col, table in cases:
        assert store.nodes.by_id(col.id).deleted_by is None
        assert store.edges.live_parent(col.id).from_id == live_before[table.qualified_name][0]


def test_report_deterministic(fx):
    d = fx[0]
    outs = []
    for name in ("det1.db", "det2.db"):
        store = build(d, name)
        outs.append("".join(undo_op(store, BIG).lines))
        store.close()
    assert outs[0] == outs[1]


def test_runtime(fx):
    d = fx[0]
    t0 = time.perf_counter()
    store = build(d, "timed.db")
    undo_op(store, BIG)
    store.close()
    assert time.perf_counter() - t0 < 5
