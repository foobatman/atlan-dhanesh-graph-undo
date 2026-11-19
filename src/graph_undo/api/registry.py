"""Handler registry and apply_op: one op, one transaction (spec: apply-foundation, "apply_op and apply_file")."""

from __future__ import annotations

from graph_undo.api import add_edge, add_node, delete_edge, delete_node, edit_node
from graph_undo.api.context import Handler, OpContext, OpResult
from graph_undo.db.connection import transaction
from graph_undo.db.store import Store
from graph_undo.domain.errors import MalformedOp, OpIdConflict
from graph_undo.domain.ops import Op, canonical

HANDLERS: dict[str, Handler] = {
    "add_node": add_node.handle,
    "edit_node": edit_node.handle,
    "delete_node": delete_node.handle,
    "add_edge": add_edge.handle,
    "delete_edge": delete_edge.handle,
}


def apply_op(store: Store, op: Op) -> OpResult:
    """Apply one parsed op in its own transaction.

    Already logged with the same payload: skipped, nothing written. Same op_id, other payload: OpIdConflict.
    Any exception from the handler (OpError or a bug) rolls the whole op back and propagates.
    """
    logged = store.log.get(op.op_id)
    if logged is not None:
        if logged.payload != canonical(op.raw):
            raise OpIdConflict.for_payload(op.op_id)
        return OpResult(op.op_id, "skipped")
    handler = HANDLERS.get(op.kind)
    if handler is None:
        raise MalformedOp(op.op_id, f"no handler for op {op.kind!r}")
    with transaction(store.con):
        result = handler(OpContext(store, op), op)
        if store.log.get(op.op_id) is None:
            raise RuntimeError(f"bug: {op.kind} handler wrote no op-log row for {op.op_id}")
    return result
