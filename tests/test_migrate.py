"""In-house migration runner (spec: apply-foundation, "Schema migrations")."""

from importlib.resources import files

import pytest

from graph_undo.db.connection import connect
from graph_undo.db.migrate import LATEST, MigrationError, migrate, pending

PACKAGED = files("graph_undo.db").joinpath("migrations")


def version(con):
    return con.execute("PRAGMA user_version").fetchone()[0]


def tables(con):
    return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")}


def mig_dir(tmp_path, names, contents=None):
    """A temp migrations folder holding copies of the packaged files named in `names` (or given contents)."""
    d = tmp_path / "migs"
    d.mkdir(exist_ok=True)
    for n in names:
        if contents and n in contents:
            (d / n).write_text(contents[n])
        else:
            (d / n).write_text(PACKAGED.joinpath(n).read_text(encoding="utf-8"))
    return d


def packaged_names():
    return sorted(p.name for p in PACKAGED.iterdir() if p.name.endswith(".sql"))


def test_fresh_db_runs_all(tmp_path):
    con = connect(tmp_path / "g.db")
    assert migrate(con) == list(range(1, LATEST + 1))
    assert version(con) == LATEST
    assert {"nodes", "edges", "live_nodes", "live_edges", "effective_tags"} <= tables(con)


def test_idempotent(tmp_path):
    con = connect(tmp_path / "g.db")
    migrate(con)
    assert migrate(con) == []
    assert pending(con) == []


def test_pending(tmp_path):
    con = connect(tmp_path / "g.db")
    first = packaged_names()[0]
    assert migrate(con, source=mig_dir(tmp_path, [first])) == [1]
    assert pending(con) == list(range(2, LATEST + 1))


def test_pending_fresh(tmp_path):
    assert pending(connect(tmp_path / "g.db")) == list(range(1, LATEST + 1))


def test_failed_migration_rolls_back(tmp_path):
    first = packaged_names()[0]
    src = mig_dir(tmp_path, [first, "0002_broken.sql"],
                  {"0002_broken.sql": "CREATE TABLE half (x INTEGER);\nTHIS IS NOT SQL;\n"})
    con = connect(tmp_path / "g.db")
    with pytest.raises(MigrationError) as ei:
        migrate(con, source=src)
    assert ei.value.number == 2
    assert version(con) == 1
    assert "half" not in tables(con)
    assert "nodes" in tables(con)
    assert not con.in_transaction


def test_newer_db_refused(tmp_path):
    con = connect(tmp_path / "g.db")
    con.execute("PRAGMA user_version = 99")
    with pytest.raises(MigrationError, match="newer"):
        migrate(con)
    with pytest.raises(MigrationError, match="newer"):
        pending(con)


@pytest.mark.parametrize("names", [["0001_a.sql", "0003_c.sql"],
                                   ["0001_a.sql", "0002_b.sql", "0002_c.sql"],
                                   ["0002_b.sql"],
                                   ["001_a.sql"]])
def test_gap_or_duplicate_rejected(tmp_path, names):
    src = mig_dir(tmp_path, names, {n: "SELECT 1;" for n in names})
    con = connect(tmp_path / "g.db")
    with pytest.raises(MigrationError):
        migrate(con, source=src)
    assert version(con) == 0


def test_migration_files_do_not_set_user_version():
    for n in packaged_names():
        assert "user_version" not in PACKAGED.joinpath(n).read_text(encoding="utf-8").lower(), n


def test_latest_matches_files():
    assert LATEST == len(packaged_names())
    assert packaged_names()[0].startswith("0001_")


def _db_at_version_1(tmp_path):
    db = tmp_path / "g.db"
    con = connect(db)
    migrate(con, source=mig_dir(tmp_path, [packaged_names()[0]]))
    con.close()
    return db


@pytest.mark.skipif(LATEST < 2, reason="needs migration 0002 (Task 3)")
def test_migrate_cli(tmp_path, capsys, monkeypatch):
    from graph_undo.cli.migrate import main

    _db_at_version_1(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert main(["--db", "g.db"]) == 0
    assert capsys.readouterr().err == f"migrated g.db: 1 -> {LATEST}\n"
    assert main(["--db", "g.db"]) == 0
    assert capsys.readouterr().err == f"g.db is up to date ({LATEST})\n"


def test_migrate_cli_missing_db(tmp_path, capsys):
    from graph_undo.cli.migrate import main

    db = tmp_path / "nope.db"
    assert main(["--db", str(db)]) == 1
    assert "uv run load" in capsys.readouterr().err
    assert not db.exists()


def test_migrate_cli_newer_db(tmp_path, capsys):
    from graph_undo.cli.migrate import main

    db = tmp_path / "g.db"
    connect(db).execute("PRAGMA user_version = 99")
    assert main(["--db", str(db)]) == 1
    assert "newer" in capsys.readouterr().err


@pytest.mark.skipif(LATEST < 2, reason="needs migration 0002 (Task 3)")
def test_migrate_entry_point(tmp_path):
    import subprocess

    db = _db_at_version_1(tmp_path)
    r = subprocess.run(["uv", "run", "migrate", "--db", str(db)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert r.stderr.endswith(f"1 -> {LATEST}\n")
