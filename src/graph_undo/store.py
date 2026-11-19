"""SQLite graph store: connection setup and seed loading (spec: docs/superpowers/specs/2026-11-19-load-design.md)."""

from __future__ import annotations

import json
import os
import sqlite3
from importlib.resources import files
from pathlib import Path

NODE_KEYS = {"id", "qualified_name", "type", "description", "owner", "tags"}
EDGE_KEYS = {"from_id", "to_id", "type"}
NODE_TYPES = ("connection", "database", "schema", "table", "column")
EDGE_TYPES = ("contains", "references")
LEVEL = {t: i for i, t in enumerate(NODE_TYPES)}


class LoadError(Exception):
    def __init__(self, line: int, message: str):
        super().__init__(f"{line}: {message}")
        self.line = line
        self.message = message


def connect(path: str | Path) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.execute("PRAGMA foreign_keys = ON")
    return con


def _parse(seed_path: Path) -> tuple[list[tuple[int, dict]], list[tuple[int, dict]]]:
    try:
        text = seed_path.read_text(encoding="utf-8")
    except OSError as e:
        raise LoadError(0, f"cannot read seed: {e}") from e
    nodes, edges = [], []
    for no, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError as e:
            raise LoadError(no, f"invalid JSON: {e.msg}") from e
        if not isinstance(row, dict):
            raise LoadError(no, "line is not a JSON object")
        is_edge = "from_id" in row
        want, kinds = (EDGE_KEYS, EDGE_TYPES) if is_edge else (NODE_KEYS, NODE_TYPES)
        if set(row) != want:
            missing, extra = sorted(want - set(row)), sorted(set(row) - want)
            raise LoadError(no, f"{'edge' if is_edge else 'node'} keys: missing {missing}, unexpected {extra}")
        for k, v in row.items():
            if k == "tags":
                if not isinstance(v, list) or not all(isinstance(t, str) for t in v):
                    raise LoadError(no, "tags must be a list of strings")
            elif not isinstance(v, str):
                raise LoadError(no, f"{k} must be a string")
        if row["type"] not in kinds:
            raise LoadError(no, f"unknown {'edge' if is_edge else 'node'} type {row['type']!r}")
        (edges if is_edge else nodes).append((no, row))
    return nodes, edges


def _insert(con: sqlite3.Connection, nodes, edges) -> None:
    types: dict[str, str] = {}
    for no, n in nodes:
        try:
            con.execute("INSERT INTO nodes (id, qualified_name, type, description, owner, tags) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (n["id"], n["qualified_name"], n["type"], n["description"], n["owner"],
                         json.dumps(sorted(set(n["tags"])), separators=(",", ":"))))
        except sqlite3.IntegrityError as e:
            what = "id" if "nodes.id" in str(e) else "qualified_name"
            raise LoadError(no, f"duplicate {what}: {e}") from e
        types[n["id"]] = n["type"]
    seen: set[tuple[str, str, str]] = set()
    parent: dict[str, str] = {}
    for no, e in edges:
        f, t = e["from_id"], e["to_id"]
        for end in (f, t):
            if end not in types:
                raise LoadError(no, f"edge endpoint {end!r} is not a node")
        if f == t:
            raise LoadError(no, "self-loop")
        key = (f, t, e["type"])
        if key in seen:
            raise LoadError(no, f"duplicate edge {f} -> {t} ({e['type']})")
        if e["type"] == "contains":
            if LEVEL[types[t]] != LEVEL[types[f]] + 1:
                raise LoadError(no, f"contains {types[f]} -> {types[t]} breaks type order")
            if t in parent:
                raise LoadError(no, f"{t!r} already has contains parent {parent[t]!r}")
            parent[t] = f
        seen.add(key)
        # the schema's constraints back these checks up; any violation they catch still names the line
        try:
            con.execute("INSERT INTO edges (from_id, to_id, type) VALUES (?, ?, ?)", key)
        except sqlite3.IntegrityError as err:
            raise LoadError(no, f"constraint violation: {err}") from err


def load_seed(seed_path: str | Path, db_path: str | Path) -> tuple[int, int]:
    """Create db_path from seed_path, replacing any existing file. All or nothing."""
    seed_path, db_path = Path(seed_path), Path(db_path)
    nodes, edges = _parse(seed_path)
    tmp = db_path.with_name(db_path.name + ".tmp")
    tmp.unlink(missing_ok=True)
    try:
        con = connect(tmp)
        try:
            con.executescript(files("graph_undo").joinpath("schema.sql").read_text(encoding="utf-8"))
            with con:
                _insert(con, nodes, edges)
        finally:
            con.close()
        os.replace(tmp, db_path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return len(nodes), len(edges)
