"""Invariants 1, 2, 4 on a small hand-built undo scenario.

D0: db -> s (schema c/d/s) -> t1, t2; t1 -> c1; x (schema c/d/x) -> r; r -references-> t1.
The delete removes s's subtree. Before undo, ingest recreated s as s2 (same name and type)
and t2 as t2b; a human set r's owner. A correct undo skips s and t2, restores t1 under s2,
and restores c1 and the reference.
"""

from checker.helpers import dump_text, node, report_text
from undo_checker.invariants import human_ops_on, inv1_frame, inv2_restored, inv4_report
from undo_checker.model import parse_dump, parse_report, removed

DB = node("db", "c/d", "database")
S = node("s", "c/d/s", "schema", owner="ann")
T1 = node("t1", "c/d/s/t1", description="orders", owner="ann", tags=["pii"])
T2 = node("t2", "c/d/s/t2")
C1 = node("c1", "c/d/s/t1/id", "column", eff=["pii"])
X = node("x", "c/d/x", "schema")
R = node("r", "c/d/x/r")
S2 = node("s2", "c/d/s", "schema")
T2B = node("t2b", "c/d/s/t2")
R_EDITED = dict(R, owner="bob")

D0_EDGES = [("db", "s", "contains"), ("s", "t1", "contains"), ("s", "t2", "contains"),
            ("t1", "c1", "contains"), ("db", "x", "contains"), ("x", "r", "contains"),
            ("r", "t1", "references")]
KEPT = [("db", "x", "contains"), ("x", "r", "contains")]
D2_EDGES = KEPT + [("db", "s2", "contains"), ("s2", "t2b", "contains")]
RESTORED_EDGES = [("s2", "t1", "contains"), ("t1", "c1", "contains"), ("r", "t1", "references")]

OPS = [{"op_id": "op-0005", "actor": {"kind": "human", "name": "bob"}, "op": "edit_node",
        "node": "c/d/x/r", "set": {"owner": "bob"}},
       {"op_id": "op-0006", "actor": {"kind": "ingest", "name": "i"}, "op": "add_node",
        "qualified_name": "c/d/s", "type": "schema", "parent": "c/d"}]


def d(nodes, edges):
    return parse_dump(dump_text(nodes, edges))


D0 = d([DB, S, T1, T2, C1, X, R], D0_EDGES)
D1 = d([DB, X, R], KEPT)
D2 = d([DB, X, R_EDITED, S2, T2B], D2_EDGES)
D3 = d([DB, X, R_EDITED, S2, T2B, T1, C1], D2_EDGES + RESTORED_EDGES)
REM = removed(D0, D1)


def item(kind, outcome, reason=None, **kw):
    out = {"kind": kind, "op_id": "op-0001", "outcome": outcome, **kw}
    if reason:
        out["reason"], out["reason_code"] = reason, "x"
    return out


def edge(f, t, ty, outcome, reason=None):
    return item("edge", outcome, reason, id=1, from_id=f, to_id=t, type=ty)


GOOD = [item("node", "skipped", "name held by s2", id="s"),
        item("node", "restored", id="t1"),
        item("node", "skipped", "name held by t2b", id="t2"),
        item("node", "restored", id="c1"),
        edge("db", "s", "contains", "skipped", "child skipped"),
        edge("s", "t1", "contains", "merged", "recreated as s2 -> t1"),
        edge("s", "t2", "contains", "skipped", "child skipped"),
        edge("t1", "c1", "contains", "restored"),
        edge("r", "t1", "references", "restored")]


def rep(items):
    return parse_report(report_text(items))


# --- 1 ---

def test_human_ops_on():
    assert human_ops_on(OPS, "c/d/x/r") == ["op-0005"]
    assert human_ops_on(OPS, "c/d/s") == []


def test_inv1_pass():
    r = inv1_frame(D2, D3, REM, OPS)
    assert r.status == "PASS", r.violations
    assert r.summary == "frame: 5 nodes, 4 edges unchanged; 2 nodes, 3 edges added"


def test_inv1_field_changed():
    d3 = d([DB, X, R, S2, T2B, T1, C1], D2_EDGES + RESTORED_EDGES)  # r's owner reverted
    r = inv1_frame(D2, d3, REM, OPS)
    assert r.status == "FAIL"
    assert any("r" in v and "human ops on c/d/x/r: op-0005" in v for v in r.violations)


def test_inv1_edge_dropped():
    d3 = d([DB, X, R_EDITED, S2, T2B, T1, C1], KEPT + [("db", "s2", "contains")] + RESTORED_EDGES)
    r = inv1_frame(D2, d3, REM, OPS)
    assert r.status == "FAIL" and any("s2" in v and "t2b" in v for v in r.violations)


def test_inv1_foreign_node_added():
    d3 = d([DB, X, R_EDITED, S2, T2B, T1, C1, node("z", "c/d/x/z")], D2_EDGES + RESTORED_EDGES)
    r = inv1_frame(D2, d3, REM, OPS)
    assert r.status == "FAIL" and any("z" in v for v in r.violations)


def test_inv1_foreign_edge_added():
    d3 = d([DB, X, R_EDITED, S2, T2B, T1, C1], D2_EDGES + RESTORED_EDGES + [("r", "c1", "references")])
    r = inv1_frame(D2, d3, REM, OPS)
    assert r.status == "FAIL" and any("c1" in v and "references" in v for v in r.violations)


# --- 2 ---

def test_inv2_pass():
    r = inv2_restored(D0, D2, D3, REM, rep(GOOD))
    assert r.status == "PASS", r.violations
    assert r.summary == "2 eligible nodes restored intact"


def test_inv2_missing():
    d3 = d([DB, X, R_EDITED, S2, T2B, T1], D2_EDGES + [("s2", "t1", "contains"), ("r", "t1", "references")])
    r = inv2_restored(D0, D2, d3, REM, rep(GOOD))
    assert r.status == "FAIL" and any("c1" in v for v in r.violations)


def test_inv2_old_fields_wrong():
    d3 = d([DB, X, R_EDITED, S2, T2B, dict(T1, description="changed"), C1], D2_EDGES + RESTORED_EDGES)
    r = inv2_restored(D0, D2, d3, REM, rep(GOOD))
    assert r.status == "FAIL" and any("t1" in v for v in r.violations)


def test_inv2_wrong_parent():
    d3 = d([DB, X, R_EDITED, S2, T2B, T1, C1], D2_EDGES + RESTORED_EDGES[1:])
    r = inv2_restored(D0, D2, d3, REM, rep(GOOD))
    assert r.status == "FAIL" and any("t1" in v and "parent" in v for v in r.violations)


def test_inv2_report_not_restored():
    items = [dict(i, outcome="skipped", reason="no") if i.get("id") == "c1" else i for i in GOOD]
    r = inv2_restored(D0, D2, D3, REM, rep(items))
    assert r.status == "FAIL" and any("c1" in v and "report" in v for v in r.violations)


def test_inv2_type_mismatch_not_eligible():
    s2_table = dict(S2, type="table")
    d2 = d([DB, X, R_EDITED, s2_table, T2B], D2_EDGES)
    r = inv2_restored(D0, d2, d2, REM, rep(GOOD))
    assert r.status == "FAIL" and r.summary == "vacuous: no eligible removed node"


def test_inv2_vacuous():
    d2 = d([DB, X, R_EDITED], KEPT)
    d0 = d([DB, S, X, R], [("db", "s", "contains")] + KEPT)
    d2b = d([DB, X, R_EDITED, S2], KEPT + [("db", "s2", "contains")])
    r = inv2_restored(d0, d2b, d2b, removed(d0, d2), rep([]))
    assert r.status == "FAIL" and r.summary == "vacuous: no eligible removed node"


# --- 4 ---

def test_inv4_pass():
    r = inv4_report(rep(GOOD), REM, D3)
    assert r.status == "PASS", r.violations
    assert r.summary == "4 nodes, 5 edges, each reported once"


def test_inv4_missing_item():
    r = inv4_report(rep(GOOD[:-1]), REM, D3)
    assert r.status == "FAIL" and any("r" in v and "not reported" in v for v in r.violations)


def test_inv4_duplicate_item():
    r = inv4_report(rep(GOOD + [GOOD[1]]), REM, D3)
    assert r.status == "FAIL" and "node t1 reported 2 times" in r.violations


def test_inv4_extra_item():
    r = inv4_report(rep(GOOD + [item("node", "restored", id="zz")]), REM, D3)
    assert r.status == "FAIL" and any("zz" in v for v in r.violations)


def test_inv4_bad_outcome():
    items = [dict(GOOD[0], outcome="lost")] + GOOD[1:]
    r = inv4_report(rep(items), REM, D3)
    assert r.status == "FAIL" and any("lost" in v for v in r.violations)


def test_inv4_skipped_without_reason():
    items = [{k: v for k, v in GOOD[0].items() if k != "reason"}] + GOOD[1:]
    r = inv4_report(rep(items), REM, D3)
    assert r.status == "FAIL" and any("reason" in v for v in r.violations)


def test_inv4_restored_but_absent():
    d3 = d([DB, X, R_EDITED, S2, T2B, T1], D2_EDGES + [("s2", "t1", "contains"), ("r", "t1", "references")])
    r = inv4_report(rep(GOOD), REM, d3)
    assert r.status == "FAIL" and any("c1" in v for v in r.violations)


def test_inv4_skipped_but_present():
    d3 = d([DB, X, R_EDITED, S2, T2B, T1, C1, T2], D2_EDGES + RESTORED_EDGES)
    r = inv4_report(rep(GOOD), REM, d3)
    assert r.status == "FAIL" and any("t2" in v and "skipped" in v for v in r.violations)


def test_inv4_merged_child_without_parent():
    d3 = d([DB, X, R_EDITED, S2, T2B, T1, C1], D2_EDGES + RESTORED_EDGES[1:])
    r = inv4_report(rep(GOOD), REM, d3)
    assert r.status == "FAIL" and any("t1" in v and "merged" in v for v in r.violations)


def test_inv4_garbage_lines():
    empty = inv4_report(parse_report(""), REM, D3)
    assert empty.status == "FAIL"
    text = report_text(GOOD) + "nope\n" + "undid op-0001: restored=2\n"
    r = inv4_report(parse_report(text), REM, D3)
    assert r.status == "FAIL"
    assert any(v.startswith("report line 10:") for v in r.violations)
    assert any(v.startswith("report line 11:") for v in r.violations)
