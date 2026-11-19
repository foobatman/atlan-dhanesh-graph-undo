"""undo_op against a real store (spec: undo-design). Setup ops go through apply_op.

BASE (tests/apply/conftest.py): conn [c] > conn/db [d] > conn/db/sc [s, tags a] > conn/db/sc/t [t] > .../t/k [k]
                                                                         > conn/db/sc/u [u]
edges: c>d=1 d>s=2 s>t=3 s>u=4 t>k=5, references t->u=6
"""

import json

import pytest

from apply.conftest import BASE, run, snapshot, live_names
from graph_undo.api.undo import undo_op
from graph_undo.db.store import Store
from graph_undo.domain.errors import AlreadyUndone, MalformedOp, NameTaken, NotUndoable, OpNotFound

INGEST = {"kind": "ingest", "name": "crawler"}


def report(result):
    return {(o["kind"], o["id"]): o for o in map(json.loads, result.lines)}


def short(result):
    return {k: (o["outcome"], o.get("reason_code")) for k, o in report(result).items()}


def live_edge_ids(store):
    return sorted(r[0] for r in store.con.execute("SELECT id FROM live_edges"))


def node_row(store, node_id):
    return store.con.execute("SELECT * FROM nodes WHERE id = ?", (node_id,)).fetchone()


def test_undo_delete_restores_subtree(store):
    run(store, op_id="op-0100", op="delete_node", node="conn/db/sc")
    result = undo_op(store, "op-0100")
    assert live_names(store) == {r["qualified_name"] for r in BASE if "qualified_name" in r}
    assert live_edge_ids(store) == [1, 2, 3, 4, 5, 6]
    assert all(o in (("restored", None),) for o in short(result).values())
    assert store.nodes.by_id("t").description == "d"
    tags = store.con.execute("SELECT tags FROM effective_tags WHERE node_id = 'k'").fetchone()[0]
    assert json.loads(tags) == ["a"]
    assert result.counts == {"restored_nodes": 4, "restored_edges": 5, "merged": 0, "skipped": 0}


def test_undo_with_recreated_root(store):
    run(store, op_id="op-0100", op="delete_node", node="conn/db/sc")
    run(store, op_id="op-0101", actor=INGEST, op="add_node", qualified_name="conn/db/sc", type="schema",
        parent="conn/db")
    run(store, op_id="op-0102", actor=INGEST, op="add_node", qualified_name="conn/db/sc/t", type="table",
        parent="conn/db/sc")
    run(store, op_id="op-0103", op="edit_node", node="conn/db/sc", set={"owner": "zed"})
    sc2, t2 = Store.mint_node_id("op-0101"), Store.mint_node_id("op-0102")
    before = node_row(store, sc2)
    result = undo_op(store, "op-0100")
    r = report(result)
    assert short(result) == {
        ("node", "s"): ("skipped", "name_held"), ("node", "t"): ("skipped", "name_held"),
        ("node", "u"): ("restored", None), ("node", "k"): ("restored", None),
        ("edge", 2): ("skipped", "endpoint_not_restored"), ("edge", 3): ("skipped", "endpoint_not_restored"),
        ("edge", 4): ("merged", "reparented"), ("edge", 5): ("merged", "reparented"),
        ("edge", 6): ("skipped", "endpoint_not_restored"),
    }
    assert r[("node", "s")]["reason"] == f"name held by {sc2} created by op-0101 (ingest)"
    assert r[("node", "u")]["parent_id"] == sc2 and r[("node", "k")]["parent_id"] == t2
    for old, (frm, to) in {4: (sc2, "u"), 5: (t2, "k")}.items():
        new = store.edges.by_ids([r[("edge", old)]["new_edge_id"]])[0]
        assert (new.from_id, new.to_id, new.type, new.deleted_by) == (frm, to, "contains", None)
    assert node_row(store, sc2) == before                 # inv 1: the human's edit on the holder is untouched
    assert store.nodes.by_id("s").deleted_by == "op-0100"


def test_human_claimed_name(store):
    run(store, op_id="op-0100", op="delete_node", node="conn/db/sc/u")
    run(store, op_id="op-0101", op="add_node", qualified_name="conn/db/sc/u", type="table", parent="conn/db/sc",
        description="mine", owner="ann", tags=[])
    result = undo_op(store, "op-0100")
    assert short(result)[("node", "u")] == ("skipped", "name_held")
    assert report(result)[("node", "u")]["reason"].endswith("created by op-0101 (human)")
    assert store.nodes.live_by_name("conn/db/sc/u").description == "mine"


def test_endpoint_deleted(store):
    run(store, op_id="op-0100", op="delete_node", node="conn/db/sc/u")
    run(store, op_id="op-0101", op="delete_node", node="conn/db/sc/t")
    result = undo_op(store, "op-0100")
    assert short(result) == {("node", "u"): ("restored", None), ("edge", 4): ("restored", None),
                             ("edge", 6): ("skipped", "endpoint_deleted")}
    assert report(result)[("edge", 6)]["reason"] == "endpoint conn/db/sc/t was deleted by op-0101 (undo op-0101 first to restore this)"


def test_undo_delete_edge(store):
    run(store, op_id="op-0100", op="delete_edge", **{"from": "conn/db/sc/t", "to": "conn/db/sc/u",
                                                     "type": "references"})
    result = undo_op(store, "op-0100")
    assert short(result) == {("edge", 6): ("restored", None)}
    assert 6 in live_edge_ids(store)


def test_undo_delete_edge_moved_child(store):
    run(store, op_id="op-0100", op="delete_edge", **{"from": "conn/db/sc", "to": "conn/db/sc/t",
                                                     "type": "contains"})
    run(store, op_id="op-0101", op="add_node", qualified_name="conn/db/sc2", type="schema", parent="conn/db",
        description="", owner="", tags=[])
    run(store, op_id="op-0102", op="add_edge", **{"from": "conn/db/sc2", "to": "conn/db/sc/t", "type": "contains"})
    result = undo_op(store, "op-0100")
    assert short(result) == {("edge", 3): ("skipped", "child_has_parent")}
    assert report(result)[("edge", 3)]["reason"] == "conn/db/sc/t now has parent conn/db/sc2"


def test_errors(store):
    with pytest.raises(OpNotFound) as e:
        undo_op(store, "op-0999")
    assert str(e.value) == "op-0999 op_not_found: no applied op with id 'op-0999'"
    run(store, op_id="op-0100", op="edit_node", node="conn/db/sc", set={"owner": "x"})
    with pytest.raises(NotUndoable) as e:
        undo_op(store, "op-0100")
    assert str(e.value) == "op-0100 not_undoable: edit_node cannot be undone; only delete_node and delete_edge can"
    with pytest.raises(MalformedOp) as e:
        undo_op(store, "op-1")
    assert str(e.value) == "op-1 malformed: op_id 'op-1' does not match op-NNNN"


def test_second_undo_refused(store):
    run(store, op_id="op-0100", op="delete_node", node="conn/db/sc/u")
    run(store, op_id="op-0101", op="edit_node", node="conn/db/sc", set={"owner": "x"})
    undo_op(store, "op-0100")
    snap = snapshot(store)
    with pytest.raises(AlreadyUndone) as e:
        undo_op(store, "op-0100")
    assert str(e.value) == ("op-0100 already_undone: already undone (undo #1, after op-0101); restored 1 nodes, "
                            "2 edges, merged 0, skipped 0. Undo runs once per op.")
    assert snapshot(store) == snap


def test_failure_rolls_back(store, monkeypatch):
    run(store, op_id="op-0100", op="delete_node", node="conn/db/sc")
    snap = snapshot(store)

    def boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(store.undo_log, "add_items", boom)
    with pytest.raises(RuntimeError):
        undo_op(store, "op-0100")
    assert snapshot(store) == snap


def test_undo_log_rows(store):
    run(store, op_id="op-0100", op="delete_node", node="conn/db/sc")
    result = undo_op(store, "op-0100")
    assert store.undo_log.get("op-0100").report == "".join(result.lines)
    n = store.con.execute("SELECT count(*) FROM undo_items WHERE op_id = 'op-0100'").fetchone()[0]
    assert n == len(result.lines) == 4 + 5


def test_apply_after_undo(store):
    run(store, op_id="op-0100", op="delete_node", node="conn/db/sc")
    undo_op(store, "op-0100")
    with pytest.raises(NameTaken):
        run(store, op_id="op-0101", op="add_node", qualified_name="conn/db/sc/u", type="table",
            parent="conn/db/sc", description="", owner="", tags=[])
    res = run(store, op_id="op-0102", op="delete_node", node="conn/db/sc")
    assert (res.removed_nodes, res.removed_edges) == (4, 5)
    assert store.log.removals("op-0102")[0][0] == "s"
