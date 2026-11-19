"""Typed op errors. The message text is produced here once, so every interface prints the same thing.

str(e) == f"{op_id} {code}: {message}"  (or f"{code}: {message}" when the op has no usable op_id).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import ClassVar


class OpError(Exception):
    code: ClassVar[str] = "op_error"

    def __init__(self, op_id: str | None, message: str):
        self.op_id = op_id
        self.message = message
        super().__init__(str(self))

    def __str__(self) -> str:
        head = f"{self.op_id} {self.code}" if self.op_id is not None else self.code
        return f"{head}: {self.message}"


class MalformedOp(OpError):
    """Bad JSON, wrong or missing keys, wrong types, unknown op or type, bad op_id."""
    code = "malformed"


class ActorNotAllowed(OpError):
    """Ingest sends edit_node/delete_node/delete_edge, or authored fields on add_node. Raised by parse_op."""
    code = "actor_not_allowed"

    @classmethod
    def for_op(cls, op_id: str | None, op: str) -> "ActorNotAllowed":
        return cls(op_id, f"ingest may not {op}")

    @classmethod
    def for_fields(cls, op_id: str | None, fields: Iterable[str]) -> "ActorNotAllowed":
        return cls(op_id, f"ingest may not set {', '.join(fields)}")


class NodeNotFound(OpError):
    """A referenced name (node, parent, from, to) is not live."""
    code = "node_not_found"

    @classmethod
    def for_field(cls, op_id: str | None, field: str, qn: str) -> "NodeNotFound":
        return cls(op_id, f"{field} '{qn}' is not a live node")


class NameTaken(OpError):
    code = "name_taken"


class TypeOrderViolation(OpError):
    code = "type_order"


class SelfLoop(OpError):
    code = "self_loop"


class DuplicateEdge(OpError):
    code = "duplicate_edge"


class AlreadyHasParent(OpError):
    code = "already_has_parent"


class EdgeNotFound(OpError):
    code = "edge_not_found"


class OpIdConflict(OpError):
    code = "op_id_conflict"

    @classmethod
    def for_payload(cls, op_id: str) -> "OpIdConflict":
        return cls(op_id, f"op_id {op_id} is already in the log with a different payload")
