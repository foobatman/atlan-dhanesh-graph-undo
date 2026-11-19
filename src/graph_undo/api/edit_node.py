"""`edit_node` handler (spec: docs/superpowers/specs/2026-11-19-op-edit-node-design.md).

Replaces the given authored fields of one live node and logs their previous values in ops.before.
A set that changes nothing is still 'applied' (a human stated the value; undo must respect it).
"""

from __future__ import annotations

from graph_undo.api.context import OpContext, OpResult
from graph_undo.api.resolve import require_live
from graph_undo.db.oplog import LoggedOp
from graph_undo.domain.ops import EditNode, canonical


def handle(ctx: OpContext, op: EditNode) -> OpResult:
    node = require_live(ctx, "node", op.node)
    before = {k: list(node.tags) if k == "tags" else getattr(node, k) for k in op.set}
    ctx.store.nodes.update_fields(node.id, dict(op.set))
    ctx.store.log.record(LoggedOp.of(op, "applied", node_id=node.id, before=canonical(before)))
    return OpResult(op.op_id, "applied", node_id=node.id)
