"""Parse what the tool prints (dumps, undo reports) into plain values.

Written from the dump and undo specs' output contracts only; shares no code with graph_undo.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

AUTHORED = ("description", "owner", "tags")
IDENTITY = ("id", "qualified_name", "type")

Edge = tuple[str, str, str]


class ModelError(Exception):
    """A dump that can't be read; the checker can't judge anything from it."""


def fields(node: dict, keys) -> tuple:
    return tuple(json.dumps(node.get(k), sort_keys=True) for k in keys)


@dataclass(frozen=True)
class Dump:
    text: str
    nodes: dict[str, dict]
    edges: frozenset[Edge]
    parent: dict[str, str]
    multi_parent: frozenset[str] = field(default_factory=frozenset)

    def names(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for i in sorted(self.nodes):
            out.setdefault(self.nodes[i]["qualified_name"], []).append(i)
        return out


def parse_dump(text: str) -> Dump:
    nodes: dict[str, dict] = {}
    edges: list[Edge] = []
    for n, raw in enumerate(text.splitlines(), 1):
        if not raw.strip():
            raise ModelError(f"line {n}: blank line")
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ModelError(f"line {n}: not JSON ({e.msg})") from None
        if not isinstance(obj, dict):
            raise ModelError(f"line {n}: not a JSON object")
        if "from_id" in obj:
            try:
                edges.append((obj["from_id"], obj["to_id"], obj["type"]))
            except KeyError as e:
                raise ModelError(f"line {n}: edge without {e.args[0]}") from None
        elif "qualified_name" in obj:
            if "id" not in obj:
                raise ModelError(f"line {n}: node without id")
            if obj["id"] in nodes:
                raise ModelError(f"line {n}: duplicate node id {obj['id']}")
            nodes[obj["id"]] = obj
        else:
            raise ModelError(f"line {n}: neither a node nor an edge")
    parent: dict[str, str] = {}
    multi: set[str] = set()
    for f, t, ty in edges:
        if ty != "contains":
            continue
        if t in parent:
            multi.add(t)
        else:
            parent[t] = f
    return Dump(text, nodes, frozenset(edges), parent, frozenset(multi))


@dataclass(frozen=True)
class Removed:
    nodes: dict[str, dict]
    edges: frozenset[Edge]
    parent0: dict[str, str | None]


def removed(d0: Dump, d1: Dump) -> Removed:
    nodes = {i: n for i, n in d0.nodes.items() if i not in d1.nodes}
    return Removed(nodes, d0.edges - d1.edges, {i: d0.parent.get(i) for i in nodes})


@dataclass(frozen=True)
class ReportLine:
    lineno: int
    obj: dict | None
    error: str | None


def parse_report(text: str) -> list[ReportLine]:
    out = []
    for n, raw in enumerate(text.splitlines(), 1):
        if not raw.strip():
            out.append(ReportLine(n, None, "blank line"))
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError as e:
            out.append(ReportLine(n, None, f"not JSON ({e.msg})"))
            continue
        if not isinstance(obj, dict):
            out.append(ReportLine(n, None, "not a JSON object"))
            continue
        out.append(ReportLine(n, obj, None))
    return out
