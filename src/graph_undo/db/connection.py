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


SQLITE_READONLY_ROLLBACK = 776   # a hot journal exists and a read-only connection cannot roll it back


def connect_readonly(path: str | Path) -> sqlite3.Connection:
    """Open an existing db for reading only. Nothing is written by the caller and no file is created.

    Opened with mode=ro. If a writer crashed mid-transaction (hot journal), a read-only connection cannot
    read at all; then the db is reopened normally so SQLite's own crash recovery rolls the journal back to
    the last committed state. That recovery is the only write that can happen, and it changes no data.
    """
    uri = f"{Path(path).resolve().as_uri()}?mode=ro"
    con = sqlite3.connect(uri, uri=True, isolation_level=None)
    try:
        con.execute("PRAGMA user_version").fetchone()
    except sqlite3.OperationalError as e:
        if getattr(e, "sqlite_errorcode", None) != SQLITE_READONLY_ROLLBACK:
            con.close()
            raise
        con.close()
        con = sqlite3.connect(Path(path), isolation_level=None)
        con.execute("PRAGMA user_version").fetchone()      # triggers the hot-journal rollback
    except BaseException:
        con.close()
        raise
    return con
