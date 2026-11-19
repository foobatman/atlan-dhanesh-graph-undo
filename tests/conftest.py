import pytest

from graph_undo.fixture.seed import generate_seed


# 42 is the default; the others include seeds the review found off-spec (6, 61, 101, 252).
SEEDS = [0, 1, 6, 7, 42, 61, 101, 252, 2**64]


@pytest.fixture(scope="module", params=SEEDS, ids=lambda s: f"seed{s}")
def graph(request):
    return generate_seed(request.param)


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
