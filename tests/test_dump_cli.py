import json
import subprocess

import pytest

from apply.conftest import BASE
from graph_undo.api.dump import dump_lines
from graph_undo.api.load import load_seed
from graph_undo.cli.dump import main


@pytest.fixture
def db(tmp_path):
    seed = tmp_path / "s.jsonl"
    seed.write_text("".join(json.dumps(r) + "\n" for r in BASE))
    path = tmp_path / "g.db"
    load_seed(seed, path)
    return path


def test_dump_main_ok(db, capsys):
    assert main(["--db", str(db)]) == 0
    out = capsys.readouterr()
    assert out.out == "".join(dump_lines(db))
    assert out.err == ""


def test_dump_main_error(tmp_path, capsys):
    assert main(["--db", str(tmp_path / "nope.db")]) == 1
    out = capsys.readouterr()
    assert out.out == "" and out.err != ""


def test_default_db(db, monkeypatch, capsys):
    monkeypatch.chdir(db.parent)
    db.rename(db.parent / "graph.db")
    assert main([]) == 0
    assert capsys.readouterr().out.startswith('{"description"')


def test_entry_point(db):
    r = subprocess.run(["uv", "run", "dump", "--db", str(db)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert r.stdout == "".join(dump_lines(db))


def test_closed_pipe_is_quiet(tmp_path):
    from graph_undo.fixture.seed import generate_seed, serialize
    seed = tmp_path / "big.jsonl"
    seed.write_text(serialize(*generate_seed(42)))
    db = tmp_path / "big.db"
    load_seed(seed, db)
    r = subprocess.run(f"uv run dump --db '{db}' | head -1", shell=True, capture_output=True, text=True)
    assert r.stdout.startswith('{"description"')
    assert "Traceback" not in r.stderr, r.stderr
