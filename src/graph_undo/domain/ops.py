"""Typed ops and `parse_op`: shape checks only (spec: apply-foundation, "Domain types", "Shared conventions" 2-3).

Rules that need the graph (a name is live, a parent exists) belong to the handlers.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import ClassVar, Union

from graph_undo.domain.errors import ActorNotAllowed, MalformedOp
from graph_undo.domain.model import AUTHORED_FIELDS, EDGE_TYPES, NODE_TYPES, Actor, normalise_tags

OP_ID = re.compile(r"op-[0-9]{4,}")
ACTOR_KINDS = ("human", "ingest")
COMMON_KEYS = ("op_id", "actor", "op")
INGEST_FORBIDDEN_OPS = ("edit_node", "delete_node", "delete_edge")


def canonical(d: object) -> str:
    """Compact, sorted-key JSON: ops.payload, ops.before and the OpIdConflict comparison all use this."""
    return json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


@dataclass(frozen=True)
class _Op:
    KIND: ClassVar[str] = ""
    op_id: str
    actor: Actor
    raw: dict = field(hash=False, repr=False)   # the op exactly as received; goes to the op log

    @property
    def kind(self) -> str:
        return self.KIND


@dataclass(frozen=True)
class AddNode(_Op):
    KIND: ClassVar[str] = "add_node"
    qualified_name: str = ""
    type: str = ""
    parent: str = ""
    description: str | None = None      # None for ingest
    owner: str | None = None
    tags: tuple[str, ...] | None = None  # normalised (sorted, unique); None for ingest


@dataclass(frozen=True)
class EditNode(_Op):
    KIND: ClassVar[str] = "edit_node"
    node: str = ""
    set: dict = field(default_factory=dict, hash=False)   # non-empty, keys ⊆ AUTHORED_FIELDS, tags a tuple


@dataclass(frozen=True)
class DeleteNode(_Op):
    KIND: ClassVar[str] = "delete_node"
    node: str = ""


@dataclass(frozen=True)
class AddEdge(_Op):
    KIND: ClassVar[str] = "add_edge"
    from_: str = ""
    to: str = ""
    type: str = ""


@dataclass(frozen=True)
class DeleteEdge(_Op):
    KIND: ClassVar[str] = "delete_edge"
    from_: str = ""
    to: str = ""
    type: str = ""


Op = Union[AddNode, EditNode, DeleteNode, AddEdge, DeleteEdge]
OP_KINDS = ("add_node", "edit_node", "delete_node", "add_edge", "delete_edge")

# exact field set per (op, actor kind), beyond COMMON_KEYS: the ops-fixture "Operation contract"
_FIELDS = {
    ("add_node", "human"): ("qualified_name", "type", "parent", "description", "owner", "tags"),
    ("add_node", "ingest"): ("qualified_name", "type", "parent"),
    ("edit_node", "human"): ("node", "set"),
    ("delete_node", "human"): ("node",),
    ("add_edge", "human"): ("from", "to", "type"),
    ("add_edge", "ingest"): ("from", "to", "type"),
    ("delete_edge", "human"): ("from", "to", "type"),
}


def _is_tags(v: object) -> bool:
    return isinstance(v, list) and all(isinstance(t, str) for t in v)


def _str(op_id: str, d: dict, key: str) -> str:
    v = d[key]
    if not isinstance(v, str):
        raise MalformedOp(op_id, f"{key} must be a string")
    return v


def _parse_set(op_id: str, s: object) -> dict:
    if not isinstance(s, dict) or not s:
        raise MalformedOp(op_id, "set must be a non-empty object")
    unknown = sorted(k for k in s if k not in AUTHORED_FIELDS)
    if unknown:
        raise MalformedOp(op_id, f"set has unknown key {unknown[0]!r}")
    out = {}
    for k, v in s.items():
        if k == "tags":
            if not _is_tags(v):
                raise MalformedOp(op_id, "set.tags must be a list of strings")
            out[k] = normalise_tags(v)
        else:
            if not isinstance(v, str):
                raise MalformedOp(op_id, f"set.{k} must be a string")
            out[k] = v
    return out


def parse_op(d: object) -> Op:
    """Check the shape of one op and return its typed form, or raise MalformedOp / ActorNotAllowed.

    Order: JSON object, op_id, actor, known op, actor allowed for op, exact key set, field types and enums.
    """
    if not isinstance(d, dict):
        raise MalformedOp(None, "line is not a JSON object")

    op_id = d.get("op_id")
    if "op_id" not in d:
        raise MalformedOp(None, "missing key 'op_id'")
    if not isinstance(op_id, str):
        raise MalformedOp(None, "op_id must be a string")
    if not OP_ID.fullmatch(op_id):
        raise MalformedOp(op_id, f"op_id {op_id!r} does not match op-NNNN")

    if "actor" not in d:
        raise MalformedOp(op_id, "missing key 'actor'")
    a = d["actor"]
    if not isinstance(a, dict) or set(a) != {"kind", "name"}:
        raise MalformedOp(op_id, "actor must be an object with exactly kind and name")
    if a["kind"] not in ACTOR_KINDS:
        raise MalformedOp(op_id, f"unknown actor kind {a['kind']!r}")
    if not isinstance(a["name"], str):
        raise MalformedOp(op_id, "actor.name must be a string")
    actor = Actor(a["kind"], a["name"])

    if "op" not in d:
        raise MalformedOp(op_id, "missing key 'op'")
    kind = d["op"]
    if not isinstance(kind, str) or kind not in OP_KINDS:
        raise MalformedOp(op_id, f"unknown op {kind!r}")

    if actor.kind == "ingest":
        if kind in INGEST_FORBIDDEN_OPS:
            raise ActorNotAllowed.for_op(op_id, kind)
        if kind == "add_node":
            authored = [f for f in AUTHORED_FIELDS if f in d]
            if authored:
                raise ActorNotAllowed.for_fields(op_id, authored)

    want = set(COMMON_KEYS) | set(_FIELDS[(kind, actor.kind)])
    if set(d) != want:
        missing, extra = sorted(want - set(d)), sorted(set(d) - want)
        raise MalformedOp(op_id, f"{kind} keys: missing {missing}, unexpected {extra}")

    try:
        canonical(d).encode("utf-8")
    except UnicodeEncodeError:
        raise MalformedOp(op_id, "string is not valid UTF-8 (lone surrogate)") from None

    if kind == "add_node":
        qn, parent = _str(op_id, d, "qualified_name"), _str(op_id, d, "parent")
        type_ = _str(op_id, d, "type")
        if type_ not in NODE_TYPES:
            raise MalformedOp(op_id, f"unknown node type {type_!r}")
        if actor.kind == "ingest":
            return AddNode(op_id, actor, d, qualified_name=qn, type=type_, parent=parent)
        description, owner = _str(op_id, d, "description"), _str(op_id, d, "owner")
        if not _is_tags(d["tags"]):
            raise MalformedOp(op_id, "tags must be a list of strings")
        return AddNode(op_id, actor, d, qualified_name=qn, type=type_, parent=parent,
                       description=description, owner=owner, tags=normalise_tags(d["tags"]))
    if kind == "edit_node":
        node = _str(op_id, d, "node")
        return EditNode(op_id, actor, d, node=node, set=_parse_set(op_id, d["set"]))
    if kind == "delete_node":
        return DeleteNode(op_id, actor, d, node=_str(op_id, d, "node"))
    # add_edge / delete_edge
    f, t, type_ = _str(op_id, d, "from"), _str(op_id, d, "to"), _str(op_id, d, "type")
    if type_ not in EDGE_TYPES:
        raise MalformedOp(op_id, f"unknown edge type {type_!r}")
    cls = AddEdge if kind == "add_edge" else DeleteEdge
    return cls(op_id, actor, d, from_=f, to=t, type=type_)
