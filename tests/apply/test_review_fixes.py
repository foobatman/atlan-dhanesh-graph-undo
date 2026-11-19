"""Regression tests for findings of the final apply review."""

import json

import pytest

from apply.conftest import BASE, load_store, run, snapshot
from graph_undo.api.apply import ApplyError, apply_file
from graph_undo.api.load import load_seed
from test_layers import SQL


def write_seed(path):
    path.write_text("".join(json.dumps(r) + "\n" for r in BASE))
    return path


def test_deeply_nested_line_is_rejected_not_fatal(tmp_path):
    db = tmp_path / "g.db"
    load_seed(write_seed(tmp_path / "s.jsonl"), db)
    good = {"op_id": "op-0002", "actor": {"kind": "human", "name": "ann"},
            "op": "edit_node", "node": "conn", "set": {"owner": "zed"}}
    ops = tmp_path / "ops.jsonl"
    ops.write_text("[" * 100_000 + "\n" + json.dumps(good) + "\n")
    rejects = []
    summary = apply_file(ops, db, lambda line, e: rejects.append((line, e.code)))
    assert rejects == [(1, "malformed")]
    assert summary.applied == 1


def test_non_sqlite_db_is_apply_error(tmp_path):
    db = tmp_path / "g.db"
    db.write_bytes(b"this is not a database" * 100)
    ops = tmp_path / "ops.jsonl"
    ops.write_text("")
    with pytest.raises(ApplyError):
        apply_file(ops, db)


def test_sql_detector_is_case_insensitive():
    assert SQL.search("select id from nodes")
    assert SQL.search("delete from edges")


@pytest.mark.parametrize("repo", ["edges", "nodes"])
def test_delete_node_count_mismatch_raises_even_without_asserts(store, monkeypatch, repo):
    before = snapshot(store)
    target = getattr(store, repo)
    real = target.mark_deleted
    monkeypatch.setattr(target, "mark_deleted", lambda ids, op_id: real(ids, op_id) - 1)
    with pytest.raises(RuntimeError, match="bug"):
        run(store, op="delete_node", node="conn/db/sc/t")
    assert snapshot(store) == before


def test_delete_edge_count_mismatch_raises_even_without_asserts(store, monkeypatch):
    before = snapshot(store)
    monkeypatch.setattr(store.edges, "mark_deleted", lambda ids, op_id: 0)
    with pytest.raises(RuntimeError, match="bug"):
        run(store, op="delete_edge", **{"from": "conn/db/sc/t", "to": "conn/db/sc/u", "type": "references"})
    assert snapshot(store) == before
