from checker.helpers import dump_text, node
from undo_checker.invariants import guard, inv3_unique, inv5_effective_tags, inv6_idempotent, inv8_timing
from undo_checker.model import parse_dump

DB = node("db", "c/d", "database", tags=["gold"])
S = node("s", "c/d/s", "schema", eff=["gold"])
T = node("t", "c/d/s/t", "table", tags=["pii"], eff=["gold", "pii"])
C = node("col", "c/d/s/t/x", "column", eff=["gold", "pii"])
X = node("x", "c/d/s2", "schema", eff=["gold"])
EDGES = [("db", "s", "contains"), ("s", "t", "contains"), ("t", "col", "contains"),
         ("db", "x", "contains"), ("x", "t", "references")]


def d(nodes, edges):
    return parse_dump(dump_text(nodes, edges))


D0 = d([DB, S, T, C, X], EDGES)
D1 = d([DB, X], [("db", "x", "contains")])


def test_guard_ok():
    assert guard(D0, D1, "c/d/s") == []


def test_guard_extra_node_in_d1():
    v = guard(D0, d([DB, X, node("n", "c/new")], [("db", "x", "contains")]), "c/d/s")
    assert any("n" in s and "added" in s for s in v)


def test_guard_changed_owner_in_d1():
    x2 = dict(X, owner="eve")
    v = guard(D0, d([DB, x2], [("db", "x", "contains")]), "c/d/s")
    assert any("x" in s and "changed" in s for s in v)


def test_guard_ignores_effective_tags_change():
    x2 = dict(X, effective_tags=["gold", "other"])
    assert guard(D0, d([DB, x2], [("db", "x", "contains")]), "c/d/s") == []


def test_guard_partial_subtree():
    v = guard(D0, d([DB, X, C], [("db", "x", "contains")]), "c/d/s")
    assert any("col" in s for s in v)


def test_guard_extra_edge_removed():
    v = guard(D0, d([DB, X], []), "c/d/s")
    assert any("db" in s and "x" in s for s in v)


def test_guard_unknown_target():
    v = guard(D0, D1, "c/d/nope")
    assert v and "c/d/nope" in v[0]


def test_inv3_pass():
    r = inv3_unique(D0, D0)
    assert r.status == "PASS"


def test_inv3_dup_in_d4_fails():
    dup = node("s2", "c/d/s", "schema", eff=["gold"])
    r = inv3_unique(D0, d([DB, S, dup], [("db", "s", "contains"), ("db", "s2", "contains")]))
    assert r.status == "FAIL"
    assert r.violations == ("D4: name 'c/d/s' held by s, s2",)


def test_inv5_pass():
    r = inv5_effective_tags(D0, D0)
    assert r.status == "PASS" and "5 nodes" in r.summary


def test_inv5_wrong_effective_tags():
    bad = dict(C, effective_tags=["pii"])
    r = inv5_effective_tags(d([DB, S, T, bad, X], EDGES), D0)
    assert r.status == "FAIL"
    (v,) = r.violations
    assert v.startswith("D3:") and "col" in v and "gold" in v


def test_inv5_dangling_edge():
    r = inv5_effective_tags(D0, d([DB], [("db", "ghost", "references")]))
    assert r.status == "FAIL" and any(v.startswith("D4:") and "ghost" in v for v in r.violations)


def test_inv5_two_parents():
    r = inv5_effective_tags(d([DB, S, X, T], [("db", "s", "contains"), ("db", "x", "contains"),
                                             ("s", "t", "contains"), ("x", "t", "contains")]), D0)
    assert r.status == "FAIL" and any("t" in v and "parent" in v for v in r.violations)


def test_inv5_cycle():
    a, b = node("a", "a", eff=[]), node("b", "b", eff=[])
    r = inv5_effective_tags(d([a, b], [("a", "b", "contains"), ("b", "a", "contains")]), D0)
    assert r.status == "FAIL" and any("cycle" in v for v in r.violations)


def test_inv6_equal():
    assert inv6_idempotent(D0.text, D0.text).status == "PASS"


def test_inv6_diff_shows_line():
    other = D0.text.replace('"owner":""', '"owner":"zed"', 1)
    r = inv6_idempotent(D0.text, other)
    assert r.status == "FAIL" and r.violations[0].startswith("line 1:")


def test_inv8_under():
    r = inv8_timing(59.9, 70.0)
    assert r.status == "PASS" and r.summary == "scenario 59.9 s (checker total 70.0 s)"


def test_inv8_over():
    assert inv8_timing(60.0, 61.0).status == "FAIL"
