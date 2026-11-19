"""`delete_edge` handler (spec: docs/superpowers/specs/2026-11-19-op-delete-edge-design.md).

Soft-deletes exactly one live edge (from, to, type): sets its deleted_by, logs the op and records one
removed_edges row. Runs inside apply_op's transaction; never commits, never hard-deletes, never touches nodes.
"""

from __future__ import annotations

from graph_undo.api.context import OpContext, OpResult
from graph_undo.api.resolve import require_live
from graph_undo.db.oplog import LoggedOp
from graph_undo.domain.errors import EdgeNotFound
from graph_undo.domain.ops import DeleteEdge


def handle(ctx: OpContext, op: DeleteEdge) -> OpResult:
    store = ctx.store
    src = require_live(ctx, "from", op.from_)
    dst = require_live(ctx, "to", op.to)
    # from == to needs no special case: the schema forbids self-loops, so the lookup finds nothing.
    edge = store.edges.live(src.id, dst.id, op.type)
    if edge is None:
        raise EdgeNotFound(op.op_id, f"no live {op.type} edge '{op.from_}' -> '{op.to}'")

    updated = store.edges.mark_deleted([edge.id], op.op_id)
    assert updated == 1, f"bug: delete_edge {op.op_id} marked {updated} rows, expected 1"
    store.log.record(LoggedOp.of(op, "applied", edge_id=edge.id))
    store.log.add_removals(op.op_id, [], [edge.id])  # after record: removed_edges.op_id -> ops
    return OpResult(op.op_id, "applied", edge_id=edge.id, removed_edges=1)
