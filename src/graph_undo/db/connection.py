"""SQLite connection setup and explicit transactions. The only module that opens a database."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

# Re-exported so upper layers can catch database failures without importing sqlite3.
DbError = sqlite3.Error
IntegrityError = sqlite3.IntegrityError


def connect(path: str | Path) -> sqlite3.Connection:
    """Open `path` with foreign keys on and autocommit mode: every transaction is explicit."""
    con = sqlite3.connect(path, isolation_level=None)
    con.execute("PRAGMA foreign_keys = ON")
    return con


@contextmanager
def transaction(con: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """BEGIN IMMEDIATE ... COMMIT; ROLLBACK on any exception (including KeyboardInterrupt)."""
    con.execute("BEGIN IMMEDIATE")
    try:
        yield con
    except BaseException:
        if con.in_transaction:
            con.execute("ROLLBACK")
        raise
    con.execute("COMMIT")
