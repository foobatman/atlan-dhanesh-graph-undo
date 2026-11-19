"""In-house schema migration runner (spec: apply-foundation, "Schema migrations").

Migrations are `migrations/NNNN_<name>.sql`: 4-digit, gap-free, ascending, never edited once committed.
`PRAGMA user_version` holds the last migration applied; only this runner sets it.
"""

from __future__ import annotations

import re
import sqlite3
from importlib.resources import files
from importlib.resources.abc import Traversable

_NAME = re.compile(r"([0-9]{4})_[A-Za-z0-9_]+\.sql")


class MigrationError(Exception):
    def __init__(self, number: int, message: str):
        super().__init__(f"migration {number}: {message}")
        self.number = number
        self.message = message


def _packaged() -> Traversable:
    return files("graph_undo.db").joinpath("migrations")


def _scan(source: Traversable | None) -> list[tuple[int, Traversable]]:
    """The migration files of `source`, checked to be numbered 1..N without gaps or duplicates."""
    src = _packaged() if source is None else source
    found: dict[int, Traversable] = {}
    for entry in src.iterdir():
        name = entry.name
        if not name.endswith(".sql"):
            continue
        m = _NAME.fullmatch(name)
        if not m:
            raise MigrationError(0, f"bad migration file name {name!r}; want NNNN_<name>.sql")
        n = int(m.group(1))
        if n in found:
            raise MigrationError(n, f"duplicate migration number: {found[n].name}, {name}")
        found[n] = entry
    numbers = sorted(found)
    for want, got in enumerate(numbers, start=1):
        if want != got:
            raise MigrationError(want, f"migration {want:04d} is missing (found {got:04d})")
    return [(n, found[n]) for n in numbers]


LATEST = len(_scan(None))   # import fails loudly if the packaged files have a gap or a duplicate


def _version(con: sqlite3.Connection) -> int:
    return con.execute("PRAGMA user_version").fetchone()[0]


def _todo(con: sqlite3.Connection, source: Traversable | None) -> list[tuple[int, Traversable]]:
    migs = _scan(source)
    latest = len(migs)
    v = _version(con)
    if v > latest:
        raise MigrationError(v, f"database is newer than this code (version {v}, latest {latest})")
    return migs[v:]


def pending(con: sqlite3.Connection, source: Traversable | None = None) -> list[int]:
    """Migration numbers not yet applied to `con`."""
    return [n for n, _ in _todo(con, source)]


def migrate(con: sqlite3.Connection, source: Traversable | None = None) -> list[int]:
    """Apply every pending migration in order, each in its own transaction. Returns the numbers applied."""
    applied = []
    for n, entry in _todo(con, source):
        sql = entry.read_text(encoding="utf-8")
        try:
            con.executescript(f"BEGIN IMMEDIATE;\n{sql}\n;\nPRAGMA user_version = {n};\nCOMMIT;")
        except sqlite3.Error as e:
            if con.in_transaction:
                con.execute("ROLLBACK")
            raise MigrationError(n, str(e)) from e
        applied.append(n)
    return applied
