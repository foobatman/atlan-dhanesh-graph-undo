"""The checker against the real tool on seed 42. Needs the undo entry point."""

import subprocess

import pytest

from checker.conftest import needs_undo
from checker.helpers import dump_text
from undo_checker import invariants as inv
from undo_checker.model import parse_dump, parse_report, removed
from undo_checker.run import finish, prepare, slice_ops

pytestmark = needs_undo


def test_check_seed42(fixture42, tmp_path):
    seed, ops = fixture42
    r = subprocess.run(["uv", "run", "check", "--seed", str(seed), "--ops", str(ops), "--workdir", str(tmp_path)],
                       capture_output=True, encoding="utf-8")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "7 SKIP" in r.stdout
    assert r.stdout.rstrip().endswith("check op-0001: 7 passed, 0 failed, 1 skipped")


@pytest.fixture(scope="module")
def real(fixture42, tmp_path_factory):
    seed, ops = fixture42
    t = slice_ops(ops, "op-0001")
    art = finish(prepare(seed, ops, t, tmp_path_factory.mktemp("real")), "op-0001")
    d0, d1, d2, d3 = (parse_dump(x) for x in (art.d0, art.d1, art.d2, art.d3))
    return t, art, d0, d2, d3, removed(d0, d1)


def rebuild(d, nodes):
    return parse_dump(dump_text(list(nodes.values()), d.edges))


def test_real_run_passes_all(real):
    t, art, d0, d2, d3, rem = real
    rep = parse_report(art.report)
    for r in (inv.inv1_frame(d2, d3, rem, t.ops_lines), inv.inv2_restored(d0, d2, d3, rem, rep),
              inv.inv3_unique(d3, d3), inv.inv4_report(rep, rem, d3, d2), inv.inv5_effective_tags(d3, d3)):
        assert r.status == "PASS", r.violations[:5]


def test_mutation_inv1_owner_changed(real):
    t, art, d0, d2, d3, rem = real
    i = sorted(d2.nodes)[0]
    nodes = dict(d3.nodes)
    nodes[i] = dict(nodes[i], owner=nodes[i]["owner"] + "-mut")
    assert inv.inv1_frame(d2, rebuild(d3, nodes), rem, t.ops_lines).status == "FAIL"


def test_mutation_inv2_restored_node_dropped(real):
    t, art, d0, d2, d3, rem = real
    restored = sorted(set(d3.nodes) - set(d2.nodes))
    assert restored
    gone = restored[0]
    nodes = {k: v for k, v in d3.nodes.items() if k != gone}
    edges = {e for e in d3.edges if gone not in e[:2]}
    mut = parse_dump(dump_text(list(nodes.values()), edges))
    assert inv.inv2_restored(d0, d2, mut, rem, parse_report(art.report)).status == "FAIL"


def test_mutation_inv3_duplicate_name(real):
    t, art, d0, d2, d3, rem = real
    n = next(iter(d3.nodes.values()))
    nodes = dict(d3.nodes, dup=dict(n, id="dup"))
    assert inv.inv3_unique(rebuild(d3, nodes), d3).status == "FAIL"


def test_mutation_inv4_report_line_dropped(real):
    t, art, d0, d2, d3, rem = real
    text = "".join(art.report.splitlines(keepends=True)[1:])
    assert inv.inv4_report(parse_report(text), rem, d3).status == "FAIL"


def test_mutation_inv5_effective_tags(real):
    t, art, d0, d2, d3, rem = real
    i = sorted(d3.nodes)[0]
    nodes = dict(d3.nodes)
    nodes[i] = dict(nodes[i], effective_tags=sorted(nodes[i]["effective_tags"] + ["zzz-mut"]))
    assert inv.inv5_effective_tags(rebuild(d3, nodes), d3).status == "FAIL"


def test_mutation_inv6(real):
    t, art, d0, d2, d3, rem = real
    lines = art.d3.splitlines(keepends=True)
    assert inv.inv6_idempotent(art.d3, "".join(lines[1:])).status == "FAIL"
