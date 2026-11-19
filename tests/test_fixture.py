import re
import uuid

from conftest import by_id, children, subtree_size

NODE_FIELDS = {"id", "qualified_name", "type", "description", "owner", "tags"}
EDGE_FIELDS = {"from_id", "to_id", "type"}


def test_exact_fields(graph):
    nodes, edges = graph
    assert all(set(n) == NODE_FIELDS for n in nodes)
    assert all(set(e) == EDGE_FIELDS for e in edges)
    assert {e["type"] for e in edges} <= {"contains", "references"}
    assert {n["type"] for n in nodes} == {"connection", "database", "schema", "table", "column"}


def test_unique_ids_and_names(graph):
    nodes, _ = graph
    assert len({n["id"] for n in nodes}) == len(nodes)
    assert len({n["qualified_name"] for n in nodes}) == len(nodes)


def test_ids_are_uuids(graph):
    nodes, _ = graph
    for n in nodes:
        assert str(uuid.UUID(n["id"])) == n["id"]


def test_contains_is_tree(graph):
    nodes, edges = graph
    ids = by_id(nodes)
    contains = [e for e in edges if e["type"] == "contains"]
    child_ids = [e["to_id"] for e in contains]
    assert len(child_ids) == len(set(child_ids))  # one parent each
    roots = set(ids) - set(child_ids)
    assert {ids[r]["type"] for r in roots} == {"connection"} and len(roots) == 2
    assert len(contains) == len(nodes) - 2
    for e in contains:  # path rule, also rules out cycles
        p, c = ids[e["from_id"]], ids[e["to_id"]]
        assert c["qualified_name"].rsplit("/", 1)[0] == p["qualified_name"]


def test_level_order(graph):
    nodes, edges = graph
    ids, order = by_id(nodes), ["connection", "database", "schema", "table", "column"]
    for e in edges:
        if e["type"] == "contains":
            assert order.index(ids[e["to_id"]]["type"]) == order.index(ids[e["from_id"]]["type"]) + 1


def test_name_components_clean(graph):
    nodes, _ = graph
    for n in nodes:
        assert all(re.fullmatch(r"[a-z0-9_]+", part) for part in n["qualified_name"].split("/"))


def test_nodes_in_preorder(graph):
    nodes, edges = graph
    pos = {n["id"]: i for i, n in enumerate(nodes)}
    for e in edges:
        if e["type"] == "contains":
            assert pos[e["from_id"]] < pos[e["to_id"]]
