"""Undo planner: every conflict rule of rule B2, as a pure function (spec: undo-design, "Semantics").

plan_undo() decides an outcome for every node and edge the delete removed; it reads nothing and writes
nothing. The API layer gathers UndoInputs from the database and applies the plan.

Node rules, top-down (rule numbers as in the spec):
  1   name held by a live node                         -> skipped name_held
  2a  root with no parent edge                         -> restored, parent None
  2b  original parent live, or restored in this undo   -> restored under it
  2c  parent's name held by a live node of same type   -> restored under that holder (contains edge merged)
  2d  holder of parent's name has another type         -> skipped parent_type_mismatch
  2e  otherwise                                        -> skipped no_live_parent
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from graph_undo.domain.model import Edge, Node
from graph_undo.domain.ops import canonical


@dataclass(frozen=True)
class UndoInputs:
    op_id: str
    op: str                                   # 'delete_node' | 'delete_edge'
    root_id: str | None                       # delete_node root; None for delete_edge
    removed_nodes: dict[str, Node]            # N, by id (dead rows)
    removed_edges: list[Edge]                 # E
    others: dict[str, Node]                   # endpoints of E outside N, plus the from-node of each live_parents edge
    holders: dict[str, Node]                  # qualified_name -> live node holding it
    creators: dict[str, tuple[str, str]]      # holder id -> (op_id, actor_kind); absent = seed
    live_triples: set[tuple[str, str, str]]   # live (from, to, type) among E's triples
    live_parents: dict[str, Edge]             # child id -> its live contains edge


@dataclass(frozen=True)
class NodeOutcome:
    node: Node
    outcome: str                              # 'restored' | 'skipped'
    parent_id: str | None = None              # restored: the node it now hangs under (None = root)
    reason_code: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class EdgeOutcome:
    edge: Edge
    outcome: str                              # 'restored' | 'merged' | 'skipped'
    new_from_id: str | None = None            # merged: the holder the new contains edge starts at
    reason_code: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class UndoPlan:
    op_id: str
    op: str
    nodes: list[NodeOutcome]                  # top-down (write order)
    edges: list[EdgeOutcome]                  # by edge id


def _top_down(inp: UndoInputs) -> list[Node]:
    """N in walk order: BFS from the root over E's contains edges, children by qualified_name."""
    n = inp.removed_nodes
    if not n:
        return []
    children: dict[str, list[str]] = defaultdict(list)
    for e in inp.removed_edges:
        if e.type == "contains" and e.from_id in n and e.to_id in n:
            children[e.from_id].append(e.to_id)
    order, queue = [], [inp.root_id]
    while queue:
        nxt = []
        for i in queue:
            order.append(n[i])
            nxt.extend(sorted(children[i], key=lambda c: n[c].qualified_name))
        queue = nxt
    if len(order) != len(n):
        missing = sorted(set(n) - {x.id for x in order})
        raise ValueError(f"removed nodes not reachable from root {inp.root_id}: {missing}")
    return order


def plan_undo(inp: UndoInputs) -> UndoPlan:
    n = inp.removed_nodes
    parent_of = {e.to_id: e.from_id for e in inp.removed_edges if e.type == "contains" and e.to_id in n}
    done: dict[str, NodeOutcome] = {}

    def holder_reason(h: Node) -> str:
        made = inp.creators.get(h.id)
        return f"name held by {h.id} created by " + (f"{made[0]} ({made[1]})" if made else "seed")

    def decide(node: Node) -> NodeOutcome:
        h = inp.holders.get(node.qualified_name)
        if h is not None:                                                     # rule 1
            return NodeOutcome(node, "skipped", reason_code="name_held", reason=holder_reason(h))
        p_id = parent_of.get(node.id)
        if p_id is None:                                                      # 2a
            return NodeOutcome(node, "restored", parent_id=None)
        if p_id in n:
            p, p_out = n[p_id], done[p_id]
            if p_out.outcome == "restored":                                   # 2b
                return NodeOutcome(node, "restored", parent_id=p_id)
            holder = inp.holders.get(p.qualified_name) if p_out.reason_code == "name_held" else None
            gone = f"parent {p.qualified_name} was not restored ({p_out.reason_code})"
        else:
            p = inp.others[p_id]
            if p.deleted_by is None:                                          # 2b
                return NodeOutcome(node, "restored", parent_id=p_id)
            holder = inp.holders.get(p.qualified_name)
            gone = f"parent {p.qualified_name} was deleted by {p.deleted_by}"
        if holder is not None and holder.type == p.type:                      # 2c
            return NodeOutcome(node, "restored", parent_id=holder.id)
        if holder is not None:                                                # 2d
            return NodeOutcome(node, "skipped", reason_code="parent_type_mismatch",
                               reason=f"parent {p.qualified_name} is held by {holder.id} of type {holder.type}, "
                                      f"not {p.type}")
        return NodeOutcome(node, "skipped", reason_code="no_live_parent", reason=gone)   # 2e

    nodes = []
    for node in _top_down(inp):
        done[node.id] = decide(node)
        nodes.append(done[node.id])

    def endpoint_problem(node_id: str) -> tuple[str, str] | None:
        if node_id in n:
            o = done[node_id]
            if o.outcome != "restored":
                return "endpoint_not_restored", f"endpoint {o.node.qualified_name} was skipped ({o.reason_code})"
            return None
        other = inp.others[node_id]
        if other.deleted_by is not None:
            return "endpoint_deleted", f"endpoint {other.qualified_name} was deleted by {other.deleted_by}"
        return None

    def qn(node_id: str) -> str:
        return (n.get(node_id) or inp.others[node_id]).qualified_name

    def decide_edge(e: Edge) -> EdgeOutcome:
        if e.type == "contains" and e.to_id in n:
            c = done[e.to_id]
            if c.outcome != "restored":
                return EdgeOutcome(e, "skipped", reason_code="endpoint_not_restored",
                                   reason=f"child {c.node.qualified_name} was skipped ({c.reason_code})")
            if c.parent_id == e.from_id:
                return EdgeOutcome(e, "restored")
            return EdgeOutcome(e, "merged", new_from_id=c.parent_id, reason_code="reparented",
                               reason=f"recreated as {c.parent_id} → {e.to_id}")
        for end in (e.from_id, e.to_id):
            problem = endpoint_problem(end)
            if problem:
                return EdgeOutcome(e, "skipped", reason_code=problem[0], reason=problem[1])
        if (e.from_id, e.to_id, e.type) in inp.live_triples:
            return EdgeOutcome(e, "skipped", reason_code="duplicate_edge",
                               reason=f"a live {e.type} edge {qn(e.from_id)} → {qn(e.to_id)} already exists")
        if e.type == "contains" and e.to_id in inp.live_parents:
            return EdgeOutcome(e, "skipped", reason_code="child_has_parent",
                               reason=f"{qn(e.to_id)} now has parent {qn(inp.live_parents[e.to_id].from_id)}")
        return EdgeOutcome(e, "restored")

    edges = [decide_edge(e) for e in sorted(inp.removed_edges, key=lambda e: e.id)]
    return UndoPlan(inp.op_id, inp.op, nodes, edges)


def report_lines(plan: UndoPlan, new_edge_ids: dict[int, int]) -> list[str]:
    """The JSONL report: node lines by (qualified_name, id), then edge lines by id; canonical JSON + newline."""
    out = []
    for o in sorted(plan.nodes, key=lambda o: (o.node.qualified_name, o.node.id)):
        d = {"kind": "node", "id": o.node.id, "op_id": plan.op_id, "qualified_name": o.node.qualified_name,
             "outcome": o.outcome}
        if o.outcome == "restored":
            d["parent_id"] = o.parent_id
        else:
            d["reason_code"], d["reason"] = o.reason_code, o.reason
        out.append(canonical(d) + "\n")
    for o in sorted(plan.edges, key=lambda o: o.edge.id):
        e = o.edge
        d = {"kind": "edge", "id": e.id, "op_id": plan.op_id, "from_id": e.from_id, "to_id": e.to_id,
             "type": e.type, "outcome": o.outcome}
        if o.outcome != "restored":
            d["reason_code"], d["reason"] = o.reason_code, o.reason
        if o.outcome == "merged":
            d["new_edge_id"] = new_edge_ids[e.id]
        out.append(canonical(d) + "\n")
    return out
