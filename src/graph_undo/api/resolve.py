"""Name resolution shared by all handlers, so every op words NodeNotFound the same way."""

from __future__ import annotations

from graph_undo.api.context import OpContext
from graph_undo.domain.errors import NodeNotFound
from graph_undo.domain.model import Node


def require_live(ctx: OpContext, field: str, qn: str) -> Node:
    """The live node named qn, else NodeNotFound "{field} '{qn}' is not a live node".
    field is one of node, parent, from, to."""
    node = ctx.store.nodes.live_by_name(qn)
    if node is None:
        raise NodeNotFound.for_field(ctx.op.op_id, field, qn)
    return node
