"""Migration 0003 and the repository reads/writes undo needs (spec: undo-design, "Undo log", "Code layout")."""

import sqlite3

import pytest

from apply.conftest import run
from graph_undo.db.migrate import LATEST
from graph_undo.db.undolog import UndoItem


def test_latest_is_3(store):
    assert LATEST == 3
    assert store.con.execute("PRAGMA user_version").fetchone()[0] == 3
    for t in ("undos", "undo_items"):
        assert store.con.execute(f"SELECT count(*) FROM {t}").fetchone()[0] == 0


def test_revive_only_rows_of_that_op(store):
    run(store, op_id="op-0100", op="delete_node", node="conn/db/sc/u")
    store.con.execute("UPDATE nodes SET deleted_by = 'op-0050' WHERE id = 't'")
    assert store.nodes.revive(["u", "t"], "op-0100") == 1
    assert store.nodes.by_id("u").deleted_by is None
    assert store.nodes.by_id("t").deleted_by == "op-0050"
    assert store.edges.revive([4, 6], "op-0100") == 2
    assert store.edges.by_ids([6, 4])[0].id == 4
    assert all(e.deleted_by is None for e in store.edges.by_ids([4, 6]))


def test_bulk_reads(store):
    run(store, op_id="op-0100", op="delete_node", node="conn/db/sc/u")
    got = store.nodes.by_ids(["u", "t", "nope"])
    assert set(got) == {"u", "t"} and got["u"].deleted_by == "op-0100"
    assert set(store.nodes.live_by_names(["conn/db/sc/t", "conn/db/sc/u", "x"])) == {"conn/db/sc/t"}
    assert store.edges.live_triples([("s", "t", "contains"), ("s", "u", "contains")]) == {("s", "t", "contains")}
    parents = store.edges.live_parents(["t", "u", "c"])
    assert set(parents) == {"t"} and parents["t"].from_id == "s"


def test_bulk_reads_2000_ids(store):
    ids = [f"n{i}" for i in range(2000)]
    assert store.nodes.by_ids(ids) == {}
    assert store.nodes.live_by_names(ids) == {}
    assert store.nodes.revive(ids, "op-0100") == 0
    assert store.edges.by_ids(list(range(10, 2010))) == []
    assert store.edges.revive(list(range(10, 2010)), "op-0100") == 0
    assert store.edges.live_triples([(i, i, "contains") for i in ids]) == set()
    assert store.edges.live_parents(ids) == {}
    assert store.log.creators(ids) == {}


def test_creators_and_seq(store):
    run(store, op_id="op-0101", op="add_node", qualified_name="conn/db/sc/z", type="table",
        parent="conn/db/sc", description="", owner="", tags=[])
    run(store, op_id="op-0102", op="edit_node", node="conn/db/sc/z", set={"owner": "x"})
    new_id = store.nodes.live_by_name("conn/db/sc/z").id
    assert store.log.creators([new_id, "t"]) == {new_id: ("op-0101", "human")}
    assert store.log.seq("op-0102") == 2 and store.log.seq("op-0999") is None
    assert store.log.last_op_id() == "op-0102"


def test_undo_log_record_get_counts(store):
    run(store, op_id="op-0100", op="delete_node", node="conn/db/sc/u")
    assert store.undo_log.get("op-0100") is None
    seq = store.undo_log.record("op-0100", 1, "line\n")
    store.undo_log.add_items("op-0100", [
        UndoItem("node", "u", "restored", None, None),
        UndoItem("edge", "4", "restored", None, None),
        UndoItem("edge", "6", "merged", "reparented", 4),
        UndoItem("edge", "7", "skipped", "endpoint_deleted", None),
    ])
    rec = store.undo_log.get("op-0100")
    assert (rec.undo_seq, rec.op_id, rec.after_op_id, rec.report) == (seq, "op-0100", "op-0100", "line\n")
    assert store.undo_log.counts("op-0100") == {"restored_nodes": 1, "restored_edges": 1, "merged": 1,
                                                 "skipped": 1}


def test_undos_op_id_unique(store):
    run(store, op_id="op-0100", op="delete_node", node="conn/db/sc/u")
    store.undo_log.record("op-0100", 1, "")
    with pytest.raises(sqlite3.IntegrityError):
        store.undo_log.record("op-0100", 1, "")
