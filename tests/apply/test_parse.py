"""parse_op, errors and domain types (spec: apply-foundation, "Domain types", "Errors", "Shared conventions")."""

import copy
import json

import pytest

from graph_undo.domain import errors
from graph_undo.domain.errors import ActorNotAllowed, MalformedOp, NodeNotFound, OpError
from graph_undo.domain.model import Actor, Edge, Node, normalise_tags
from graph_undo.domain.ops import AddEdge, AddNode, DeleteEdge, DeleteNode, EditNode, canonical, parse_op

HUMAN = {"kind": "human", "name": "ann"}
INGEST = {"kind": "ingest", "name": "ingest-crawler"}


def op(kind, actor=HUMAN, op_id="op-0001", **fields):
    return {"op_id": op_id, "actor": dict(actor) if isinstance(actor, dict) else actor, "op": kind, **fields}


def human_add(**over):
    d = op("add_node", qualified_name="conn/db/x", type="schema", parent="conn/db",
           description="d", owner="o", tags=["pii"])
    d.update(over)
    return d


def ingest_add(**over):
    d = op("add_node", INGEST, qualified_name="conn/db/x", type="schema", parent="conn/db")
    d.update(over)
    return d


def edit(**over):
    d = op("edit_node", op_id="op-0100", node="conn/db/sc/t", set={"owner": "bob"})
    d.update(over)
    return d


def edge_op(kind, actor=HUMAN, **over):
    d = op(kind, actor, **{"from": "conn/db/sc/t", "to": "conn/db/sc/u", "type": "references"})
    d.update(over)
    return d


def without(d, key):
    return {k: v for k, v in d.items() if k != key}


# ---- domain values ----

def test_normalise_tags():
    assert normalise_tags(["b", "a", "b"]) == ("a", "b")
    assert normalise_tags([]) == ()
    assert normalise_tags([" A", "a", ""]) == ("", " A", "a")


def test_domain_values_frozen():
    n = Node("i", "q", "table", "d", "o", ("a",), None)
    e = Edge(1, "a", "b", "contains", None)
    a = Actor("human", "ann")
    for v, attr in ((n, "owner"), (e, "type"), (a, "name")):
        with pytest.raises(AttributeError):
            setattr(v, attr, "x")


def test_canonical():
    assert canonical({"b": 1, "a": ["é", 2]}) == '{"a":["é",2],"b":1}'


# ---- parse: happy paths ----

def test_parse_each_kind():
    a = parse_op(human_add(tags=["pii", "gold", "pii"]))
    assert isinstance(a, AddNode) and a.kind == "add_node"
    assert (a.op_id, a.actor, a.qualified_name, a.type, a.parent) == \
        ("op-0001", Actor("human", "ann"), "conn/db/x", "schema", "conn/db")
    assert (a.description, a.owner, a.tags) == ("d", "o", ("gold", "pii"))

    i = parse_op(ingest_add())
    assert isinstance(i, AddNode) and i.actor == Actor("ingest", "ingest-crawler")
    assert (i.description, i.owner, i.tags) == (None, None, None)

    e = parse_op(edit(set={"tags": ["b", "a"], "description": "x"}))
    assert isinstance(e, EditNode) and e.kind == "edit_node"
    assert e.node == "conn/db/sc/t" and e.set == {"tags": ("a", "b"), "description": "x"}

    dn = parse_op(op("delete_node", node="conn/db/sc"))
    assert isinstance(dn, DeleteNode) and dn.node == "conn/db/sc" and dn.kind == "delete_node"

    for kind, cls in (("add_edge", AddEdge), ("delete_edge", DeleteEdge)):
        x = parse_op(edge_op(kind))
        assert isinstance(x, cls) and x.kind == kind
        assert (x.from_, x.to, x.type) == ("conn/db/sc/t", "conn/db/sc/u", "references")
    ie = parse_op(edge_op("add_edge", INGEST, type="contains"))
    assert ie.actor.kind == "ingest" and ie.type == "contains"


def test_raw_kept_as_received():
    d = human_add(tags=["pii", "gold", "pii"])
    before = copy.deepcopy(d)
    a = parse_op(d)
    assert a.raw == before and d == before
    assert canonical(a.raw) == canonical(before)


def test_op_id_long_form():
    assert parse_op(op("delete_node", op_id="op-123456", node="a")).op_id == "op-123456"


# ---- errors ----

def test_error_str():
    e = NodeNotFound.for_field("op-0007", "node", "a/b")
    assert str(e) == "op-0007 node_not_found: node 'a/b' is not a live node"
    assert (e.op_id, e.code, e.message) == ("op-0007", "node_not_found", "node 'a/b' is not a live node")
    assert str(ActorNotAllowed.for_op("op-0001", "delete_edge")) == \
        "op-0001 actor_not_allowed: ingest may not delete_edge"
    classes = [c for c in vars(errors).values()
               if isinstance(c, type) and issubclass(c, OpError) and c is not OpError]
    assert len(classes) == 10
    codes = [c.code for c in classes]
    assert len(set(codes)) == len(codes)
    assert set(codes) == {"malformed", "actor_not_allowed", "node_not_found", "name_taken", "type_order",
                          "self_loop", "duplicate_edge", "already_has_parent", "edge_not_found",
                          "op_id_conflict"}


def test_error_without_op_id():
    e = MalformedOp(None, "line is not a JSON object")
    assert e.op_id is None
    assert str(e) == "malformed: line is not a JSON object"


# ---- parse: rejections ----

REJECTS = {
    "not_a_dict": ([1], MalformedOp),
    "missing_op_id": (without(op("delete_node", node="a"), "op_id"), MalformedOp),
    "op_id_7": (op("delete_node", op_id="7", node="a"), MalformedOp),
    "op_id_short": (op("delete_node", op_id="op-001", node="a"), MalformedOp),
    "op_id_trailing_newline": (op("delete_node", op_id="op-0001\n", node="a"), MalformedOp),
    "op_id_not_string": (op("delete_node", op_id=1, node="a"), MalformedOp),
    "actor_not_dict": (op("delete_node", actor="ann", node="a"), MalformedOp),
    "actor_missing_name": (op("delete_node", actor={"kind": "human"}, node="a"), MalformedOp),
    "actor_extra_key": (op("delete_node", actor={**HUMAN, "x": 1}, node="a"), MalformedOp),
    "actor_name_not_string": (op("delete_node", actor={"kind": "human", "name": 3}, node="a"), MalformedOp),
    "unknown_actor_kind": (op("delete_node", actor={"kind": "robot", "name": "r"}, node="a"), MalformedOp),
    "unknown_op": (op("rename_node", node="a"), MalformedOp),
    "missing_op": (without(op("delete_node", node="a"), "op"), MalformedOp),
    "missing_field": (without(human_add(), "owner"), MalformedOp),
    "extra_field": (human_add(colour="red"), MalformedOp),
    "non_string_field": (human_add(owner=7), MalformedOp),
    "tags_not_list": (human_add(tags="pii"), MalformedOp),
    "tag_not_string": (human_add(tags=[1]), MalformedOp),
    "unknown_node_type": (human_add(type="view"), MalformedOp),
    "unknown_edge_type": (edge_op("add_edge", type="likes"), MalformedOp),
    "edit_empty_set": (edit(set={}), MalformedOp),
    "edit_unknown_key": (edit(set={"colour": "red"}), MalformedOp),
    "ingest_edit_node": (edit(actor=INGEST), ActorNotAllowed),
    "ingest_delete_node": (op("delete_node", INGEST, node="a"), ActorNotAllowed),
    "ingest_delete_edge": (edge_op("delete_edge", INGEST), ActorNotAllowed),
    "ingest_add_node_owner": (ingest_add(owner="o"), ActorNotAllowed),
    "lone_surrogate": (op("delete_node", node="\ud800"), MalformedOp),
}


@pytest.mark.parametrize("case", sorted(REJECTS))
def test_parse_rejects(case):
    d, cls = REJECTS[case]
    with pytest.raises(cls) as ei:
        parse_op(d)
    e = ei.value
    assert type(e) is cls
    has_op_id = isinstance(d, dict) and isinstance(d.get("op_id"), str)
    assert e.op_id == (d["op_id"] if has_op_id else None)
    assert str(e) == (f"{e.op_id} {e.code}: {e.message}" if has_op_id else f"{e.code}: {e.message}")


def test_each_rejection_has_its_own_message():
    msgs = {}
    for case, (d, _) in REJECTS.items():
        with pytest.raises(OpError) as ei:
            parse_op(d)
        msgs[case] = ei.value.message
    # cases that are the same mistake may share text; everything else must be distinguishable
    assert len(set(msgs.values())) >= len(msgs) - 6


def test_check_order_actor_before_keys():
    # ingest + forbidden op wins over every later check, whatever the keys
    with pytest.raises(ActorNotAllowed):
        parse_op({"op_id": "op-0001", "actor": INGEST, "op": "edit_node", "bogus": 1})
    # but a bad op_id is reported before the actor
    with pytest.raises(MalformedOp):
        parse_op({"op_id": "x", "actor": INGEST, "op": "edit_node"})


# ======================================================================
# parse tests requested by op plans
# ======================================================================

# ---- add_node (docs/superpowers/plans/2026-11-19-op-add-node.md, "Parse-level cases") ----

@pytest.mark.parametrize("make", [human_add, ingest_add], ids=["human", "ingest"])
def test_parse_add_node_missing_parent(make):
    with pytest.raises(MalformedOp):
        parse_op(without(make(), "parent"))


@pytest.mark.parametrize("make", [human_add, ingest_add], ids=["human", "ingest"])
def test_parse_add_node_null_parent(make):
    with pytest.raises(MalformedOp):
        parse_op(make(parent=None))


@pytest.mark.parametrize("field", ["description", "owner", "tags"])
def test_parse_add_node_human_missing_authored(field):
    with pytest.raises(MalformedOp):
        parse_op(without(human_add(), field))


@pytest.mark.parametrize("field,value", [("description", "d"), ("owner", "o"), ("tags", [])])
def test_parse_add_node_ingest_authored_field(field, value):
    with pytest.raises(ActorNotAllowed) as ei:
        parse_op(ingest_add(**{field: value}))
    assert str(ei.value) == f"op-0001 actor_not_allowed: ingest may not set {field}"


def test_parse_add_node_ingest_several_authored_fields():
    with pytest.raises(ActorNotAllowed) as ei:
        parse_op(ingest_add(tags=[], owner="o"))
    assert ei.value.message == "ingest may not set owner, tags"


@pytest.mark.parametrize("make", [human_add, ingest_add], ids=["human", "ingest"])
def test_parse_add_node_type_view(make):
    with pytest.raises(MalformedOp):
        parse_op(make(type="view"))


@pytest.mark.parametrize("tags", ["pii", [1]])
def test_parse_add_node_bad_tags(tags):
    with pytest.raises(MalformedOp):
        parse_op(human_add(tags=tags))


@pytest.mark.parametrize("make", [human_add, ingest_add], ids=["human", "ingest"])
def test_parse_add_node_extra_key(make):
    with pytest.raises(MalformedOp):
        parse_op(make(colour="red"))


def test_parse_add_node_op_id_7():
    with pytest.raises(MalformedOp):
        parse_op(human_add(op_id="7"))


def test_parse_add_node_normalises_tags():
    d = human_add(tags=["pii", "gold", "pii"])
    a = parse_op(d)
    assert a.tags == ("gold", "pii")
    assert d["tags"] == ["pii", "gold", "pii"]


# ---- edit_node (docs/superpowers/plans/2026-11-19-op-edit-node.md, "Foundation parse tests") ----

def test_parse_edit_empty_set():
    with pytest.raises(MalformedOp) as ei:
        parse_op(edit(set={}))
    assert str(ei.value) == "op-0100 malformed: set must be a non-empty object"


def test_parse_edit_set_not_object():
    with pytest.raises(MalformedOp) as ei:
        parse_op(edit(set=["owner"]))
    assert str(ei.value) == "op-0100 malformed: set must be a non-empty object"


@pytest.mark.parametrize("key", ["colour", "qualified_name", "type"])
def test_parse_edit_unknown_key(key):
    with pytest.raises(MalformedOp) as ei:
        parse_op(edit(set={key: "red"}))
    assert str(ei.value) == f"op-0100 malformed: set has unknown key {key!r}"


@pytest.mark.parametrize("value", [7, None])
def test_parse_edit_owner_not_string(value):
    with pytest.raises(MalformedOp) as ei:
        parse_op(edit(set={"owner": value}))
    assert str(ei.value) == "op-0100 malformed: set.owner must be a string"


def test_parse_edit_description_not_string():
    with pytest.raises(MalformedOp) as ei:
        parse_op(edit(set={"description": ["x"]}))
    assert str(ei.value) == "op-0100 malformed: set.description must be a string"


def test_parse_edit_tags_not_list():
    with pytest.raises(MalformedOp) as ei:
        parse_op(edit(set={"tags": "pii"}))
    assert str(ei.value) == "op-0100 malformed: set.tags must be a list of strings"


def test_parse_edit_tags_non_string_item():
    with pytest.raises(MalformedOp) as ei:
        parse_op(edit(set={"tags": ["a", 1]}))
    assert str(ei.value) == "op-0100 malformed: set.tags must be a list of strings"


def test_parse_edit_ingest_actor():
    # The edit_node spec proposed "edit_node is human only; actor kind is 'ingest'"; the foundation's
    # shared convention (ActorNotAllowed.for_op) is binding: "ingest may not edit_node".
    with pytest.raises(ActorNotAllowed) as ei:
        parse_op(edit(actor=INGEST))
    assert str(ei.value) == "op-0100 actor_not_allowed: ingest may not edit_node"


def test_parse_edit_extra_top_key():
    with pytest.raises(MalformedOp):
        parse_op(edit(parent="conn/db"))
    with pytest.raises(MalformedOp):
        parse_op(without(edit(), "node"))


def test_parse_edit_normalises_tags():
    d = edit(set={"tags": ["b", "a", "b"]})
    e = parse_op(d)
    assert e.set["tags"] == ("a", "b")
    assert d["set"]["tags"] == ["b", "a", "b"]
    assert e.raw == d


def test_parse_edit_empty_tags_and_unicode_kept():
    e = parse_op(edit(set={"tags": [], "description": "", "owner": "zoë "}))
    assert e.set == {"tags": (), "description": "", "owner": "zoë "}


# ---- delete_node (docs/superpowers/plans/2026-11-19-op-delete-node.md, "Parse tests requested") ----

def test_parse_delete_node_ingest_rejected():
    with pytest.raises(ActorNotAllowed) as ei:
        parse_op({"op_id": "op-0001", "actor": INGEST, "op": "delete_node", "node": "a"})
    assert str(ei.value).startswith("op-0001 actor_not_allowed: ")


def test_parse_delete_node_extra_key():
    with pytest.raises(MalformedOp):
        parse_op(op("delete_node", node="a", cascade=True))


def test_parse_delete_node_missing_node():
    with pytest.raises(MalformedOp):
        parse_op(op("delete_node"))


def test_parse_delete_node_node_not_string():
    with pytest.raises(MalformedOp):
        parse_op(op("delete_node", node=7))


def test_parse_delete_node_ok():
    d = op("delete_node", node="a")
    assert parse_op(d) == DeleteNode("op-0001", Actor("human", "ann"), d, node="a")


# ---- add_edge (docs/superpowers/plans/2026-11-19-op-add-edge.md, "Foundation parse tests") ----

def test_parse_add_edge_human_ok():
    d = edge_op("add_edge")
    x = parse_op(d)
    assert x == AddEdge("op-0001", Actor("human", "ann"), d, from_="conn/db/sc/t", to="conn/db/sc/u",
                        type="references")


def test_parse_add_edge_ingest_ok():
    x = parse_op(edge_op("add_edge", INGEST))
    assert isinstance(x, AddEdge) and x.actor.kind == "ingest"


def test_parse_add_edge_unknown_type():
    with pytest.raises(MalformedOp):
        parse_op(edge_op("add_edge", type="likes"))


@pytest.mark.parametrize("key", ["from", "to", "type"])
def test_parse_add_edge_missing_key(key):
    with pytest.raises(MalformedOp):
        parse_op(without(edge_op("add_edge"), key))


def test_parse_add_edge_extra_key():
    with pytest.raises(MalformedOp):
        parse_op(edge_op("add_edge", tags=[]))


def test_parse_add_edge_non_string_endpoint():
    with pytest.raises(MalformedOp):
        parse_op(edge_op("add_edge", **{"from": 7}))


# ---- delete_edge (docs/superpowers/plans/2026-11-19-op-delete-edge.md, Task 4) ----

def test_parse_delete_edge_ingest_actor_not_allowed():
    with pytest.raises(ActorNotAllowed) as ei:
        parse_op(edge_op("delete_edge", INGEST))
    assert str(ei.value) == "op-0001 actor_not_allowed: ingest may not delete_edge"


def test_parse_delete_edge_ingest_with_bad_keys_still_actor_not_allowed():
    with pytest.raises(ActorNotAllowed):
        parse_op(without(edge_op("delete_edge", INGEST), "to"))


def test_parse_delete_edge_unknown_type():
    with pytest.raises(MalformedOp):
        parse_op(edge_op("delete_edge", type="likes"))


@pytest.mark.parametrize("key", ["from", "to", "type"])
def test_parse_delete_edge_missing_key(key):
    with pytest.raises(MalformedOp):
        parse_op(without(edge_op("delete_edge"), key))


def test_parse_delete_edge_extra_key():
    with pytest.raises(MalformedOp):
        parse_op(edge_op("delete_edge", description="x"))


def test_parse_delete_edge_non_string_endpoint():
    with pytest.raises(MalformedOp):
        parse_op(edge_op("delete_edge", **{"from": 7}))


def test_parse_delete_edge_ok():
    d = edge_op("delete_edge")
    x = parse_op(d)
    assert isinstance(x, DeleteEdge)
    assert (x.from_, x.to, x.type, x.raw) == ("conn/db/sc/t", "conn/db/sc/u", "references", d)


# ---- every op line of the real fixture parses ----

def test_fixture_ops_parse():
    from graph_undo.fixture.ops import generate_ops
    from graph_undo.fixture.seed import generate_seed

    ops, _ = generate_ops(42, *generate_seed(42))
    assert len(ops) == 309
    kinds = {parse_op(json.loads(json.dumps(d))).kind for d in ops}
    assert kinds == {"add_node", "edit_node", "delete_node", "add_edge", "delete_edge"}
