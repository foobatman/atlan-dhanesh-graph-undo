import json
import sqlite3

import pytest

from graph_undo.fixture.seed import generate_seed, serialize
from graph_undo.api.load import LoadError, load_seed
from graph_undo.db.connection import connect


@pytest.fixture(scope="module")
def seed42():
    return generate_seed(42)


@pytest.fixture
def loaded(tmp_path, seed42):
    nodes, edges = seed42
    seed = tmp_path / "seed.jsonl"
    seed.write_text(serialize(nodes, edges))
    db = tmp_path / "g.db"
    counts = load_seed(seed, db)
    return counts, connect(db), nodes, edges


def write_lines(path, rows):
    path.write_text("".join((r if isinstance(r, str) else json.dumps(r)) + "\n" for r in rows))
    return path


def node(i, qn, type_="connection", tags=None):
    return {"id": i, "qualified_name": qn, "type": type_, "description": "d", "owner": "o",
            "tags": tags if tags is not None else []}


def edge(f, t, type_="contains"):
    return {"from_id": f, "to_id": t, "type": type_}


# ---- happy path ----

def test_counts(loaded):
    assert loaded[0] == (2503, 2801)


def test_round_trip(loaded):
    _, con, nodes, edges = loaded
    got = [dict(zip(("id", "qualified_name", "type", "description", "owner", "tags"), r))
           for r in con.execute("SELECT id, qualified_name, type, description, owner, tags FROM live_nodes")]
    for g in got:
        g["tags"] = json.loads(g["tags"])
    key = lambda n: n["id"]
    assert sorted(got, key=key) == sorted(nodes, key=key)
    got_e = sorted(con.execute("SELECT from_id, to_id, type FROM live_edges"))
    assert got_e == sorted((e["from_id"], e["to_id"], e["type"]) for e in edges)


def test_all_live(loaded):
    con = loaded[1]
    assert con.execute("SELECT count(*) FROM nodes WHERE deleted_by IS NOT NULL").fetchone()[0] == 0
    assert con.execute("SELECT count(*) FROM edges WHERE deleted_by IS NOT NULL").fetchone()[0] == 0


def test_edge_ids_file_order(loaded):
    _, con, _, edges = loaded
    rows = list(con.execute("SELECT id, from_id, to_id, type FROM edges ORDER BY id"))
    assert [r[0] for r in rows] == list(range(1, len(edges) + 1))
    assert [r[1:] for r in rows] == [(e["from_id"], e["to_id"], e["type"]) for e in edges]


def test_user_version(loaded):
    assert loaded[1].execute("PRAGMA user_version").fetchone()[0] == 1


def test_foreign_keys_on(loaded):
    assert loaded[1].execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_effective_tags(loaded):
    _, con, nodes, edges = loaded
    by_id = {n["id"]: n for n in nodes}
    parent = {e["to_id"]: e["from_id"] for e in edges if e["type"] == "contains"}

    def expected(i):
        tags = set()
        while i is not None:
            tags |= set(by_id[i]["tags"])
            i = parent.get(i)
        return sorted(tags)

    got = {i: json.loads(t) for i, t in con.execute("SELECT node_id, tags FROM effective_tags")}
    assert got == {i: expected(i) for i in by_id}


def test_tags_normalised(tmp_path):
    seed = write_lines(tmp_path / "s.jsonl", [node("a", "c", tags=["pii", "gold", "pii"])])
    load_seed(seed, tmp_path / "g.db")
    con = connect(tmp_path / "g.db")
    assert con.execute("SELECT tags FROM nodes").fetchone()[0] == '["gold","pii"]'


def test_order_independent(tmp_path):
    seed = write_lines(tmp_path / "s.jsonl",
                       [edge("a", "b"), node("a", "c"), node("b", "c/db", "database")])
    assert load_seed(seed, tmp_path / "g.db") == (2, 1)


def test_blank_lines_ignored(tmp_path):
    seed = write_lines(tmp_path / "s.jsonl", [node("a", "c"), ""])
    assert load_seed(seed, tmp_path / "g.db") == (1, 0)


# ---- rejection ----

BASE = [node("c", "conn"), node("d", "conn/db", "database"), node("s", "conn/db/sc", "schema"),
        node("t", "conn/db/sc/t", "table"), node("u", "conn/db/sc/u", "table"),
        node("k", "conn/db/sc/t/k", "column"),
        edge("c", "d"), edge("d", "s"), edge("s", "t"), edge("s", "u"), edge("t", "k")]

BAD = {
    "bad_json": "{not json",
    "missing_key": {k: v for k, v in node("x", "x").items() if k != "owner"},
    "extra_key": {**node("x", "x"), "colour": "red"},
    "tags_not_list": {**node("x", "x"), "tags": "pii"},
    "tag_not_string": {**node("x", "x"), "tags": [1]},
    "non_string_field": {**node("x", "x"), "owner": 7},
    "unknown_node_type": node("x", "x", "view"),
    "unknown_edge_type": edge("t", "u", "likes"),
    "duplicate_id": node("t", "conn/other"),
    "duplicate_name": node("x", "conn/db"),
    "unknown_endpoint": edge("t", "nope", "references"),
    "self_loop": edge("t", "t", "references"),
    "duplicate_edge": edge("s", "t"),
    "second_parent": edge("u", "k"),
    "type_order_same_level": edge("t", "u"),
    "type_order_skip": edge("s", "k"),
    "edge_missing_key": {"from_id": "t", "type": "references"},
}


@pytest.mark.parametrize("case", sorted(BAD))
def test_rejects(tmp_path, case):
    rows = BASE + [BAD[case]]
    seed = write_lines(tmp_path / "s.jsonl", rows)
    db = tmp_path / "g.db"
    with pytest.raises(LoadError) as ei:
        load_seed(seed, db)
    assert ei.value.line == len(rows)
    assert not db.exists()
    assert not (tmp_path / "g.db.tmp").exists()


def test_failed_load_keeps_existing_db(tmp_path):
    db = tmp_path / "g.db"
    load_seed(write_lines(tmp_path / "ok.jsonl", BASE), db)
    before = db.read_bytes()
    with pytest.raises(LoadError):
        load_seed(write_lines(tmp_path / "bad.jsonl", BASE + [BAD["self_loop"]]), db)
    assert db.read_bytes() == before
    assert not (tmp_path / "g.db.tmp").exists()


def test_reload_replaces(tmp_path):
    db = tmp_path / "g.db"
    load_seed(write_lines(tmp_path / "a.jsonl", BASE), db)
    load_seed(write_lines(tmp_path / "b.jsonl", [node("z", "zed")]), db)
    con = connect(db)
    assert list(con.execute("SELECT id FROM nodes")) == [("z",)]
    assert con.execute("SELECT count(*) FROM edges").fetchone()[0] == 0


def test_deterministic(tmp_path, seed42):
    seed = tmp_path / "seed.jsonl"
    seed.write_text(serialize(*seed42))
    dumps = []
    for name in ("a.db", "b.db"):
        load_seed(seed, tmp_path / name)
        con = connect(tmp_path / name)
        dumps.append((list(con.execute("SELECT * FROM nodes ORDER BY id")),
                      list(con.execute("SELECT * FROM edges ORDER BY id"))))
    assert dumps[0] == dumps[1]


def test_stale_tmp_is_replaced(tmp_path):
    db = tmp_path / "g.db"
    (tmp_path / "g.db.tmp").write_bytes(b"garbage")
    assert load_seed(write_lines(tmp_path / "s.jsonl", BASE), db) == (6, 5)
    assert not (tmp_path / "g.db.tmp").exists()


def test_missing_seed_file(tmp_path):
    with pytest.raises(LoadError) as ei:
        load_seed(tmp_path / "nope.jsonl", tmp_path / "g.db")
    assert ei.value.line == 0
    assert not (tmp_path / "g.db").exists()


def test_partial_indexes_ignore_dead_rows(tmp_path):
    db = tmp_path / "g.db"
    load_seed(write_lines(tmp_path / "s.jsonl", BASE), db)
    con = connect(db)
    con.execute("UPDATE nodes SET deleted_by='op-x' WHERE id='k'")
    con.execute("UPDATE edges SET deleted_by='op-x' WHERE to_id='k'")
    # name, edge and parent slot of the dead row are free again
    con.execute("INSERT INTO nodes VALUES ('k2','conn/db/sc/t/k','column','','','[]',NULL)")
    con.execute("INSERT INTO edges (from_id,to_id,type) VALUES ('t','k2','contains')")
    con.execute("UPDATE edges SET deleted_by='op-y' WHERE from_id='s' AND to_id='u'")
    con.execute("INSERT INTO edges (from_id,to_id,type) VALUES ('s','u','contains')")       # same edge again
    con.execute("UPDATE edges SET deleted_by='op-z' WHERE from_id='s' AND to_id='u' AND deleted_by IS NULL")
    con.execute("INSERT INTO edges (from_id,to_id,type) VALUES ('d','u','contains')")       # new parent slot
    assert con.execute("SELECT count(*) FROM live_nodes WHERE qualified_name='conn/db/sc/t/k'").fetchone()[0] == 1
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO nodes VALUES ('k3','conn/db/sc/t/k','column','','','[]',NULL)")
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO edges (from_id,to_id,type) VALUES ('c','u','contains')")   # second live parent


# ---- review fixes ----

def test_unicode_line_separators_preserved(tmp_path):
    text = "a b c\x85d\x0ce"
    seed = tmp_path / "s.jsonl"
    seed.write_text(json.dumps({**node("a", "c"), "description": text}, ensure_ascii=False) + "\n"
                    + json.dumps(BAD["self_loop"]) + "\n", encoding="utf-8")
    with pytest.raises(LoadError) as ei:          # line numbers count only \n
        load_seed(seed, tmp_path / "g.db")
    assert ei.value.line == 2
    seed.write_text(json.dumps({**node("a", "c"), "description": text}, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    load_seed(seed, tmp_path / "g.db")
    assert connect(tmp_path / "g.db").execute("SELECT description FROM nodes").fetchone()[0] == text


def test_db_same_as_seed_refused(tmp_path):
    seed = write_lines(tmp_path / "s.jsonl", BASE)
    before = seed.read_bytes()
    with pytest.raises(LoadError) as ei:
        load_seed(seed, seed)
    assert ei.value.line == 0
    assert seed.read_bytes() == before
    link = tmp_path / "link.db"
    link.symlink_to(seed)
    with pytest.raises(LoadError):
        load_seed(seed, link)
    assert seed.read_bytes() == before


def test_effective_tags_skip_dead_ancestor(tmp_path):
    rows = [node("c", "c", tags=["top"]), node("d", "c/d", "database"), node("s", "c/d/s", "schema"),
            edge("c", "d"), edge("d", "s")]
    db = tmp_path / "g.db"
    load_seed(write_lines(tmp_path / "s.jsonl", rows), db)
    con = connect(db)
    con.execute("UPDATE nodes SET deleted_by='op-x' WHERE id='d'")   # node dead, edges left live
    assert con.execute("SELECT tags FROM effective_tags WHERE node_id='s'").fetchone()[0] == "[]"


def test_missing_db_dir(tmp_path):
    with pytest.raises(LoadError) as ei:
        load_seed(write_lines(tmp_path / "s.jsonl", BASE), tmp_path / "nodir" / "g.db")
    assert ei.value.line == 0


def test_duplicate_json_keys_rejected(tmp_path):
    seed = tmp_path / "s.jsonl"
    seed.write_text('{"id":"a","id":"b","qualified_name":"c","type":"connection",'
                    '"description":"","owner":"","tags":[]}\n')
    with pytest.raises(LoadError) as ei:
        load_seed(seed, tmp_path / "g.db")
    assert ei.value.line == 1


def test_lone_surrogate_rejected(tmp_path):
    seed = tmp_path / "s.jsonl"
    seed.write_text(json.dumps({**node("a", "c"), "owner": "\ud800"}) + "\n")
    with pytest.raises(LoadError) as ei:
        load_seed(seed, tmp_path / "g.db")
    assert ei.value.line == 1
    assert not (tmp_path / "g.db.tmp").exists()


def test_stale_side_files_removed(tmp_path):
    db = tmp_path / "g.db"
    for suffix in ("-journal", "-wal", "-shm"):
        (tmp_path / f"g.db{suffix}").write_bytes(b"stale")
    load_seed(write_lines(tmp_path / "s.jsonl", BASE), db)
    for suffix in ("-journal", "-wal", "-shm"):
        assert not (tmp_path / f"g.db{suffix}").exists()
    assert connect(db).execute("SELECT count(*) FROM nodes").fetchone()[0] == 6
