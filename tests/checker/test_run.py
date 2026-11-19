import json

import pytest

from checker.conftest import needs_undo
from undo_checker.invariants import guard
from undo_checker.model import parse_dump, removed
from undo_checker.run import RunError, finish, prepare, scenario_seconds, slice_ops


def test_slice_finds_target(fixture42):
    _, ops = fixture42
    t = slice_ops(ops, "op-0001")
    first = json.loads(ops.read_text(encoding="utf-8").splitlines()[0])
    assert t.op_id == "op-0001" and t.pre == []
    assert t.qualified_name == first["node"]
    assert len(t.ops_lines) == 309


def test_slice_unknown_op(fixture42):
    _, ops = fixture42
    with pytest.raises(RunError) as e:
        slice_ops(ops, "op-9999")
    assert str(e.value) == f"op 'op-9999' not found in {ops}"


def test_slice_not_delete_node(fixture42):
    _, ops = fixture42
    kind = json.loads(ops.read_text(encoding="utf-8").splitlines()[1])["op"]
    assert kind != "delete_node"
    with pytest.raises(RunError) as e:
        slice_ops(ops, "op-0002")
    assert str(e.value) == f"only delete_node ops can be checked (op-0002 is {kind})"


def test_slice_bad_json(tmp_path):
    ops = tmp_path / "ops.jsonl"
    ops.write_text('{"op_id":"op-0001"}\n{oops\n', encoding="utf-8")
    with pytest.raises(RunError) as e:
        slice_ops(ops, "op-0001")
    assert str(e.value) == f"{ops}:2: not JSON"


def test_slice_blank_lines_and_unicode(tmp_path):
    a = '{"actor":{"kind":"human","name":"ü"},"op":"edit_node","op_id":"op-0001","node":"c/ü","set":{"owner":"ü"}}'
    b = '{"actor":{"kind":"human","name":"x"},"op":"edit_node","op_id":"op-0002","node":"c/d","set":{"owner":"y"}}'
    c = '{"actor":{"kind":"human","name":"x"},"node":"c/ü","op":"delete_node","op_id":"op-0003"}'
    ops = tmp_path / "ops.jsonl"
    ops.write_text(f"{a}\n\n{b}\n{c}\n", encoding="utf-8")
    t = slice_ops(ops, "op-0003")
    assert t.pre == [a, b] and t.line == c and t.qualified_name == "c/ü"
    assert [o["op_id"] for o in t.ops_lines] == ["op-0001", "op-0002", "op-0003"]


def test_prepare_seed42_first_op(fixture42, tmp_path):
    seed, ops = fixture42
    t = slice_ops(ops, "op-0001")
    art = prepare(seed, ops, t, tmp_path)
    assert 2 not in art.timings and {1, 4, 6} <= art.timings.keys()
    d0, d1 = parse_dump(art.d0), parse_dump(art.d1)
    assert guard(d0, d1, t.qualified_name) == []
    rem = removed(d0, d1)
    assert (len(rem.nodes), len(rem.edges)) == (614, 774)
    assert (tmp_path / "D2.jsonl").read_text(encoding="utf-8") == art.d2
    assert (tmp_path / "ops_target.jsonl").read_text(encoding="utf-8") == t.line + "\n"


def test_prepare_bad_seed_raises(fixture42, tmp_path):
    _, ops = fixture42
    seed = tmp_path / "empty.jsonl"
    seed.write_text("not a seed\n", encoding="utf-8")
    with pytest.raises(RunError, match=r"step 1 \(load\)"):
        prepare(seed, ops, slice_ops(ops, "op-0001"), tmp_path / "w")


@needs_undo
def test_finish_seed42(fixture42, tmp_path):
    seed, ops = fixture42
    art = finish(prepare(seed, ops, slice_ops(ops, "op-0001"), tmp_path), "op-0001")
    assert art.d3 and art.d4 and art.report
    assert scenario_seconds(art) < 60
