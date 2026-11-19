"""Pure undo planner: rule B2 (spec: undo-design, "Semantics"). No database."""

import json

import pytest

from graph_undo.domain.model import Edge, Node
from graph_undo.domain.ops import canonical
from graph_undo.domain.undo import UndoInputs, plan_undo, report_lines

X = "op-0001"


def N(i, qn, type_, deleted_by=X):
    return Node(i, qn, type_, f"desc {i}", "o", (), deleted_by)


def E(i, f, t, type_="contains", deleted_by=X):
    return Edge(i, f, t, type_, deleted_by)


def inputs(nodes, edges, others=(), holders=(), creators=None, live_triples=(), live_parents=None,
           root="core", op="delete_node"):
    return UndoInputs(op_id=X, op=op, root_id=root, removed_nodes={n.id: n for n in nodes},
                      removed_edges=list(edges), others={n.id: n for n in others},
                      holders={h.qualified_name: h for h in holders}, creators=creators or {},
                      live_triples=set(live_triples), live_parents=live_parents or {})


def outcomes(plan):
    nodes = {o.node.id: (o.outcome, o.reason_code, o.parent_id) for o in plan.nodes}
    edges = {o.edge.id: (o.outcome, o.reason_code, o.new_from_id) for o in plan.edges}
    return nodes, edges


def reasons(plan):
    return {**{o.node.id: o.reason for o in plan.nodes}, **{o.edge.id: o.reason for o in plan.edges}}


P = N("P", "pg/ops", "database", deleted_by=None)
CORE = N("core", "pg/ops/core", "schema")
ORDERS = N("orders", "pg/ops/core/orders", "table")
USERS = N("users", "pg/ops/core/users", "table")
ID = N("id", "pg/ops/core/orders/id", "column")
TOTAL = N("total", "pg/ops/core/orders/total", "column")
EMAIL = N("email", "pg/ops/core/users/email", "column")
TREE = [CORE, ORDERS, USERS, ID, TOTAL, EMAIL]
TREE_EDGES = [E(1, "P", "core"), E(2, "core", "orders"), E(3, "core", "users"), E(4, "orders", "id"),
              E(5, "orders", "total"), E(6, "users", "email")]
CRM = N("crm", "crm/acct", "table", deleted_by=None)
FIN = N("fin", "fin/ledger", "table", deleted_by="op-0207")
REFS = [E(7, "crm", "orders", "references"), E(8, "fin", "users", "references")]
CORE2 = N("core2", "pg/ops/core", "schema", deleted_by=None)
ORDERS2 = N("orders2", "pg/ops/core/orders", "table", deleted_by=None)
ID2 = N("id2", "pg/ops/core/orders/id", "column", deleted_by=None)


def test_no_conflict_restores_all():
    plan = plan_undo(inputs(TREE, TREE_EDGES, others=[P]))
    nodes, edges = outcomes(plan)
    assert all(o == "restored" for o, _, _ in nodes.values())
    assert nodes["core"][2] == "P" and nodes["total"][2] == "orders" and nodes["email"][2] == "users"
    assert all(o == ("restored", None, None) for o in edges.values())
    assert plan.nodes[0].node.id == "core"            # top-down: root first


def test_root_without_parent():
    c = N("c", "conn", "connection")
    d = N("d", "conn/db", "database")
    plan = plan_undo(inputs([c, d], [E(1, "c", "d")], root="c"))
    nodes, edges = outcomes(plan)
    assert nodes == {"c": ("restored", None, None), "d": ("restored", None, "c")}
    assert edges == {1: ("restored", None, None)}


def test_root_name_held():
    plan = plan_undo(inputs(TREE, TREE_EDGES, others=[P], holders=[CORE2],
                            creators={"core2": ("op-0142", "ingest")}))
    nodes, edges = outcomes(plan)
    assert nodes["core"] == ("skipped", "name_held", None)
    assert reasons(plan)["core"] == "name held by core2 created by op-0142 (ingest)"
    assert edges[1] == ("skipped", "endpoint_not_restored", None)
    assert reasons(plan)[1] == "child pg/ops/core was skipped (name_held)"


def test_seed_holder_reason():
    plan = plan_undo(inputs(TREE, TREE_EDGES, others=[P], holders=[CORE2]))
    assert reasons(plan)["core"] == "name held by core2 created by seed"


def test_three_level_cascade():
    plan = plan_undo(inputs(TREE, TREE_EDGES + REFS, others=[P, CRM, FIN], holders=[CORE2, ORDERS2, ID2],
                            creators={"core2": ("op-0142", "ingest"), "orders2": ("op-0143", "ingest"),
                                      "id2": ("op-0144", "ingest")}))
    nodes, edges = outcomes(plan)
    assert nodes == {
        "core": ("skipped", "name_held", None),
        "orders": ("skipped", "name_held", None),
        "id": ("skipped", "name_held", None),
        "total": ("restored", None, "orders2"),
        "users": ("restored", None, "core2"),
        "email": ("restored", None, "users"),
    }
    assert edges == {
        1: ("skipped", "endpoint_not_restored", None),
        2: ("skipped", "endpoint_not_restored", None),
        3: ("merged", "reparented", "core2"),
        4: ("skipped", "endpoint_not_restored", None),
        5: ("merged", "reparented", "orders2"),
        6: ("restored", None, None),
        7: ("skipped", "endpoint_not_restored", None),
        8: ("skipped", "endpoint_deleted", None),
    }
    r = reasons(plan)
    assert r[5] == "recreated as orders2 → total"
    assert r[3] == "recreated as core2 → users"
    assert r[7] == "endpoint pg/ops/core/orders was skipped (name_held)"
    assert r[8] == "endpoint fin/ledger was deleted by op-0207"
    assert r["users"] is None and r[6] is None


def test_holder_type_mismatch():
    wrong = N("w", "pg/ops/core/orders", "column", deleted_by=None)
    cell = N("cell", "pg/ops/core/orders/total/cell", "column")      # child of total
    plan = plan_undo(inputs(TREE + [cell], TREE_EDGES + [E(9, "total", "cell")], others=[P], holders=[wrong]))
    nodes, edges = outcomes(plan)
    assert nodes["orders"] == ("skipped", "name_held", None)
    assert nodes["total"] == ("skipped", "parent_type_mismatch", None)
    assert nodes["id"] == ("skipped", "parent_type_mismatch", None)
    assert nodes["cell"] == ("skipped", "no_live_parent", None)
    r = reasons(plan)
    assert r["total"] == "parent pg/ops/core/orders is held by w of type column, not table"
    assert r["cell"] == "parent pg/ops/core/orders/total was not restored (parent_type_mismatch)"
    assert edges[5] == ("skipped", "endpoint_not_restored", None)


def test_outside_parent_dead_with_holder():
    dead_p = N("P", "pg/ops", "database", deleted_by="op-0300")
    p2 = N("P2", "pg/ops", "database", deleted_by=None)
    plan = plan_undo(inputs(TREE, TREE_EDGES, others=[dead_p], holders=[p2]))
    nodes, edges = outcomes(plan)
    assert nodes["core"] == ("restored", None, "P2")
    assert edges[1] == ("merged", "reparented", "P2")


def test_outside_parent_dead_no_holder():
    dead_p = N("P", "pg/ops", "database", deleted_by="op-0300")
    plan = plan_undo(inputs(TREE, TREE_EDGES, others=[dead_p]))
    nodes, edges = outcomes(plan)
    assert nodes["core"] == ("skipped", "no_live_parent", None)
    assert reasons(plan)["core"] == "parent pg/ops was deleted by op-0300"
    assert nodes["orders"] == ("skipped", "no_live_parent", None)
    assert reasons(plan)["orders"] == "parent pg/ops/core was not restored (no_live_parent)"
    assert all(o[0] == "skipped" for o in edges.values())


def test_references_both_ends_live():
    a = N("a", "pg/ops/core/a", "table")
    plan = plan_undo(inputs([CORE, a], [E(1, "P", "core"), E(2, "core", "a"), E(3, "crm", "a", "references"),
                                        E(4, "a", "crm", "references")], others=[P, CRM]))
    _, edges = outcomes(plan)
    assert edges[3] == ("restored", None, None) and edges[4] == ("restored", None, None)


def test_delete_edge_undo():
    t = N("t", "s/t", "table", deleted_by=None)
    u = N("u", "s/u", "table", deleted_by=None)
    ref = E(10, "t", "u", "references")
    plan = plan_undo(inputs([], [ref], others=[t, u], root=None, op="delete_edge"))
    assert outcomes(plan)[1] == {10: ("restored", None, None)}

    plan = plan_undo(inputs([], [ref], others=[t, u], root=None, op="delete_edge",
                            live_triples=[("t", "u", "references")]))
    assert outcomes(plan)[1] == {10: ("skipped", "duplicate_edge", None)}
    assert reasons(plan)[10] == "a live references edge s/t → s/u already exists"

    s = N("s", "s", "schema", deleted_by=None)
    s2 = N("s2", "s2", "schema", deleted_by=None)
    plan = plan_undo(inputs([], [E(11, "s", "t")], others=[s, t, s2], root=None, op="delete_edge",
                            live_parents={"t": E(12, "s2", "t", deleted_by=None)}))
    assert outcomes(plan)[1] == {11: ("skipped", "child_has_parent", None)}
    assert reasons(plan)[11] == "s/t now has parent s2"

    gone = N("u", "s/u", "table", deleted_by="op-0050")
    plan = plan_undo(inputs([], [ref], others=[t, gone], root=None, op="delete_edge"))
    assert outcomes(plan)[1] == {10: ("skipped", "endpoint_deleted", None)}
    assert reasons(plan)[10] == "endpoint s/u was deleted by op-0050"


def test_unreachable_removed_node_is_a_bug():
    stray = N("stray", "x/stray", "table")
    with pytest.raises(ValueError):
        plan_undo(inputs(TREE + [stray], TREE_EDGES, others=[P]))


def test_every_item_once():
    plan = plan_undo(inputs(TREE, TREE_EDGES + REFS, others=[P, CRM, FIN], holders=[CORE2, ORDERS2, ID2]))
    assert sorted(o.node.id for o in plan.nodes) == sorted(n.id for n in TREE)
    assert sorted(o.edge.id for o in plan.edges) == list(range(1, 9))


def test_report_lines():
    plan = plan_undo(inputs(TREE, TREE_EDGES + REFS, others=[P, CRM, FIN], holders=[CORE2, ORDERS2, ID2],
                            creators={"core2": ("op-0142", "ingest")}))
    lines = report_lines(plan, {3: 100, 5: 101})
    assert all(line == canonical(json.loads(line)) + "\n" for line in lines)
    objs = [json.loads(line) for line in lines]
    assert [o["kind"] for o in objs] == ["node"] * 6 + ["edge"] * 8
    assert [o["qualified_name"] for o in objs[:6]] == sorted(n.qualified_name for n in TREE)
    assert [o["id"] for o in objs[6:]] == list(range(1, 9))
    by = {(o["kind"], o["id"]): o for o in objs}
    assert by[("node", "core")] == {"kind": "node", "id": "core", "op_id": X, "qualified_name": "pg/ops/core",
                                    "outcome": "skipped", "reason_code": "name_held",
                                    "reason": "name held by core2 created by op-0142 (ingest)"}
    assert by[("node", "users")] == {"kind": "node", "id": "users", "op_id": X,
                                     "qualified_name": "pg/ops/core/users", "outcome": "restored",
                                     "parent_id": "core2"}
    assert by[("edge", 5)] == {"kind": "edge", "id": 5, "op_id": X, "from_id": "orders", "to_id": "total",
                               "type": "contains", "outcome": "merged", "reason_code": "reparented",
                               "reason": "recreated as orders2 → total", "new_edge_id": 101}
    assert by[("edge", 6)] == {"kind": "edge", "id": 6, "op_id": X, "from_id": "users", "to_id": "email",
                               "type": "contains", "outcome": "restored"}
