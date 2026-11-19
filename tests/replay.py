"""Independent replay of seed + ops, written from the ops spec. Must not import graph_undo.ops_fixture."""

from __future__ import annotations

from dataclasses import dataclass, field

LEVEL = {"connection": 0, "database": 1, "schema": 2, "table": 3, "column": 4}
AUTHORED = {"description", "owner", "tags"}


@dataclass
class State:
    nodes: dict = field(default_factory=dict)      # qn -> node dict (with id)
    parent: dict = field(default_factory=dict)     # child qn -> parent qn
    refs: set = field(default_factory=set)         # (from qn, to qn)
    seed_ids: set = field(default_factory=set)
    minted: int = 0

    def subtree(self, qn):
        kids = {}
        for c, p in self.parent.items():
            kids.setdefault(p, []).append(c)
        out, stack = [], [qn]
        while stack:
            q = stack.pop()
            out.append(q)
            stack.extend(kids.get(q, []))
        return out


def load(nodes, edges) -> State:
    s = State()
    by_id = {n["id"]: n for n in nodes}
    for n in nodes:
        s.nodes[n["qualified_name"]] = dict(n)
        s.seed_ids.add(n["id"])
    for e in edges:
        f, t = by_id[e["from_id"]]["qualified_name"], by_id[e["to_id"]]["qualified_name"]
        if e["type"] == "contains":
            s.parent[t] = f
        else:
            s.refs.add((f, t))
    return s


def apply(s: State, op: dict) -> None:
    oid, kind, actor = op["op_id"], op["op"], op["actor"]["kind"]

    def need(cond, msg):
        if not cond:
            raise AssertionError(f"{oid} {kind}: {msg}")

    if kind == "add_node":
        qn = op["qualified_name"]
        if actor == "ingest" and qn in s.nodes:
            return  # upsert of a live name: no-op
        need(qn not in s.nodes, "name already live")
        need(op["parent"] in s.nodes, "parent not live")
        need(LEVEL[op["type"]] == LEVEL[s.nodes[op["parent"]]["type"]] + 1, "type order")
        s.minted += 1
        authored = ({"description": "", "owner": "", "tags": []} if actor == "ingest" else
                    {"description": op["description"], "owner": op["owner"], "tags": op["tags"]})
        s.nodes[qn] = {"id": f"replay-{s.minted}", "qualified_name": qn, "type": op["type"], **authored}
        s.parent[qn] = op["parent"]
    elif kind == "edit_node":
        need(actor == "human", "edit by non-human")
        need(op["node"] in s.nodes, "node not live")
        s.nodes[op["node"]].update(op["set"])
    elif kind == "delete_node":
        need(op["node"] in s.nodes, "node not live")
        gone = set(s.subtree(op["node"]))
        for q in gone:
            del s.nodes[q]
            s.parent.pop(q, None)
        s.refs = {(f, t) for f, t in s.refs if f not in gone and t not in gone}
    elif kind == "add_edge":
        f, t = op["from"], op["to"]
        need(f in s.nodes and t in s.nodes and f != t, "bad endpoints")
        if op["type"] == "contains":
            need(t not in s.parent, "child already has a parent")
            need(LEVEL[s.nodes[t]["type"]] == LEVEL[s.nodes[f]["type"]] + 1, "type order")
            s.parent[t] = f
        else:
            need((f, t) not in s.refs, "duplicate reference")
            s.refs.add((f, t))
    elif kind == "delete_edge":
        need(actor == "human", "delete_edge by non-human")
        f, t = op["from"], op["to"]
        if op["type"] == "contains":
            need(s.parent.get(t) == f, "no such contains edge")
            del s.parent[t]
        else:
            need((f, t) in s.refs, "no such reference")
            s.refs.discard((f, t))
    else:
        raise AssertionError(f"{oid}: unknown op {kind}")


def replay(nodes, edges, ops) -> State:
    s = load(nodes, edges)
    for op in ops:
        apply(s, op)
    return s
