"""edit_node handler (spec: docs/superpowers/specs/2026-11-19-op-edit-node-design.md).

Dead and recreated rows are set up through the repositories, so these tests do not depend on the
delete_node / add_node handlers.
"""

from __future__ import annotations

import json

import pytest

from apply.conftest import log_row, op_dict, run, snapshot
from graph_undo.api import edit_node
from graph_undo.api.context import OpResult
from graph_undo.api.registry import HANDLERS
from graph_undo.domain.errors import NodeNotFound
from graph_undo.domain.model import Node
from graph_undo.domain.ops import canonical

T = "conn/db/sc/t"
U = "conn/db/sc/u"
ACTOR = {"kind": "human", "name": "ana"}


def edit(store, qn, op_id="op-0100", **set_):
    return run(store, op_id=op_id, actor=dict(ACTOR), op="edit_node", node=qn, set=set_)


def fields(store, qn):
    n = store.nodes.live_by_name(qn)
    return (n.description, n.owner, n.tags)


def eff(store, qn):
    row = store.con.execute(
        "SELECT e.tags FROM effective_tags e JOIN live_nodes n ON n.id = e.node_id "
        "WHERE n.qualified_name = ?", (qn,)).fetchone()
    return set(json.loads(row[0]))


def raw_row(store, node_id):
    return store.con.execute("SELECT * FROM nodes WHERE id = ?", (node_id,)).fetchone()


def kill(store, qn, op_id):
    """Make the live row named qn dead (as delete_node would mark it), without the delete handler."""
    n = store.nodes.live_by_name(qn)
    assert store.nodes.mark_deleted([n.id], op_id) == 1
    return n


# ---- single fields, combinations, untouched fields ----

def test_edit_description(store):
    r = edit(store, T, description="new")
    assert fields(store, T) == ("new", "o", ())
    assert r == OpResult("op-0100", "applied", node_id="t", edge_id=None)


def test_edit_owner(store):
    edit(store, T, owner="bob")
    assert fields(store, T) == ("d", "bob", ())


def test_edit_tags(store):
    edit(store, T, tags=["pii"])
    assert fields(store, T) == ("d", "o", ("pii",))


def test_edit_all_three(store):
    edit(store, T, description="x", owner="y", tags=["b", "a"])
    assert fields(store, T) == ("x", "y", ("a", "b"))
    assert log_row(store, "op-0100").before == '{"description":"d","owner":"o","tags":[]}'


def test_other_fields_untouched(store):
    before = snapshot(store)
    edit(store, T, owner="z")
    after = snapshot(store)
    assert after["edges"] == before["edges"]
    old_t = [r for r in before["nodes"] if r[0] == "t"][0]
    new_t = [r for r in after["nodes"] if r[0] == "t"][0]
    cols = [r[1] for r in store.con.execute("PRAGMA table_info(nodes)")]
    diff = [c for c, a, b in zip(cols, old_t, new_t) if a != b]
    assert diff == ["owner"]
    assert [r for r in after["nodes"] if r[0] != "t"] == [r for r in before["nodes"] if r[0] != "t"]


def test_successive_edits_accumulate(store):
    edit(store, T, op_id="op-0100", owner="a")
    edit(store, T, op_id="op-0101", description="b")
    assert fields(store, T) == ("b", "a", ())
    assert log_row(store, "op-0101").before == '{"description":"d"}'


# ---- tag normalisation ----

def test_tags_unsorted_sorted(store):
    edit(store, T, tags=["z", "a", "m"])
    assert store.con.execute("SELECT tags FROM nodes WHERE id = 't'").fetchone()[0] == '["a","m","z"]'


def test_tags_duplicates_removed(store):
    edit(store, T, tags=["pii", "gold", "pii"])
    assert fields(store, T)[2] == ("gold", "pii")


def test_tags_empty_list_clears(store):
    edit(store, T, op_id="op-0100", tags=["a", "b"])
    edit(store, T, op_id="op-0101", tags=[])
    assert fields(store, T)[2] == ()
    assert log_row(store, "op-0101").before == '{"tags":["a","b"]}'


def test_payload_keeps_raw_tags(store):
    edit(store, T, tags=["b", "a", "b"])
    assert '"tags":["b","a","b"]' in log_row(store, "op-0100").payload
    assert fields(store, T)[2] == ("a", "b")


# ---- the op-log row ----

def test_log_row_shape(store):
    edit(store, T, op_id="op-0100", owner="bob")
    row = log_row(store, "op-0100")
    raw = op_dict(op_id="op-0100", actor=dict(ACTOR), op="edit_node", node=T, set={"owner": "bob"})
    assert (row.op, row.actor_kind, row.actor_name, row.effect) == ("edit_node", "human", "ana", "applied")
    assert row.node_id == "t" and row.edge_id is None
    assert row.payload == canonical(raw)
    assert store.log.removals("op-0100") == ([], [])


def test_before_json_exact(store):
    edit(store, T, op_id="op-0100", tags=["x"])
    edit(store, T, op_id="op-0101", owner="p", tags=["y"])
    assert log_row(store, "op-0101").before == '{"owner":"o","tags":["x"]}'


# ---- effective_tags ----

def test_ancestor_tag_flows_to_descendants(store):
    before = snapshot(store)
    edit(store, "conn/db", tags=["gold"])
    for qn in ("conn/db/sc", T, U, "conn/db/sc/t/k"):
        assert "gold" in eff(store, qn)
    assert "gold" not in eff(store, "conn")
    after = snapshot(store)
    changed = [a for a, b in zip(after["nodes"], before["nodes"]) if a != b]
    assert len(changed) == 1 and changed[0][0] == "d"
    assert after["edges"] == before["edges"]


def test_ancestor_tag_removal_drops_unless_own(store):
    edit(store, "conn/db", op_id="op-0100", tags=["gold", "pii"])
    edit(store, T, op_id="op-0101", tags=["pii"])
    edit(store, "conn/db", op_id="op-0102", tags=["gold"])
    assert "pii" in eff(store, T)
    assert "pii" in eff(store, "conn/db/sc/t/k")
    assert "pii" not in eff(store, U)
    assert "pii" not in eff(store, "conn/db/sc")
    for qn in ("conn/db/sc", T, U, "conn/db/sc/t/k"):
        assert "gold" in eff(store, qn)


# ---- live and dead rows ----

def test_edit_recreated_name_hits_live_row(store):
    old = kill(store, T, "op-0101")
    dead_before = raw_row(store, old.id)
    new = Node("t2", T, "table", "new-d", "o2", ())
    store.nodes.insert(new)
    store.edges.insert("s", "t2", "contains")

    r = edit(store, T, op_id="op-0103", description="edited")
    assert r.node_id == "t2" != old.id
    assert fields(store, T) == ("edited", "o2", ())
    assert raw_row(store, old.id) == dead_before
    assert store.nodes.by_id(old.id).deleted_by == "op-0101"
    assert store.nodes.by_id(old.id).description == "d"
    row = log_row(store, "op-0103")
    assert row.node_id == "t2"
    assert row.before == '{"description":"new-d"}'


def test_unknown_name_writes_nothing(store):
    before = snapshot(store)
    with pytest.raises(NodeNotFound) as e:
        edit(store, "conn/nope", op_id="op-0100", owner="x")
    assert str(e.value) == "op-0100 node_not_found: node 'conn/nope' is not a live node"
    assert snapshot(store) == before
    assert log_row(store, "op-0100") is None


def test_dead_only_name_not_found(store):
    dead = kill(store, U, "op-0101")
    dead_before = raw_row(store, dead.id)
    with pytest.raises(NodeNotFound) as e:
        edit(store, U, op_id="op-0102", owner="x")
    assert str(e.value) == "op-0102 node_not_found: node 'conn/db/sc/u' is not a live node"
    assert raw_row(store, dead.id) == dead_before
    assert log_row(store, "op-0102") is None


def test_rejected_then_valid_same_op_id(store):
    with pytest.raises(NodeNotFound):
        edit(store, "conn/nope", op_id="op-0100", owner="x")
    r = edit(store, T, op_id="op-0100", owner="x")
    assert r.effect == "applied"
    assert fields(store, T)[1] == "x"


# ---- no-op edit: still applied ----

def test_noop_values_still_applied(store):
    r = edit(store, T, owner="o")
    assert r.effect == "applied"
    row = log_row(store, "op-0100")
    assert row.effect == "applied" and row.before == '{"owner":"o"}'
    assert fields(store, T) == ("d", "o", ())


def test_noop_tags_after_normalisation(store):
    edit(store, T, op_id="op-0100", tags=["a", "b"])
    r = edit(store, T, op_id="op-0101", tags=["b", "a", "a"])
    assert r.effect == "applied"
    assert log_row(store, "op-0101").before == '{"tags":["a","b"]}'


# ---- values that round-trip ----

def test_unicode_round_trip(store):
    edit(store, T, op_id="op-0100", description="données ✓ 数据", owner="Zoë", tags=["é", "ä"])
    assert fields(store, T) == ("données ✓ 数据", "Zoë", ("ä", "é"))
    edit(store, T, op_id="op-0101", description="x", owner="y", tags=[])
    row = log_row(store, "op-0101")
    assert row.before == '{"description":"données ✓ 数据","owner":"Zoë","tags":["ä","é"]}'
    assert "données ✓ 数据" in log_row(store, "op-0100").payload


def test_empty_strings_round_trip(store):
    edit(store, T, description="", owner="", tags=[""])
    assert fields(store, T) == ("", "", ("",))
    assert store.con.execute("SELECT description, owner, tags FROM nodes WHERE id = 't'").fetchone() == \
        ("", "", '[""]')


def test_registry_dispatch():
    assert HANDLERS["edit_node"] is edit_node.handle
