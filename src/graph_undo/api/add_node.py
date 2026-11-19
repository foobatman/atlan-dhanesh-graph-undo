"""`add_node` handler (spec: docs/superpowers/specs/2026-11-19-op-add-node-design.md).

Checks run name -> parent -> type order; the first failure wins. Ingest on a live name is a pure noop.
"""

from __future__ import annotations

from graph_undo.api.context import OpContext, OpResult
from graph_undo.api.resolve import require_live
from graph_undo.db.oplog import LoggedOp
from graph_undo.domain.errors import NameTaken, TypeOrderViolation
from graph_undo.domain.model import LEVEL, Node, normalise_tags
from graph_undo.domain.ops import AddNode


def handle(ctx: OpContext, op: AddNode) -> OpResult:
    store = ctx.store
    existing = store.nodes.live_by_name(op.qualified_name)
    if existing is not None:
        if op.actor.kind == "ingest":
            store.log.record(LoggedOp.of(op, "noop", node_id=existing.id))
            return OpResult(op.op_id, "noop", node_id=existing.id)
        raise NameTaken(op.op_id, f"{op.qualified_name} is already live as node {existing.id}")

    parent = require_live(ctx, "parent", op.parent)
    if LEVEL[op.type] != LEVEL[parent.type] + 1:
        raise TypeOrderViolation(
            op.op_id, f"{op.type} {op.qualified_name} cannot go under {parent.type} {parent.qualified_name}: "
                      "contains must go exactly one level down")

    if op.actor.kind == "human":
        description, owner, tags = op.description, op.owner, normalise_tags(op.tags)
    else:
        description, owner, tags = "", "", ()
    node_id = store.mint_node_id(op.op_id)
    store.nodes.insert(Node(node_id, op.qualified_name, op.type, description, owner, tags))
    edge = store.edges.insert(parent.id, node_id, "contains")
    store.log.record(LoggedOp.of(op, "applied", node_id=node_id, edge_id=edge.id))
    return OpResult(op.op_id, "applied", node_id=node_id, edge_id=edge.id)
