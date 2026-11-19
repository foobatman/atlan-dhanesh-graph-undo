"""apply_file: stream ops.jsonl through parse_op and apply_op, printing-and-skipping invalid ops."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from graph_undo.api.registry import apply_op
from graph_undo.db.migrate import MigrationError, pending
from graph_undo.db.store import Store
from graph_undo.domain.errors import MalformedOp, OpError
from graph_undo.domain.ops import parse_op


class ApplyError(Exception):
    """The apply as a whole cannot run (no op was attempted)."""


@dataclass
class ApplySummary:
    applied: int = 0
    noop: int = 0
    skipped: int = 0
    rejected: int = 0
    rejections: list[tuple[int, OpError]] = field(default_factory=list)


def _no_duplicate_keys(pairs: list[tuple[str, object]]) -> dict:
    keys = [k for k, _ in pairs]
    dupes = sorted({k for k in keys if keys.count(k) > 1})
    if dupes:
        raise ValueError(f"duplicate keys {dupes}")
    return dict(pairs)


def _decode(raw: bytes) -> object:
    """One line -> JSON value, or MalformedOp (no op_id: the line could not be read as an op)."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise MalformedOp(None, "line is not valid UTF-8") from None
    try:
        return json.loads(text, object_pairs_hook=_no_duplicate_keys)
    except json.JSONDecodeError as e:
        raise MalformedOp(None, f"invalid JSON: {e.msg}") from None
    except ValueError as e:
        raise MalformedOp(None, str(e)) from None


def _open_store(db_path: Path) -> Store:
    if not db_path.is_file():
        raise ApplyError(f"no database at {db_path}; run: uv run load <seed.jsonl> --db {db_path}")
    store = Store.open(db_path)
    try:
        todo = pending(store.con)
    except MigrationError as e:
        store.close()
        raise ApplyError(f"{db_path}: {e.message}") from e
    if todo:
        store.close()
        raise ApplyError(f"database needs migrations {todo}; run: uv run migrate --db {db_path}")
    return store


def apply_file(ops_path: str | Path, db_path: str | Path,
               on_reject: Callable[[int, OpError], None] | None = None) -> ApplySummary:
    """Apply every op of ops_path to db_path in order; each op commits on its own.

    An invalid op (OpError) is passed to on_reject(line, error), rolled back and skipped. Line numbers count
    '\\n' only; blank lines are ignored. Raises ApplyError if the apply cannot run at all.
    """
    ops_path, db_path = Path(ops_path), Path(db_path)
    try:
        data = ops_path.read_bytes()
    except OSError as e:
        raise ApplyError(f"cannot read {ops_path}: {e.strerror or e}") from e
    store = _open_store(db_path)
    summary = ApplySummary()
    try:
        for no, raw in enumerate(data.split(b"\n"), start=1):
            if not raw.strip():
                continue
            try:
                result = apply_op(store, parse_op(_decode(raw)))
            except OpError as e:
                summary.rejected += 1
                summary.rejections.append((no, e))
                if on_reject is not None:
                    on_reject(no, e)
                continue
            if result.effect == "applied":
                summary.applied += 1
            elif result.effect == "noop":
                summary.noop += 1
            else:
                summary.skipped += 1
    finally:
        store.close()
    return summary
