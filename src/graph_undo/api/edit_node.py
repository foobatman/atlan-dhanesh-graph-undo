"""`edit_node` handler (spec: docs/superpowers/specs/2026-11-19-op-edit-node-design.md).

Foundation stub: replace this module's body with the real handler and delete STUB.
"""

from __future__ import annotations

from graph_undo.api.context import OpContext, OpResult
from graph_undo.domain.ops import EditNode

STUB = True


def handle(ctx: OpContext, op: EditNode) -> OpResult:
    raise NotImplementedError("edit_node handler not implemented")
