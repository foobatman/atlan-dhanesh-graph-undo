"""End-to-end: load seed 42, apply ops 42, compare with the independent replay (foundation plan, Task 5)."""

import json
import time

import pytest

from graph_undo.api.apply import apply_file
from graph_undo.api.load import load_seed
from graph_undo.db.connection import connect
from graph_undo.fixture.ops import generate_ops
from graph_undo.fixture.seed import generate_seed, serialize
from replay import replay

TABLES = ("nodes", "edges", "ops", "removed_nodes", "removed_edges")


@pytest.fixture(scope="module")
def fixture42(tmp_path_factory):
    d = tmp_path_factory.mktemp("fx")
    nodes, edges = generate_seed(42)
    ops, _ = generate_ops(42, nodes, edges)
    (d / "seed.jsonl").write_text(serialize(nodes, edges))
    (d / "ops.jsonl").write_text(serialize(ops, []))
    return d, nodes, edges, ops


def build(d, name):
    db = d / name
    load_seed(d / "seed.jsonl", db)
    rejects = []
    summary = apply_file(d / "ops.jsonl", db, lambda line, e: rejects.append((line, str(e))))
    return db, summary, rejects


def snapshot(db):
    con = connect(db)
    return {t: list(con.execute(f"SELECT * FROM {t} ORDER BY 1, 2")) for t in TABLES}


def live_by_name(db):
    con = connect(db)
    nodes = {qn: (t, d, o, json.loads(tags)) for qn, t, d, o, tags in con.execute(
        "SELECT qualified_name, type, description, owner, tags FROM live_nodes")}
    name = dict(con.execute("SELECT id, qualified_name FROM live_nodes"))
    parent, refs = {}, set()
    for f, t, typ in con.execute("SELECT from_id, to_id, type FROM live_edges"):
        if typ == "contains":
            parent[name[t]] = name[f]
        else:
            refs.add((name[f], name[t]))
    return nodes, parent, refs


def test_seed42_scenario(fixture42):
    d, nodes, edges, ops = fixture42
    t0 = time.perf_counter()
    db, summary, rejects = build(d, "a.db")
    assert time.perf_counter() - t0 < 5
    assert rejects == []
    assert summary.rejected == 0 and summary.skipped == 0
    assert summary.applied + summary.noop == len(ops) == 309

    want = replay(nodes, edges, ops)
    got_nodes, got_parent, got_refs = live_by_name(db)
    assert got_nodes == {qn: (n["type"], n["description"], n["owner"], sorted(set(n["tags"])))
                         for qn, n in want.nodes.items()}
    assert got_parent == want.parent
    assert got_refs == want.refs


def test_seed42_bookkeeping(fixture42):
    db, _, _ = build(fixture42[0], "b.db")
    con = connect(db)
    for table, removed, col in (("nodes", "removed_nodes", "node_id"), ("edges", "removed_edges", "edge_id")):
        # every removal points at a row flagged by that op ...
        assert con.execute(f"SELECT count(*) FROM {removed} r JOIN {table} x ON x.id = r.{col} "
                           f"WHERE x.deleted_by IS NOT r.op_id").fetchone()[0] == 0
        # ... and every flagged row has a removal for its op
        assert con.execute(f"SELECT count(*) FROM {table} x WHERE x.deleted_by IS NOT NULL AND NOT EXISTS "
                           f"(SELECT 1 FROM {removed} r WHERE r.{col} = x.id AND r.op_id = x.deleted_by)"
                           ).fetchone()[0] == 0
    assert con.execute("SELECT count(*) FROM live_edges e WHERE e.from_id NOT IN (SELECT id FROM live_nodes) "
                       "OR e.to_id NOT IN (SELECT id FROM live_nodes)").fetchone()[0] == 0
    # op-0001 is the big delete
    assert con.execute("SELECT count(*) FROM removed_nodes WHERE op_id='op-0001'").fetchone()[0] == 614
    assert con.execute("SELECT count(*) FROM removed_edges WHERE op_id='op-0001'").fetchone()[0] == 774


def test_seed42_rerun(fixture42):
    d = fixture42[0]
    db, _, _ = build(d, "c.db")
    before = snapshot(db)
    summary = apply_file(d / "ops.jsonl", db, lambda *a: pytest.fail(f"rejected on rerun: {a}"))
    assert (summary.applied, summary.noop, summary.skipped, summary.rejected) == (0, 0, 309, 0)
    assert snapshot(db) == before


def test_seed42_deterministic(fixture42):
    d = fixture42[0]
    assert snapshot(build(d, "d1.db")[0]) == snapshot(build(d, "d2.db")[0])


def test_bad_op_mid_file(fixture42, tmp_path):
    d, _, _, ops = fixture42
    lines = [json.dumps(o) for o in ops[:20]]
    lines.insert(5, json.dumps({"op_id": "op-9001", "actor": {"kind": "human", "name": "ann"},
                                "op": "edit_node", "node": "no/such/node", "set": {"owner": "x"}}))
    lines.insert(10, "{not json")
    path = tmp_path / "ops.jsonl"
    path.write_text("\n".join(lines) + "\n")
    db = tmp_path / "g.db"
    load_seed(d / "seed.jsonl", db)
    rejects = []
    summary = apply_file(path, db, lambda line, e: rejects.append((line, str(e))))
    assert summary.rejected == 2
    assert summary.applied + summary.noop == 20          # every good op still applied
    assert [line for line, _ in rejects] == [6, 11]
    assert rejects[0][1] == "op-9001 node_not_found: node 'no/such/node' is not a live node"
