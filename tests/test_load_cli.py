import subprocess

import pytest

from graph_undo.cli.load import main as load_main
from graph_undo.fixture.seed import generate_seed, serialize


@pytest.fixture(scope="module")
def seed(tmp_path_factory):
    p = tmp_path_factory.mktemp("seed") / "seed.jsonl"
    p.write_text(serialize(*generate_seed(42)))
    return p


def test_load_main_ok(seed, tmp_path, capsys):
    db = tmp_path / "g.db"
    assert load_main([str(seed), "--db", str(db)]) == 0
    assert capsys.readouterr().err == f"loaded {db}: nodes=2503 edges=2801\n"
    assert db.exists()


def test_load_main_error(tmp_path, capsys):
    bad = tmp_path / "bad.jsonl"
    bad.write_text("{not json\n")
    assert load_main([str(bad), "--db", str(tmp_path / "g.db")]) == 1
    assert capsys.readouterr().err.startswith(f"{bad}:1: ")
    assert not (tmp_path / "g.db").exists()


def test_missing_seed_file(tmp_path, capsys):
    missing = tmp_path / "nope.jsonl"
    assert load_main([str(missing), "--db", str(tmp_path / "g.db")]) == 1
    assert capsys.readouterr().err.startswith(f"{missing}:0: ")


def test_default_db(seed, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert load_main([str(seed)]) == 0
    assert (tmp_path / "graph.db").exists()
    assert capsys.readouterr().err.startswith("loaded graph.db: ")


def test_entry_point(seed, tmp_path):
    db = tmp_path / "g.db"
    r = subprocess.run(["uv", "run", "load", str(seed), "--db", str(db)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert db.exists()
