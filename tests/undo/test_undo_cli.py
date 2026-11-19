"""`uv run undo <op_id> [--db]` (spec: undo-design, "CLI contract")."""

import json

from apply.conftest import load_store, run, snapshot
from graph_undo.cli.undo import main


def setup_db(tmp_path):
    store = load_store(tmp_path)
    run(store, op_id="op-0100", op="delete_node", node="conn/db/sc/u")
    path = store.con.execute("PRAGMA database_list").fetchone()[2]
    store.close()
    return path


def test_cli_success(tmp_path, capsys):
    db = setup_db(tmp_path)
    assert main(["op-0100", "--db", db]) == 0
    out, err = capsys.readouterr()
    objs = [json.loads(line) for line in out.splitlines()]
    assert [(o["kind"], o["id"], o["outcome"]) for o in objs] == [
        ("node", "u", "restored"), ("edge", 4, "restored"), ("edge", 6, "restored")]
    assert out.endswith("\n")
    assert err == f"undid op-0100 on {db}: nodes restored=1 skipped=0; edges restored=2 merged=0 skipped=0\n"


def test_cli_already_undone(tmp_path, capsys):
    db = setup_db(tmp_path)
    main(["op-0100", "--db", db])
    capsys.readouterr()
    assert main(["op-0100", "--db", db]) == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert err.startswith("op-0100 already_undone: already undone (undo #1, after op-0100)")


def test_cli_unknown_op(tmp_path, capsys):
    db = setup_db(tmp_path)
    assert main(["op-0999", "--db", db]) == 1
    assert capsys.readouterr().err == "op-0999 op_not_found: no applied op with id 'op-0999'\n"


def test_cli_pending_migration(tmp_path, capsys):
    store = load_store(tmp_path)
    run(store, op_id="op-0100", op="delete_node", node="conn/db/sc/u")
    store.con.execute("PRAGMA user_version = 2")
    db = store.con.execute("PRAGMA database_list").fetchone()[2]
    before = snapshot(store)
    assert main(["op-0100", "--db", db]) == 1
    assert capsys.readouterr().err == f"database needs migrations [3]; run: uv run migrate --db {db}\n"
    assert snapshot(store) == before
    store.close()


def test_cli_malformed_op_id(tmp_path, capsys):
    db = setup_db(tmp_path)
    for bad in ("op-1", "foo"):
        assert main([bad, "--db", db]) == 1
        assert capsys.readouterr().err == f"{bad} malformed: op_id '{bad}' does not match op-NNNN\n"


def test_cli_missing_db(tmp_path, capsys):
    db = tmp_path / "nope.db"
    assert main(["op-0001", "--db", str(db)]) == 1
    assert capsys.readouterr().err == f"no database at {db}; run: uv run load <seed.jsonl> --db {db}\n"
    assert not db.exists()
