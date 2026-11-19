"""Schema migration entry point for the CLI (the runner itself lives in db/migrate.py)."""

from __future__ import annotations

from pathlib import Path

from graph_undo.db.connection import connect
from graph_undo.db.migrate import LATEST, MigrationError, migrate, pending

__all__ = ["LATEST", "MigrationError", "migrate_db", "pending_for"]


def migrate_db(db_path: str | Path) -> tuple[int, int]:
    """Apply pending migrations to an existing db. Returns (version before, version after)."""
    con = connect(db_path)
    try:
        before = con.execute("PRAGMA user_version").fetchone()[0]
        applied = migrate(con)
        return before, (applied[-1] if applied else before)
    finally:
        con.close()


def pending_for(db_path: str | Path) -> list[int]:
    con = connect(db_path)
    try:
        return pending(con)
    finally:
        con.close()
