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


TAGS = {"pii", "gold", "silver", "finance", "marketing", "certified", "deprecated"}


def _schemas_by_size(graph):
    nodes, edges = graph
    ch = children(edges)
    return sorted((subtree_size(n["id"], ch), n["id"]) for n in nodes if n["type"] == "schema")


def test_big_schema(graph):
    sizes = _schemas_by_size(graph)
    assert sizes[-1][0] >= 550
    assert sizes[-2][0] < 500  # exactly one big schema


def test_node_totals(graph):
    nodes, _ = graph
    assert 2400 <= len(nodes) <= 2600


def test_tags_valid(graph):
    nodes, _ = graph
    for n in nodes:
        assert set(n["tags"]) <= TAGS and len(n["tags"]) <= 2
        assert n["tags"] == sorted(set(n["tags"]))


def test_inheritance_is_exercised(graph):
    nodes, _ = graph
    upper = [n for n in nodes if n["type"] in ("database", "schema")]
    assert sum(bool(n["tags"]) for n in upper) >= 0.2 * len(upper)


def test_redundant_inherited_tag_exists(graph):
    nodes, edges = graph
    ids = by_id(nodes)
    parent = {e["to_id"]: e["from_id"] for e in edges if e["type"] == "contains"}

    def ancestor_tags(i):
        out = set()
        while i in parent:
            i = parent[i]
            out |= set(ids[i]["tags"])
        return out

    assert any(set(n["tags"]) & ancestor_tags(n["id"]) for n in nodes)


def test_authored_fields_present(graph):
    nodes, _ = graph
    assert all(n["description"] and n["owner"] for n in nodes)
    assert len({n["owner"] for n in nodes}) >= 5


def _refs(graph):
    return [e for e in graph[1] if e["type"] == "references"]


def test_references_shape(graph):
    nodes, _ = graph
    ids, refs = by_id(nodes), _refs(graph)
    assert 250 <= len(refs) <= 350
    assert all(ids[e["from_id"]]["type"] == ids[e["to_id"]]["type"] == "table" for e in refs)
    assert all(e["from_id"] != e["to_id"] for e in refs)
    assert len({(e["from_id"], e["to_id"]) for e in refs}) == len(refs)


def test_references_cross_big_schema(graph):
    _, edges = graph
    big_id = _schemas_by_size(graph)[-1][1]
    inside, stack, ch = set(), [big_id], children(edges)
    while stack:
        i = stack.pop()
        inside.add(i)
        stack.extend(ch.get(i, []))
    refs = _refs(graph)
    inbound = [e for e in refs if e["to_id"] in inside and e["from_id"] not in inside]
    outbound = [e for e in refs if e["from_id"] in inside and e["to_id"] not in inside]
    assert len(inbound) + len(outbound) >= 60 and inbound and outbound


def test_edge_order(graph):
    nodes, edges = graph
    qn = {n["id"]: n["qualified_name"] for n in nodes}
    types = [e["type"] for e in edges]
    assert types == sorted(types)  # all contains, then all references
    keys = [(qn[e["from_id"]], qn[e["to_id"]]) for e in _refs(graph)]
    assert keys == sorted(keys)


def test_total_lines(graph):
    nodes, edges = graph
    assert 4500 <= len(nodes) + len(edges) <= 5500
