"""delete_edge handler (spec: docs/superpowers/specs/2026-11-19-op-delete-edge-design.md).

BASE (see conftest): contains c>d=1 d>s=2 s>t=3 s>u=4 t>k=5; references t->u=6.
"""

from __future__ import annotations

import json

import pytest

from apply.conftest import is_stub, live_names, log_row, requires, run, snapshot
from graph_undo.api.context import OpResult
from graph_undo.db.oplog import LoggedOp
from graph_undo.domain.errors import ActorNotAllowed, EdgeNotFound, MalformedOp, NodeNotFound
from graph_undo.domain.ops import DeleteEdge, canonical, parse_op

C, D, S, T, U, K = "conn", "conn/db", "conn/db/sc", "conn/db/sc/t", "conn/db/sc/u", "conn/db/sc/t/k"
REF_TU = 6        # BASE reference t -> u
CONTAINS_ST = 3
CONTAINS_TK = 5


# ---- local helpers (setup and reads only) ----

def de(f, t, type="references", op_id="op-0001", who="ana"):
    return {"op_id": op_id, "actor": {"kind": "human", "name": who}, "op": "delete_edge",
            "from": f, "to": t, "type": type}


def go(store, d):
    """Run one op dict (run() takes op_dict fields; de() builds the whole dict)."""
    return run(store, **d)


def nid(store, qn):
    return store.nodes.live_by_name(qn).id


def seed_ref(store, f, t, type="references") -> int:
    return store.edges.insert(nid(store, f), nid(store, t), type).id


def seed_tags(store):
    for qn, tags in ((C, ["c"]), (D, ["d"]), (S, ["s"]), (T, ["tt"]), (U, ["uu"]), (K, ["kk"])):
        store.nodes.update_fields(nid(store, qn), {"tags": tuple(tags)})


def eff(store, qn):
    row = store.con.execute("SELECT tags FROM effective_tags WHERE node_id = ?", (nid(store, qn),)).fetchone()
    return json.loads(row[0])


def edge_row(store, i):
    return store.con.execute("SELECT from_id, to_id, type, deleted_by FROM edges WHERE id = ?", (i,)).fetchone()


def live_edge_ids(store):
    return {r[0] for r in store.con.execute("SELECT id FROM live_edges")}


def removed_edges_rows(store):
    return store.con.execute("SELECT op_id, edge_id FROM removed_edges ORDER BY rowid").fetchall()


def assert_removals_consistent(store):
    for op_id, edge_id in removed_edges_rows(store):
        assert edge_row(store, edge_id)[3] == op_id
    for op_id, node_id in store.con.execute("SELECT op_id, node_id FROM removed_nodes"):
        assert store.con.execute("SELECT deleted_by FROM nodes WHERE id = ?", (node_id,)).fetchone()[0] == op_id


def nodes_rows(store):
    return store.con.execute("SELECT * FROM nodes ORDER BY rowid").fetchall()


# ---- Task 1: happy path and errors ----

def test_delete_reference_soft_deletes_one_row(store):
    n_edges = store.con.execute("SELECT count(*) FROM edges").fetchone()[0]
    nodes_before = nodes_rows(store)
    live_before = live_edge_ids(store)
    r = go(store, de(T, U))
    assert r == OpResult("op-0001", "applied", edge_id=REF_TU, removed_edges=1)
    assert edge_row(store, REF_TU) == ("t", "u", "references", "op-0001")
    assert store.con.execute("SELECT count(*) FROM edges").fetchone()[0] == n_edges
    assert nodes_rows(store) == nodes_before
    assert store.edges.live("t", "u", "references") is None
    assert live_edge_ids(store) == live_before - {REF_TU}


def test_log_row(store):
    d = de(T, U)
    go(store, d)
    assert log_row(store, "op-0001") == LoggedOp("op-0001", "human", "ana", "delete_edge", "applied",
                                                 None, REF_TU, None, canonical(d))
    assert store.con.execute("SELECT count(*) FROM ops").fetchone()[0] == 1


def test_removals_match_deleted_by(store):
    go(store, de(T, U))
    assert store.log.removals("op-0001") == ([], [REF_TU])
    assert removed_edges_rows(store) == [("op-0001", REF_TU)]
    assert store.con.execute("SELECT count(*) FROM removed_nodes WHERE op_id = 'op-0001'").fetchone()[0] == 0
    assert_removals_consistent(store)


def test_reverse_direction_untouched(store):
    b = seed_ref(store, U, T)
    go(store, de(T, U))
    assert edge_row(store, REF_TU)[3] == "op-0001"
    assert edge_row(store, b)[3] is None and b in live_edge_ids(store)


def test_wrong_type_is_edge_not_found(store):
    with pytest.raises(EdgeNotFound) as e:
        go(store, de(S, T, "references"))
    assert str(e.value) == "op-0001 edge_not_found: no live references edge 'conn/db/sc' -> 'conn/db/sc/t'"
    assert CONTAINS_ST in live_edge_ids(store)


def test_other_type_same_endpoints_survives(store):
    r = seed_ref(store, T, K)
    go(store, de(T, K))
    assert edge_row(store, r)[3] == "op-0001"
    assert CONTAINS_TK in live_edge_ids(store)
    assert store.edges.live_parent("k").id == CONTAINS_TK


def test_from_not_live(store):
    with pytest.raises(NodeNotFound) as e:
        go(store, de("conn/nope", U))
    assert str(e.value) == "op-0001 node_not_found: from 'conn/nope' is not a live node"


def test_to_not_live(store):
    with pytest.raises(NodeNotFound) as e:
        go(store, de(T, "conn/nope"))
    assert str(e.value) == "op-0001 node_not_found: to 'conn/nope' is not a live node"


def test_both_missing_reports_from(store):
    with pytest.raises(NodeNotFound) as e:
        go(store, de("conn/x", "conn/y"))
    assert str(e.value) == "op-0001 node_not_found: from 'conn/x' is not a live node"


def test_self_loop_is_edge_not_found(store):
    with pytest.raises(EdgeNotFound) as e:
        go(store, de(T, T))
    assert str(e.value) == "op-0001 edge_not_found: no live references edge 'conn/db/sc/t' -> 'conn/db/sc/t'"


REJECTIONS = {
    "wrong_type": (de(S, T, "references"), EdgeNotFound),
    "from_missing": (de("conn/nope", U), NodeNotFound),
    "to_missing": (de(T, "conn/nope"), NodeNotFound),
    "both_missing": (de("conn/x", "conn/y"), NodeNotFound),
    "self_loop": (de(T, T), EdgeNotFound),
}


@pytest.mark.parametrize("case", sorted(REJECTIONS))
def test_rejection_writes_nothing(store, case):
    d, exc = REJECTIONS[case]
    before = snapshot(store)
    with pytest.raises(exc):
        go(store, d)
    assert snapshot(store) == before
    assert store.log.get("op-0001") is None
    assert go(store, de(T, U, op_id="op-0002")).effect == "applied"


# ---- Task 2: contains deletes, effective_tags, move ----

def test_delete_contains_leaves_child_parentless(store):
    seed_tags(store)
    go(store, de(T, K, "contains"))
    assert edge_row(store, CONTAINS_TK)[3] == "op-0001"
    assert store.edges.live_parent("k") is None
    assert K in live_names(store)
    assert store.con.execute("SELECT deleted_by FROM nodes WHERE id = 'k'").fetchone()[0] is None
    assert eff(store, K) == ["kk"]
    assert eff(store, T) == ["c", "d", "s", "tt"]


def test_delete_contains_subtree_loses_ancestor_tags(store):
    seed_tags(store)
    go(store, de(S, T, "contains"))
    assert eff(store, T) == ["tt"]
    assert eff(store, K) == ["kk", "tt"]
    assert eff(store, U) == ["c", "d", "s", "uu"]
    assert {T, K} <= live_names(store)
    assert CONTAINS_TK in live_edge_ids(store)


def test_parentless_state_is_accepted(store):
    go(store, de(T, K, "contains"))
    assert go(store, de(T, U, op_id="op-0002")).effect == "applied"
    assert_removals_consistent(store)


def test_move_k_from_t_to_u(store):
    requires("add_edge")
    seed_tags(store)
    go(store, de(T, K, "contains"))
    go(store, {**de(U, K, "contains", op_id="op-0002"), "op": "add_edge"})
    assert edge_row(store, CONTAINS_TK)[3] == "op-0001"
    assert store.edges.live_parent("k").from_id == "u"
    assert eff(store, K) == ["c", "d", "kk", "s", "uu"]
    assert removed_edges_rows(store) == [("op-0001", CONTAINS_TK)]
    assert store.con.execute("SELECT seq FROM ops ORDER BY seq").fetchall() == [(1,), (2,)]
    assert_removals_consistent(store)


def test_repoint_reference(store):
    requires("add_edge")
    go(store, de(T, U))
    go(store, {**de(T, K, op_id="op-0002"), "op": "add_edge"})
    new = store.edges.live("t", "k", "references")
    assert edge_row(store, REF_TU)[3] == "op-0001"
    assert new is not None and new.id != REF_TU
    assert_removals_consistent(store)


# ---- Task 3: lifecycle ----

def test_already_dead_is_edge_not_found(store):
    go(store, de(T, U))
    with pytest.raises(EdgeNotFound) as e:
        go(store, de(T, U, op_id="op-0002"))
    assert str(e.value) == "op-0002 edge_not_found: no live references edge 'conn/db/sc/t' -> 'conn/db/sc/u'"
    assert edge_row(store, REF_TU)[3] == "op-0001"
    assert store.log.get("op-0002") is None
    assert store.log.removals("op-0002") == ([], [])


def test_dead_duplicate_keeps_original_deleted_by(store):
    a = REF_TU
    go(store, de(T, U))
    b = seed_ref(store, T, U)
    go(store, de(T, U, op_id="op-0002"))
    assert edge_row(store, a)[3] == "op-0001"
    assert edge_row(store, b)[3] == "op-0002"
    assert removed_edges_rows(store) == [("op-0001", a), ("op-0002", b)]
    assert store.log.removals("op-0002") == ([], [b])
    assert_removals_consistent(store)


def test_readd_via_add_edge_then_redelete(store):
    requires("add_edge")
    go(store, de(T, U))
    go(store, {**de(T, U, op_id="op-0002"), "op": "add_edge"})
    b = store.edges.live("t", "u", "references").id
    go(store, de(T, U, op_id="op-0003"))
    assert edge_row(store, REF_TU)[3] == "op-0001" and edge_row(store, b)[3] == "op-0003"
    assert store.con.execute("SELECT count(*) FROM ops").fetchone()[0] == 3
    assert store.log.removals("op-0002") == ([], [])
    assert_removals_consistent(store)


def _cascade_delete_t(store) -> int:
    """Remove T (and K) as delete_node would, as op-0001. Returns the id of the seeded reference U->K."""
    r = seed_ref(store, U, K)
    if not is_stub("delete_node"):
        go(store, {"op_id": "op-0001", "actor": {"kind": "human", "name": "ana"}, "op": "delete_node",
                   "node": T})
    else:
        assert store.nodes.mark_deleted(["t", "k"], "op-0001") == 2
        assert store.edges.mark_deleted([CONTAINS_ST, CONTAINS_TK, REF_TU, r], "op-0001") == 4
        raw = {"op_id": "op-0001", "actor": {"kind": "human", "name": "ana"}, "op": "delete_node", "node": T}
        store.log.record(LoggedOp("op-0001", "human", "ana", "delete_node", "applied", "t", None, None,
                                  canonical(raw)))
        store.log.add_removals("op-0001", ["t", "k"], [CONTAINS_ST, CONTAINS_TK, REF_TU, r])
    return r


def test_cascade_removed_edge_endpoint_not_live(store):
    r = _cascade_delete_t(store)
    with pytest.raises(NodeNotFound) as e:
        go(store, de(U, K, op_id="op-0002"))
    assert str(e.value) == "op-0002 node_not_found: to 'conn/db/sc/t/k' is not a live node"
    assert edge_row(store, r)[3] == "op-0001"
    assert store.log.removals("op-0002") == ([], [])


def test_cascade_removed_edge_name_recreated(store):
    requires("add_node")
    r = _cascade_delete_t(store)
    go(store, {"op_id": "op-0002", "actor": {"kind": "ingest", "name": "crawler"}, "op": "add_node",
               "qualified_name": K, "type": "column", "parent": U})
    assert nid(store, K) != "k"
    with pytest.raises(EdgeNotFound):
        go(store, de(U, K, op_id="op-0003"))
    assert edge_row(store, r)[3] == "op-0001"


def test_nodes_never_touched(store):
    before = nodes_rows(store)
    go(store, de(T, U))
    go(store, de(T, K, "contains", op_id="op-0002"))
    with pytest.raises(EdgeNotFound):
        go(store, de(T, U, op_id="op-0003"))
    assert nodes_rows(store) == before
    assert_removals_consistent(store)


# ---- parse-level cases (parse_op only, no db) ----

def test_parse_delete_edge_ok():
    d = de(T, U)
    op = parse_op(d)
    assert isinstance(op, DeleteEdge) and (op.from_, op.to, op.type) == (T, U, "references") and op.raw == d


def test_parse_delete_edge_ingest_actor_not_allowed():
    d = {**de(T, U), "actor": {"kind": "ingest", "name": "crawler"}}
    with pytest.raises(ActorNotAllowed) as e:
        parse_op(d)
    assert str(e.value) == "op-0001 actor_not_allowed: ingest may not delete_edge"
    del d["to"]
    with pytest.raises(ActorNotAllowed):
        parse_op(d)


@pytest.mark.parametrize("mutate", [
    lambda d: d.update(type="likes"),
    lambda d: d.pop("from"), lambda d: d.pop("to"), lambda d: d.pop("type"),
    lambda d: d.update(description="x"),
    lambda d: d.update({"from": 7}),
], ids=["unknown_type", "missing_from", "missing_to", "missing_type", "extra_key", "non_string_from"])
def test_parse_delete_edge_malformed(mutate):
    d = de(T, U)
    mutate(d)
    with pytest.raises(MalformedOp):
        parse_op(d)
