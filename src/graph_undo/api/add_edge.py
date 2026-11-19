"""`add_edge` handler (spec: docs/superpowers/specs/2026-11-19-op-add-edge-design.md).

Foundation stub: replace this module's body with the real handler and delete STUB.
"""

from __future__ import annotations

from graph_undo.api.context import OpContext, OpResult
from graph_undo.domain.ops import AddEdge

STUB = True


def handle(ctx: OpContext, op: AddEdge) -> OpResult:
    raise NotImplementedError("add_edge handler not implemented")
