"""Repositories, Store and migration 0002 (spec: apply-foundation, "Database additions", "Repositories")."""

import sqlite3
import subprocess
import uuid
from importlib.resources import files

import pytest

from apply.conftest import BASE, edge, load_store, node
from graph_undo.db.connection import connect, transaction
from graph_undo.db.migrate import LATEST, migrate
from graph_undo.db.oplog import LoggedOp
from graph_undo.db.store import Store
from graph_undo.domain.model import Actor, Edge, Node
from graph_undo.domain.ops import canonical, parse_op

NS = uuid.UUID("6f1c3a52-0d8e-4c1e-9b7a-2f4d5e6a7b8c")


def entry(op_id="op-0001", op="delete_node", effect="applied", node_id=None, edge_id=None, before=None):
    return LoggedOp(op_id, "human", "ann", op, effect, node_id, edge_id, before,
                    canonical({"op_id": op_id, "op": op}))


def kill_node(store, node_id, op_id="op-9000"):
    store.con.execute("UPDATE nodes SET deleted_by = ? WHERE id = ?", (op_id, node_id))


def kill_edge(store, edge_id, op_id="op-9000"):
    store.con.execute("UPDATE edges SET deleted_by = ? WHERE id = ?", (op_id, edge_id))


# ---- schema ----

def test_load_is_at_latest_with_empty_log(store):
    assert LATEST == 2
    assert store.con.execute("PRAGMA user_version").fetchone()[0] == 2
    for t in ("ops", "removed_nodes", "removed_edges"):
        assert store.con.execute(f"SELECT count(*) FROM {t}").fetchone()[0] == 0


def test_db_at_version_1_migrates_keeping_rows(tmp_path):
    src = tmp_path / "migs"
    src.mkdir()
    pk = files("graph_undo.db").joinpath("migrations")
    first = sorted(p.name for p in pk.iterdir() if p.name.endswith(".sql"))[0]
    (src / first).write_text(pk.joinpath(first).read_text(encoding="utf-8"))
    db = tmp_path / "v1.db"
    con = connect(db)
    migrate(con, source=src)
    with transaction(con):
        Store(con).bulk_load([Node("c", "conn", "connection", "d", "o", ()),
                              Node("d", "conn/db", "database", "d", "o", ("x",))], [("c", "d", "contains")])
    con.close()
    r = subprocess.run(["uv", "run", "migrate", "--db", str(db)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert r.stderr == f"migrated {db}: 1 -> 2\n"
    con = connect(db)
    assert con.execute("PRAGMA user_version").fetchone()[0] == 2
    assert con.execute("SELECT count(*) FROM nodes").fetchone()[0] == 2
    assert list(con.execute("SELECT id, from_id, to_id, type FROM edges")) == [(1, "c", "d", "contains")]
    assert con.execute("SELECT count(*) FROM ops").fetchone()[0] == 0


# ---- NodeRepo ----

def test_live_by_name(store):
    n = store.nodes.live_by_name("conn/db/sc")
    assert n == Node("s", "conn/db/sc", "schema", "d", "o", ("a",), None)
    assert store.nodes.live_by_name("nope") is None


def test_live_by_name_skips_dead_row(store):
    kill_node(store, "k")
    assert store.nodes.live_by_name("conn/db/sc/t/k") is None


def test_by_id_returns_dead_rows(store):
    kill_node(store, "k")
    assert store.nodes.by_id("k") == Node("k", "conn/db/sc/t/k", "column", "d", "o", (), "op-9000")
    assert store.nodes.by_id("nope") is None


def test_insert_and_name_reuse_after_death(store):
    kill_node(store, "k")
    new = Node("k2", "conn/db/sc/t/k", "column", "", "", ("z", "b"))
    store.nodes.insert(new)
    got = store.nodes.live_by_name("conn/db/sc/t/k")
    assert got == Node("k2", "conn/db/sc/t/k", "column", "", "", ("b", "z"), None)
    with pytest.raises(sqlite3.IntegrityError):
        store.nodes.insert(Node("k3", "conn/db/sc/t/k", "column", "", "", ()))


def test_update_fields(store):
    store.nodes.update_fields("t", {"owner": "bob", "tags": ("pii", "gold", "pii")})
    n = store.nodes.by_id("t")
    assert (n.description, n.owner, n.tags) == ("d", "bob", ("gold", "pii"))
    assert store.con.execute("SELECT tags FROM nodes WHERE id='t'").fetchone()[0] == '["gold","pii"]'
    store.nodes.update_fields("t", {"tags": ()})
    assert store.nodes.by_id("t").tags == ()


@pytest.mark.parametrize("bad", [{"qualified_name": "x"}, {"type": "table"}, {"deleted_by": "op-1"}])
def test_update_fields_refuses_other_keys(store, bad):
    with pytest.raises(ValueError):
        store.nodes.update_fields("t", bad)


def test_update_fields_refuses_dead_row(store):
    kill_node(store, "k")
    with pytest.raises(ValueError):
        store.nodes.update_fields("k", {"owner": "x"})
    assert store.nodes.by_id("k").owner == "o"


def test_live_subtree_ids(store):
    assert store.nodes.live_subtree_ids("s") == ["s", "t", "u", "k"]
    assert store.nodes.live_subtree_ids("k") == ["k"]
    assert store.nodes.live_subtree_ids("c")[0] == "c"
    assert set(store.nodes.live_subtree_ids("c")) == {"c", "d", "s", "t", "u", "k"}


def test_live_subtree_ids_ignores_references_and_dead_edges(store):
    # t->u is a reference: t's subtree is t, k only
    assert store.nodes.live_subtree_ids("t") == ["t", "k"]
    kill_edge(store, 3)                     # s>t dead
    assert store.nodes.live_subtree_ids("s") == ["s", "u"]
    kill_node(store, "s")
    assert store.nodes.live_subtree_ids("s") == []


def test_node_mark_deleted(store):
    kill_node(store, "k", "op-0001")
    assert store.nodes.mark_deleted(["t", "k", "nope"], "op-0002") == 1
    assert store.nodes.by_id("t").deleted_by == "op-0002"
    assert store.nodes.by_id("k").deleted_by == "op-0001"      # already dead: unchanged
    assert store.nodes.mark_deleted([], "op-0003") == 0


# ---- EdgeRepo ----

def test_edge_live(store):
    assert store.edges.live("t", "u", "references") == Edge(6, "t", "u", "references", None)
    assert store.edges.live("u", "t", "references") is None
    assert store.edges.live("t", "u", "contains") is None
    kill_edge(store, 6)
    assert store.edges.live("t", "u", "references") is None


def test_live_parent(store):
    assert store.edges.live_parent("t") == Edge(3, "s", "t", "contains", None)
    assert store.edges.live_parent("c") is None
    kill_edge(store, 3)
    assert store.edges.live_parent("t") is None


def test_live_touching(store):
    got = store.edges.live_touching(["u"])
    assert [e.id for e in got] == [4, 6]          # into u (contains) and into u (reference)
    assert [e.id for e in store.edges.live_touching(["t"])] == [3, 5, 6]   # both directions, both types
    kill_edge(store, 6)
    assert [e.id for e in store.edges.live_touching(["t", "u"])] == [3, 4, 5]
    assert store.edges.live_touching([]) == []


def test_edge_insert_returns_new_id(store):
    e = store.edges.insert("u", "t", "references")
    assert e == Edge(7, "u", "t", "references", None)
    kill_edge(store, 6)
    again = store.edges.insert("t", "u", "references")      # identical to a dead row: new row
    assert again.id == 8 and store.edges.live("t", "u", "references").id == 8


def test_edge_mark_deleted(store):
    kill_edge(store, 5, "op-0001")
    assert store.edges.mark_deleted([5, 6], "op-0002") == 1
    rows = dict(store.con.execute("SELECT id, deleted_by FROM edges WHERE id IN (5, 6)").fetchall())
    assert rows == {5: "op-0001", 6: "op-0002"}


# ---- OpLogRepo ----

def test_record_get_round_trip(store):
    e = LoggedOp("op-0001", "human", "ann", "edit_node", "applied", "t", None, '{"owner":"o"}',
                 '{"op":"edit_node"}')
    store.log.record(e)
    assert store.log.get("op-0001") == e
    assert store.log.get("op-0002") is None
    assert store.con.execute("SELECT seq FROM ops WHERE op_id='op-0001'").fetchone()[0] == 1


def test_logged_op_of():
    op = parse_op({"op_id": "op-0005", "actor": {"kind": "ingest", "name": "crawler"}, "op": "add_node",
                   "qualified_name": "conn/x", "type": "database", "parent": "conn"})
    got = LoggedOp.of(op, "noop", node_id="c")
    assert got == LoggedOp("op-0005", "ingest", "crawler", "add_node", "noop", "c", None, None,
                           canonical(op.raw))


def test_record_twice_raises(store):
    store.log.record(entry())
    with pytest.raises(sqlite3.IntegrityError):
        store.log.record(entry())


def test_record_checks(store):
    with pytest.raises(sqlite3.IntegrityError):
        store.log.record(entry(effect="skipped"))
    with pytest.raises(sqlite3.IntegrityError):
        store.log.record(entry(op="undo"))
    with pytest.raises(sqlite3.IntegrityError):
        store.log.record(entry(node_id="nope"))
    with pytest.raises(sqlite3.IntegrityError):
        store.log.record(entry(edge_id=99))


def test_removals_round_trip(store):
    store.log.record(entry())
    store.log.add_removals("op-0001", ["s", "t"], [2, 6, 3])
    nodes, edges = store.log.removals("op-0001")
    assert nodes == ["s", "t"] and edges == [2, 6, 3]
    assert all(type(i) is int for i in edges)
    assert store.log.removals("op-0002") == ([], [])
    store.log.add_removals("op-0001", [], [])


@pytest.mark.parametrize("args", [("op-0001", ["nope"], []), ("op-0001", [], [99]), ("op-0002", ["s"], []),
                                  ("op-0002", [], [1])])
def test_removal_foreign_keys(store, args):
    store.log.record(entry())
    with pytest.raises(sqlite3.IntegrityError):
        store.log.add_removals(*args)


def test_removal_twice_raises(store):
    store.log.record(entry())
    store.log.add_removals("op-0001", ["s"], [])
    with pytest.raises(sqlite3.IntegrityError):
        store.log.add_removals("op-0001", ["s"], [])


# ---- Store ----

def test_mint_node_id(store):
    assert store.mint_node_id("op-0001") == str(uuid.uuid5(NS, "op-0001"))
    assert Store.mint_node_id("op-0001") == store.mint_node_id("op-0001")
    assert store.mint_node_id("op-0002") != store.mint_node_id("op-0001")


def test_repos_do_not_commit(store, tmp_path):
    with pytest.raises(RuntimeError):
        with transaction(store.con):
            store.nodes.insert(Node("x", "conn/x", "database", "", "", ()))
            store.edges.insert("c", "x", "contains")
            store.log.record(entry())
            store.log.add_removals("op-0001", ["x"], [])
            store.nodes.mark_deleted(["x"], "op-0001")
            raise RuntimeError
    assert store.nodes.by_id("x") is None
    assert store.log.get("op-0001") is None
    assert store.con.execute("SELECT count(*) FROM edges").fetchone()[0] == 6


def test_custom_seed_helper(tmp_path):
    s = load_store(tmp_path, BASE + [node("v", "conn/db/sc/v", "table"), edge("s", "v")], name="w.db")
    assert s.edges.live_parent("v").id == 7
    s.close()


def test_actor_type_available():
    assert Actor("human", "x").kind == "human"
