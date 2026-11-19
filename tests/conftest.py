import pytest

from graph_undo.fixture import generate_seed


@pytest.fixture(scope="module")
def graph():
    return generate_seed(42)


def by_id(nodes):
    return {n["id"]: n for n in nodes}


def children(edges):
    out = {}
    for e in edges:
        if e["type"] == "contains":
            out.setdefault(e["from_id"], []).append(e["to_id"])
    return out


def subtree_size(node_id, ch):
    size, stack = 0, [node_id]
    while stack:
        i = stack.pop()
        size += 1
        stack.extend(ch.get(i, []))
    return size
