"""`add_edge` handler (spec: docs/superpowers/specs/2026-11-19-op-add-edge-design.md).

Creates one live edge from -> to. Checks run in the spec's order and only read; the edge row and the
op-log row are written only after every check has passed. A dead identical edge is never revived: the
new edge always gets a new row id.
"""

from __future__ import annotations

from graph_undo.api.context import OpContext, OpResult
from graph_undo.api.resolve import require_live
from graph_undo.db.oplog import LoggedOp
from graph_undo.domain.errors import AlreadyHasParent, DuplicateEdge, SelfLoop, TypeOrderViolation
from graph_undo.domain.model import LEVEL, NODE_TYPES, Node
from graph_undo.domain.ops import AddEdge


def handle(ctx: OpContext, op: AddEdge) -> OpResult:
    store = ctx.store
    src = require_live(ctx, "from", op.from_)
    dst = require_live(ctx, "to", op.to)
    if src.id == dst.id:
        raise SelfLoop(op.op_id, f"{op.type} edge from {src.qualified_name!r} to itself")
    if op.type == "contains":
        _check_type_order(op.op_id, src, dst)
    existing = store.edges.live(src.id, dst.id, op.type)
    if existing is not None:
        raise DuplicateEdge(op.op_id, f"{op.type} edge {src.qualified_name!r} -> {dst.qualified_name!r} "
                                      f"already exists (edge {existing.id})")
    if op.type == "contains":
        parent_edge = store.edges.live_parent(dst.id)
        if parent_edge is not None:
            parent = store.nodes.by_id(parent_edge.from_id)
            raise AlreadyHasParent(op.op_id, f"{dst.qualified_name!r} already has parent "
                                             f"{parent.qualified_name!r} (edge {parent_edge.id}); "
                                             "delete that contains edge first")

    created = store.edges.insert(src.id, dst.id, op.type)
    store.log.record(LoggedOp.of(op, "applied", edge_id=created.id))
    return OpResult(op.op_id, "applied", edge_id=created.id)


def _check_type_order(op_id: str, parent: Node, child: Node) -> None:
    """contains must go exactly one level down (not enforced by the database)."""
    if LEVEL[child.type] == LEVEL[parent.type] + 1:
        return
    head = f"contains {parent.qualified_name!r} ({parent.type}) -> {child.qualified_name!r} ({child.type})"
    level = LEVEL[parent.type]
    if level + 1 >= len(NODE_TYPES):
        raise TypeOrderViolation(op_id, f"{head}: a {parent.type} cannot contain anything")
    raise TypeOrderViolation(op_id, f"{head}: a {parent.type} can only contain a {NODE_TYPES[level + 1]}")
