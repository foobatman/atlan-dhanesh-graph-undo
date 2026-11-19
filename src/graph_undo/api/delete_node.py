"""`delete_node` handler (spec: docs/superpowers/specs/2026-11-19-op-delete-node-design.md).

Cascade: the live root, every live descendant along live contains edges, and every live edge touching
any of them are flagged with deleted_by = op_id, and exactly those rows are recorded in removals.
A constant number of set-based statements, whatever the subtree size.
"""

from __future__ import annotations

from graph_undo.api.context import OpContext, OpResult
from graph_undo.api.resolve import require_live
from graph_undo.db.oplog import LoggedOp
from graph_undo.domain.ops import DeleteNode


def handle(ctx: OpContext, op: DeleteNode) -> OpResult:
    store = ctx.store
    root = require_live(ctx, "node", op.node)
    node_ids = store.nodes.live_subtree_ids(root.id)
    edge_ids = [e.id for e in store.edges.live_touching(node_ids)]

    # Edges before nodes: no statement leaves a live edge pointing at a dead node.
    flagged_edges = store.edges.mark_deleted(edge_ids, op.op_id)
    if flagged_edges != len(edge_ids):
        raise RuntimeError(f"bug: flagged {flagged_edges} of {len(edge_ids)} edges")
    flagged_nodes = store.nodes.mark_deleted(node_ids, op.op_id)
    if flagged_nodes != len(node_ids):
        raise RuntimeError(f"bug: flagged {flagged_nodes} of {len(node_ids)} nodes")

    # Log row before removals: removed_*.op_id references ops(op_id).
    store.log.record(LoggedOp.of(op, "applied", node_id=root.id))
    store.log.add_removals(op.op_id, node_ids, edge_ids)
    return OpResult(op.op_id, "applied", node_id=root.id, edge_id=None,
                    removed_nodes=len(node_ids), removed_edges=len(edge_ids))
