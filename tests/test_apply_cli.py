"""`uv run apply` CLI (spec: apply-foundation, "cli/apply.py")."""

import json
import subprocess

import pytest

from apply.conftest import BASE, load_store, op_dict
from graph_undo.api.context import OpResult
from graph_undo.api.registry import HANDLERS, apply_op
from graph_undo.api.resolve import require_live
from graph_undo.cli.apply import main
from graph_undo.db.oplog import LoggedOp
from graph_undo.domain.errors import OpError
from graph_undo.domain.ops import parse_op


def fake_edit(ctx, op):
    n = require_live(ctx, "node", op.node)
    ctx.store.nodes.update_fields(n.id, op.set)
    ctx.store.log.record(LoggedOp.of(op, "applied", node_id=n.id))
    return OpResult(op.op_id, "applied", node_id=n.id)


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setitem(HANDLERS, "edit_node", fake_edit)
    load_store(tmp_path, BASE).close()
    return tmp_path / "g.db"


def edit(op_id, qn):
    return op_dict(op_id=op_id, op="edit_node", node=qn, set={"owner": "x"})


def write(tmp_path, lines):
    p = tmp_path / "ops.jsonl"
    p.write_text("".join((l if isinstance(l, str) else json.dumps(l)) + "\n" for l in lines))
    return p


def test_summary_line(db, tmp_path, capsys):
    ops = write(tmp_path, [edit("op-0001", "conn/db/sc/t")])
    assert main([str(ops), "--db", str(db)]) == 0
    assert capsys.readouterr().err == f"applied {ops} to {db}: applied=1 noop=0 skipped=0 rejected=0\n"
    assert main([str(ops), "--db", str(db)]) == 0
    assert capsys.readouterr().err == f"applied {ops} to {db}: applied=0 noop=0 skipped=1 rejected=0\n"


def test_rejections_exit_0_and_print_each(db, tmp_path, capsys):
    bad = edit("op-0002", "conn/nope")
    ingest = {**edit("op-0004", "conn/db/sc/t"), "actor": {"kind": "ingest", "name": "crawler"}}
    ops = write(tmp_path, [edit("op-0001", "conn/db/sc/t"), bad, edit("op-0003", "conn/db/sc/u"), ingest])
    assert main([str(ops), "--db", str(db)]) == 0
    err = capsys.readouterr().err.splitlines()
    # the reject line is f"{path}:{line}: {e}", e being what apply_op raises for that op on its own
    from graph_undo.db.store import Store

    s = Store.open(db)
    with pytest.raises(OpError) as e2:
        apply_op(s, parse_op(bad))
    s.close()
    with pytest.raises(OpError) as e4:
        parse_op(ingest)
    assert err == [f"{ops}:2: {e2.value}",
                   f"{ops}:4: {e4.value}",
                   f"applied {ops} to {db}: applied=2 noop=0 skipped=0 rejected=2"]
    assert err[0].endswith("op-0002 node_not_found: node 'conn/nope' is not a live node")
    assert err[1].endswith("op-0004 actor_not_allowed: ingest may not edit_node")


def test_missing_ops_file(db, tmp_path, capsys):
    assert main([str(tmp_path / "nope.jsonl"), "--db", str(db)]) == 1
    assert "nope.jsonl" in capsys.readouterr().err


def test_missing_db(tmp_path, capsys):
    ops = write(tmp_path, [edit("op-0001", "conn")])
    assert main([str(ops), "--db", str(tmp_path / "nope.db")]) == 1
    assert "uv run load" in capsys.readouterr().err
    assert not (tmp_path / "nope.db").exists()


def test_pending_migrations(db, tmp_path, capsys):
    from graph_undo.db.connection import connect

    connect(db).execute("PRAGMA user_version = 1")
    ops = write(tmp_path, [edit("op-0001", "conn/db/sc/t")])
    assert main([str(ops), "--db", str(db)]) == 1
    assert capsys.readouterr().err == f"database needs migrations [2]; run: uv run migrate --db {db}\n"
    assert connect(db).execute("SELECT owner FROM nodes WHERE id='t'").fetchone()[0] == "o"   # no op attempted


def test_newer_db(db, tmp_path, capsys):
    from graph_undo.db.connection import connect

    connect(db).execute("PRAGMA user_version = 99")
    ops = write(tmp_path, [edit("op-0001", "conn/db/sc/t")])
    assert main([str(ops), "--db", str(db)]) == 1
    assert "newer" in capsys.readouterr().err


def test_default_db(db, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    db.rename(tmp_path / "graph.db")
    ops = write(tmp_path, [edit("op-0001", "conn/db/sc/t")])
    assert main([str(ops)]) == 0
    assert capsys.readouterr().err.endswith(" to graph.db: applied=1 noop=0 skipped=0 rejected=0\n")


def test_entry_point(tmp_path):
    # rejections only (parse-level), so no handler runs: works while the per-op handlers are stubs
    load_store(tmp_path, BASE).close()
    db = tmp_path / "g.db"
    ingest_edit = {**edit("op-0002", "conn"), "actor": {"kind": "ingest", "name": "crawler"}}
    ops = write(tmp_path, ["{oops", ingest_edit])
    r = subprocess.run(["uv", "run", "apply", str(ops), "--db", str(db)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert r.stderr.splitlines() == [
        f"{ops}:1: malformed: invalid JSON: Expecting property name enclosed in double quotes",
        f"{ops}:2: op-0002 actor_not_allowed: ingest may not edit_node",
        f"applied {ops} to {db}: applied=0 noop=0 skipped=0 rejected=2",
    ]
