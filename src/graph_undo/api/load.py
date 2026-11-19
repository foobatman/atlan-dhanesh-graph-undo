"""Seed loading: parse and validate seed.jsonl, build the db in a tmp file, swap it in on success.

Spec: docs/superpowers/specs/2026-11-19-load-design.md (behaviour); the code moved here from
graph_undo/store.py in the apply-foundation layering (no SQL here: the db layer does the writes).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from graph_undo.db.connection import DbError, connect, transaction
from graph_undo.db.migrate import MigrationError, migrate
from graph_undo.db.store import Store
from graph_undo.domain.model import EDGE_TYPES, LEVEL, NODE_TYPES, Node, normalise_tags

NODE_KEYS = {"id", "qualified_name", "type", "description", "owner", "tags"}
EDGE_KEYS = {"from_id", "to_id", "type"}


class LoadError(Exception):
    def __init__(self, line: int, message: str):
        super().__init__(f"{line}: {message}")
        self.line = line
        self.message = message


def _no_duplicate_keys(pairs: list[tuple[str, object]]) -> dict:
    keys = [k for k, _ in pairs]
    dupes = sorted({k for k in keys if keys.count(k) > 1})
    if dupes:
        raise ValueError(f"duplicate keys {dupes}")
    return dict(pairs)


def _parse(seed_path: Path) -> tuple[list[tuple[int, dict]], list[tuple[int, dict]]]:
    try:
        text = seed_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise LoadError(0, f"cannot read seed: {e}") from e
    nodes, edges = [], []
    # split on \n only: str.splitlines() would also break on U+2028, \x85, \x0c ... inside JSON strings
    for no, raw in enumerate(text.split("\n"), start=1):
        if not raw.strip():
            continue
        try:
            row = json.loads(raw, object_pairs_hook=_no_duplicate_keys)
        except json.JSONDecodeError as e:
            raise LoadError(no, f"invalid JSON: {e.msg}") from e
        except ValueError as e:
            raise LoadError(no, str(e)) from e
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
        try:
            json.dumps(row, ensure_ascii=False).encode("utf-8")
        except UnicodeEncodeError as e:
            raise LoadError(no, "string is not valid UTF-8 (lone surrogate)") from e
        if row["type"] not in kinds:
            raise LoadError(no, f"unknown {'edge' if is_edge else 'node'} type {row['type']!r}")
        (edges if is_edge else nodes).append((no, row))
    return nodes, edges


def _validate(nodes, edges) -> tuple[list[Node], list[tuple[str, str, str]]]:
    """Graph rules of the seed, checked in Python so every failure names its line."""
    out_nodes: list[Node] = []
    types: dict[str, str] = {}
    names: set[str] = set()
    for no, n in nodes:
        if n["id"] in types:
            raise LoadError(no, f"duplicate id {n['id']!r}")
        if n["qualified_name"] in names:
            raise LoadError(no, f"duplicate qualified_name {n['qualified_name']!r}")
        types[n["id"]] = n["type"]
        names.add(n["qualified_name"])
        out_nodes.append(Node(n["id"], n["qualified_name"], n["type"], n["description"], n["owner"],
                              normalise_tags(n["tags"])))
    seen: set[tuple[str, str, str]] = set()
    parent: dict[str, str] = {}
    out_edges: list[tuple[str, str, str]] = []
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
        out_edges.append(key)
    return out_nodes, out_edges


def load_seed(seed_path: str | Path, db_path: str | Path) -> tuple[int, int]:
    """Create db_path from seed_path, replacing any existing file. All or nothing."""
    seed_path, db_path = Path(seed_path), Path(db_path)
    if db_path.resolve() == seed_path.resolve():
        raise LoadError(0, f"--db {db_path} is the seed file; refusing to overwrite it")
    nodes, edges = _validate(*_parse(seed_path))
    tmp = db_path.with_name(db_path.name + ".tmp")
    tmp.unlink(missing_ok=True)
    try:
        con = connect(tmp)
        try:
            migrate(con)
            with transaction(con):
                Store(con).bulk_load(nodes, edges)
        finally:
            con.close()
        for side in ("-journal", "-wal", "-shm"):   # stale side files of the old db must not touch the new one
            db_path.with_name(db_path.name + side).unlink(missing_ok=True)
        os.replace(tmp, db_path)
    except (DbError, MigrationError, OSError) as e:
        tmp.unlink(missing_ok=True)
        raise LoadError(0, f"cannot write {db_path}: {e}") from e
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return len(nodes), len(edges)
