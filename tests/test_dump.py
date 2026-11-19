import json
import os
import sqlite3

import pytest

from apply.conftest import BASE, edge, load_store, node, run
from graph_undo.api.apply import apply_file
from graph_undo.api.dump import DumpError, dump_lines
from graph_undo.api.load import load_seed
from graph_undo.fixture.ops import generate_ops
from graph_undo.fixture.seed import generate_seed, serialize
from replay import replay


def line(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"


def write(path, rows):
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    return path


def ancestor_tags(nodes, edges):
    """Independent effective_tags: own tags plus every ancestor's along contains."""
    by_id = {n["id"]: n for n in nodes}
    parent = {e["to_id"]: e["from_id"] for e in edges if e["type"] == "contains"}
    out = {}
    for i in by_id:
        tags, cur = set(), i
        while cur is not None:
            tags |= set(by_id[cur]["tags"])
            cur = parent.get(cur)
        out[i] = sorted(tags)
    return out


def parse(lines):
    rows = [json.loads(x) for x in lines]
    return [r for r in rows if "from_id" not in r], [r for r in rows if "from_id" in r]


@pytest.fixture
def base_db(tmp_path):
    db = tmp_path / "g.db"
    load_seed(write(tmp_path / "s.jsonl", BASE), db)
    return db


def test_after_load_equals_seed(base_db):
    nodes = [r for r in BASE if "from_id" not in r]
    edges = [r for r in BASE if "from_id" in r]
    eff = ancestor_tags(nodes, edges)
    want = ([line({**n, "tags": sorted(set(n["tags"])), "effective_tags": eff[n["id"]]})
             for n in sorted(nodes, key=lambda n: n["qualified_name"])]
            + [line(e) for e in sorted(edges, key=lambda e: (e["from_id"], e["to_id"], e["type"]))])
    assert dump_lines(base_db) == want


def test_after_apply_equals_replay(tmp_path):
    nodes, edges = generate_seed(42)
    ops, _ = generate_ops(42, nodes, edges)
    (tmp_path / "seed.jsonl").write_text(serialize(nodes, edges))
    (tmp_path / "ops.jsonl").write_text(serialize(ops, []))
    db = tmp_path / "g.db"
    load_seed(tmp_path / "seed.jsonl", db)
    assert apply_file(tmp_path / "ops.jsonl", db).rejected == 0
    got_n, got_e = parse(dump_lines(db))
    want = replay(nodes, edges, ops)
    assert {n["qualified_name"]: (n["type"], n["description"], n["owner"], n["tags"]) for n in got_n} == \
        {qn: (n["type"], n["description"], n["owner"], sorted(set(n["tags"]))) for qn, n in want.nodes.items()}
    name = {n["id"]: n["qualified_name"] for n in got_n}
    assert {name[e["to_id"]]: name[e["from_id"]] for e in got_e if e["type"] == "contains"} == want.parent
    assert {(name[e["from_id"]], name[e["to_id"]]) for e in got_e if e["type"] == "references"} == want.refs
    eff = ancestor_tags(got_n, got_e)
    assert all(n["effective_tags"] == eff[n["id"]] for n in got_n)


def test_sorted_and_deterministic(base_db):
    out = dump_lines(base_db)
    got_n, got_e = parse(out)
    assert [n["qualified_name"] for n in got_n] == sorted(n["qualified_name"] for n in got_n)
    assert [(e["from_id"], e["to_id"], e["type"]) for e in got_e] == \
        sorted((e["from_id"], e["to_id"], e["type"]) for e in got_e)
    assert out[: len(got_n)] == [line(n) for n in got_n]   # nodes first
    assert dump_lines(base_db) == out


def test_dump_is_read_only(base_db):
    before, mtime = base_db.read_bytes(), os.stat(base_db).st_mtime_ns
    dump_lines(base_db)
    assert base_db.read_bytes() == before
    assert os.stat(base_db).st_mtime_ns == mtime
    for side in ("-journal", "-wal", "-shm"):
        assert not base_db.with_name(base_db.name + side).exists()


def test_no_dead_rows_no_dangling(tmp_path):
    store = load_store(tmp_path)
    run(store, op="delete_node", node="conn/db/sc/t")
    store.close()
    got_n, got_e = parse(dump_lines(tmp_path / "g.db"))
    ids = {n["id"] for n in got_n}
    assert ids == {"c", "d", "s", "u"}
    assert all(e["from_id"] in ids and e["to_id"] in ids for e in got_e)
    assert len(got_e) == 3     # c>d, d>s, s>u


def test_non_ascii_sort_and_encoding(tmp_path):
    db = tmp_path / "g.db"
    load_seed(write(tmp_path / "s.jsonl", [node("z", "conn/z"), node("e", "conn/é")]), db)
    out = dump_lines(db)
    names = [json.loads(x)["qualified_name"] for x in out]
    assert names == sorted(names) == ["conn/z", "conn/é"]
    assert "é" in out[1]


def test_empty_graph(tmp_path):
    db = tmp_path / "g.db"
    (tmp_path / "s.jsonl").write_text("")
    load_seed(tmp_path / "s.jsonl", db)
    assert dump_lines(db) == []


def _set_version(db, v):
    con = sqlite3.connect(db)
    con.execute(f"PRAGMA user_version = {v}")
    con.commit()
    con.close()


@pytest.mark.parametrize("case", ["missing", "garbage", "pending", "newer"])
def test_errors(tmp_path, base_db, case):
    db = base_db
    if case == "missing":
        db = tmp_path / "nope.db"
    elif case == "garbage":
        db = tmp_path / "junk.db"
        db.write_bytes(b"not sqlite" * 100)
    elif case == "pending":
        _set_version(db, 1)
    else:
        _set_version(db, 99)
    with pytest.raises(DumpError):
        dump_lines(db)
    if case == "pending":
        assert sqlite3.connect(db).execute("PRAGMA user_version").fetchone()[0] == 1
