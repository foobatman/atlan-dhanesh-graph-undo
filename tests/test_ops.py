import pytest
SEEDS = [42]  # default seed only (partner: seed variety not important)
from replay import apply, load, replay

from graph_undo.fixture import generate_seed
from graph_undo.ops_fixture import generate_ops

COMMON = {"op_id", "actor", "op"}
FIELDS = {
    ("add_node", "ingest"): COMMON | {"qualified_name", "type", "parent"},
    ("add_node", "human"): COMMON | {"qualified_name", "type", "parent", "description", "owner", "tags"},
    ("edit_node", "human"): COMMON | {"node", "set"},
    ("delete_node", "human"): COMMON | {"node"},
    ("add_edge", "human"): COMMON | {"from", "to", "type"},
    ("add_edge", "ingest"): COMMON | {"from", "to", "type"},
    ("delete_edge", "human"): COMMON | {"from", "to", "type"},
}


@pytest.fixture(scope="module", params=SEEDS, ids=lambda s: f"seed{s}")
def case(request):
    nodes, edges = generate_seed(request.param)
    ops, manifest = generate_ops(request.param, nodes, edges)
    return nodes, edges, ops, manifest


def _deleted(case):
    nodes, edges, ops, _ = case
    s = load(nodes, edges)
    return s, set(s.subtree(ops[0]["node"]))


def test_op_fields(case):
    for op in case[2]:
        assert set(op) == FIELDS[(op["op"], op["actor"]["kind"])], op
        assert set(op["actor"]) == {"kind", "name"}
        if op["op"] == "edit_node":
            assert op["set"] and set(op["set"]) <= {"description", "owner", "tags"}
        for tags in (op.get("tags"), op.get("set", {}).get("tags")):
            if tags is not None:
                assert tags == sorted(set(tags))


def test_op_ids_and_first_delete(case):
    ops = case[2]
    assert [o["op_id"] for o in ops] == [f"op-{i:04d}" for i in range(1, len(ops) + 1)]
    assert ops[0]["op"] == "delete_node" and ops[0]["actor"]["kind"] == "human"
    assert len(_deleted(case)[1]) >= 500


def test_op_count(case):
    assert 290 <= len(case[2]) - 1 <= 320


def test_replay_valid(case):
    nodes, edges, ops, _ = case
    replay(nodes, edges, ops)


def test_recreated(case):
    nodes, edges, ops, _ = case
    s = load(nodes, edges)
    d = set(s.subtree(ops[0]["node"]))
    edits_on_recreated = 0
    for op in ops:
        if op["op"] == "edit_node":
            n = s.nodes[op["node"]]
            if n["qualified_name"] in d and n["id"] not in s.seed_ids:
                edits_on_recreated += 1
        apply(s, op)
    recreated = [q for q in d if q in s.nodes and s.nodes[q]["id"] not in s.seed_ids]
    assert len(recreated) >= 50
    assert edits_on_recreated >= 5


MINIMA = {"neighbour_edits": 15, "repoints": 20, "moves": 4, "ingest_recreate": 50,
          "ingest_retouch": 5, "ingest_edges": 3, "recreated_edits": 8, "late_parent_edit": 1,
          "E1": 1, "E2": 2, "E3": 1, "E4": 1, "E5": 1, "E6": 1, "E7": 1}


def test_cases_present(case):
    nodes, edges, ops, manifest = case
    for key, minimum in MINIMA.items():
        assert len(manifest.get(key, [])) >= minimum, key
    by_id = {o["op_id"]: o for o in ops}
    seed = load(nodes, edges)
    for key in ("E1", "E2"):
        add = [by_id[i] for i in manifest[key] if by_id[i]["op"] == "add_edge"][-1]
        assert (add["from"], add["to"]) in seed.refs  # X had a seed reference to T (same name)


def test_end_states(case):
    nodes, edges, ops, _ = case
    seed, d = _deleted(case)
    end = replay(nodes, edges, ops)
    root = ops[0]["node"]

    def held(q):
        return q in end.nodes

    def chain_free(q):
        while q != root:
            if held(q):
                return False
            q = seed.parent[q]
        return True  # R itself is excluded: its name is always recreated (state d)

    a = [q for q in d if q != root and chain_free(q)]
    b = [q for q in d if held(q)]
    c = [q for q in d if q != root and not held(q) and held(seed.parent[q])]
    assert a and b and c
    assert held(root) and seed.parent[root] in end.nodes  # (d)


def test_contains_moves_adjacent(case):
    ops = case[2]
    for i, op in enumerate(ops):
        if op["op"] == "delete_edge" and op["type"] == "contains":
            nxt = ops[i + 1]
            assert nxt["op"] == "add_edge" and nxt["type"] == "contains" and nxt["to"] == op["to"]


def test_repoint_pairs(case):
    ops = case[2]
    pairs = sum(1 for a, b in zip(ops, ops[1:])
                if a["op"] == "delete_edge" and b["op"] == "add_edge"
                and a["type"] == b["type"] == "references"
                and a["from"] == b["from"] and a["to"] != b["to"])
    assert pairs >= 10


def test_ops_deterministic():
    n, e = generate_seed(42)
    assert generate_ops(42, n, e) == generate_ops(42, n, e)
    n2, e2 = generate_seed(43)
    assert generate_ops(42, n, e)[0] != generate_ops(43, n2, e2)[0]
