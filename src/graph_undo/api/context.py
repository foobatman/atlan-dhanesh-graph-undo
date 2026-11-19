"""What a handler receives (OpContext) and returns (OpResult)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from graph_undo.db.store import Store
from graph_undo.domain.ops import Op


@dataclass(frozen=True)
class OpContext:
    store: Store
    op: Op


@dataclass(frozen=True)
class OpResult:
    op_id: str
    effect: Literal["applied", "noop", "skipped"]   # "skipped" only from apply_op, never logged
    node_id: str | None = None
    edge_id: int | None = None
    removed_nodes: int = 0
    removed_edges: int = 0


Handler = Callable[[OpContext, Op], OpResult]
