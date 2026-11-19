"""undo_op: reverse one recorded delete under rule B2 (spec: undo-design).

Checks (no writes): op_id format, op exists, op is an applied delete_node/delete_edge, op not undone yet.
Then, in one transaction: bulk-read the inputs, plan with domain.undo.plan_undo, revive restored rows
(nodes before edges), insert the merged contains edges, and record the undo log.
"""

from __future__ import annotations

from dataclasses import dataclass

from graph_undo.db.connection import transaction
from graph_undo.db.store import Store
from graph_undo.db.undolog import UndoItem
from graph_undo.domain.errors import AlreadyUndone, MalformedOp, NotUndoable, OpNotFound
from graph_undo.domain.ops import OP_ID
from graph_undo.domain.undo import UndoInputs, UndoPlan, plan_undo, report_lines

UNDOABLE = ("delete_node", "delete_edge")


@dataclass(frozen=True)
class UndoResult:
    op_id: str
    lines: list[str]            # the JSONL report, one line per removed node/edge
    counts: dict[str, int]      # restored_nodes, restored_edges, merged, skipped


def _check(store: Store, op_id: str) -> str:
    """Raise the OpError that refuses this undo, else return the op kind."""
    if not isinstance(op_id, str) or not OP_ID.fullmatch(op_id):
        raise MalformedOp(op_id, f"op_id {op_id!r} does not match op-NNNN")
    logged = store.log.get(op_id)
    if logged is None:
        raise OpNotFound.for_op(op_id)
    if logged.op not in UNDOABLE or logged.effect != "applied":
        raise NotUndoable.for_op(op_id, logged.op)
    done = store.undo_log.get(op_id)
    if done is not None:
        raise AlreadyUndone.for_record(op_id, done.undo_seq, done.after_op_id, store.undo_log.counts(op_id))
    return logged.op


def _inputs(store: Store, op_id: str, op: str) -> UndoInputs:
    node_ids, edge_ids = store.log.removals(op_id)
    removed = store.nodes.by_ids(node_ids)
    edges = store.edges.by_ids(edge_ids)
    outside = sorted({i for e in edges for i in (e.from_id, e.to_id)} - set(removed))
    contains_children = [e.to_id for e in edges if e.type == "contains" and e.to_id not in removed]
    live_parents = store.edges.live_parents(contains_children)
    others = store.nodes.by_ids(outside + [e.from_id for e in live_parents.values()])
    names = [n.qualified_name for n in removed.values()]
    names += [n.qualified_name for n in others.values() if n.deleted_by is not None]
    holders = store.nodes.live_by_names(names)
    return UndoInputs(
        op_id=op_id, op=op,
        root_id=store.log.get(op_id).node_id if op == "delete_node" else None,
        removed_nodes=removed, removed_edges=edges, others=others, holders=holders,
        creators=store.log.creators([h.id for h in holders.values()]),
        live_triples=store.edges.live_triples([(e.from_id, e.to_id, e.type) for e in edges]),
        live_parents=live_parents)


def _write(store: Store, plan: UndoPlan) -> list[str]:
    """Apply the plan; returns the report lines. Nodes go live before any edge that touches them."""
    nodes = [o.node.id for o in plan.nodes if o.outcome == "restored"]
    edges = [o.edge.id for o in plan.edges if o.outcome == "restored"]
    revived = store.nodes.revive(nodes, plan.op_id)
    assert revived == len(nodes), f"revived {revived} nodes, expected {len(nodes)}"
    revived = store.edges.revive(edges, plan.op_id)
    assert revived == len(edges), f"revived {revived} edges, expected {len(edges)}"
    new_ids = {o.edge.id: store.edges.insert(o.new_from_id, o.edge.to_id, "contains").id
               for o in plan.edges if o.outcome == "merged"}
    lines = report_lines(plan, new_ids)
    store.undo_log.record(plan.op_id, store.log.seq(store.log.last_op_id()), "".join(lines))
    store.undo_log.add_items(plan.op_id, [
        *(UndoItem("node", o.node.id, o.outcome, o.reason_code, None) for o in plan.nodes),
        *(UndoItem("edge", str(o.edge.id), o.outcome, o.reason_code, new_ids.get(o.edge.id)) for o in plan.edges),
    ])
    return lines


def undo_op(store: Store, op_id: str) -> UndoResult:
    """Undo one applied delete op. Raises MalformedOp, OpNotFound, NotUndoable or AlreadyUndone (nothing written)."""
    op = _check(store, op_id)
    with transaction(store.con):
        lines = _write(store, plan_undo(_inputs(store, op_id, op)))
        counts = store.undo_log.counts(op_id)
    return UndoResult(op_id, lines, counts)
