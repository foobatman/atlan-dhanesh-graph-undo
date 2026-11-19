"""Registry, apply_op and apply_file with fake handlers (spec: apply-foundation, "apply_op and apply_file")."""

import importlib
import json

import pytest

from apply.conftest import OP_KINDS, is_stub, live_names, log_row, op_dict, requires, run, snapshot
from graph_undo.api.apply import ApplyError, apply_file
from graph_undo.api.context import OpContext, OpResult
from graph_undo.api.registry import HANDLERS, apply_op
from graph_undo.api.resolve import require_live
from graph_undo.db.oplog import LoggedOp
from graph_undo.domain.errors import MalformedOp, NodeNotFound, OpError, OpIdConflict
from graph_undo.domain.model import Node
from graph_undo.domain.ops import canonical, parse_op


def fake_delete(ctx, op):
    """Stands in for delete_node: resolves the name, flags just that node, logs and records the removal."""
    n = require_live(ctx, "node", op.node)
    assert ctx.store.nodes.mark_deleted([n.id], op.op_id) == 1
    ctx.store.log.record(LoggedOp.of(op, "applied", node_id=n.id))
    ctx.store.log.add_removals(op.op_id, [n.id], [])
    return OpResult(op.op_id, "applied", node_id=n.id, removed_nodes=1)


def fake_edit(ctx, op):
    n = require_live(ctx, "node", op.node)
    ctx.store.nodes.update_fields(n.id, op.set)
    ctx.store.log.record(LoggedOp.of(op, "applied", node_id=n.id))
    return OpResult(op.op_id, "applied", node_id=n.id)


@pytest.fixture
def fakes(monkeypatch):
    monkeypatch.setitem(HANDLERS, "delete_node", fake_delete)
    monkeypatch.setitem(HANDLERS, "edit_node", fake_edit)


def edit_line(op_id, qn, **set_):
    return json.dumps(op_dict(op_id=op_id, op="edit_node", node=qn, set=set_ or {"owner": "x"}))


def write_ops(tmp_path, lines, name="ops.jsonl"):
    p = tmp_path / name
    p.write_text("".join(l + "\n" for l in lines))
    return p


def db_path(store):
    return store.con.execute("PRAGMA database_list").fetchone()[2]


# ---- registry ----

def test_all_five_kinds_registered():
    assert set(HANDLERS) == set(OP_KINDS)
    for kind in OP_KINDS:
        mod = importlib.import_module(f"graph_undo.api.{kind}")
        assert HANDLERS[kind] is mod.handle


def test_stub_handlers_raise_until_implemented(store):
    for kind in OP_KINDS:
        mod = importlib.import_module(f"graph_undo.api.{kind}")
        if not getattr(mod, "STUB", False):
            continue
        assert is_stub(kind)
        with pytest.raises(NotImplementedError, match=f"{kind} handler not implemented"):
            mod.handle(None, None)


def test_requires_skips_for_stubs():
    stubs = [k for k in OP_KINDS if is_stub(k)]
    if not stubs:
        pytest.skip("no stubs left")
    with pytest.raises(pytest.skip.Exception):
        requires(*stubs)


def test_requires_passes_for_registered_non_stub(monkeypatch):
    mod = importlib.import_module("graph_undo.api.add_edge")
    monkeypatch.setattr(mod, "STUB", False)
    requires("add_edge")           # no skip


# ---- apply_op ----

def test_apply_op_dispatches(store, monkeypatch):
    seen = []

    def handler(ctx, op):
        seen.append((type(ctx), ctx.store is store, ctx.op is op))
        ctx.store.log.record(LoggedOp.of(op, "applied"))
        return OpResult(op.op_id, "applied")

    monkeypatch.setitem(HANDLERS, "add_edge", handler)
    r = run(store, op="add_edge", **{"from": "conn/db/sc/u", "to": "conn/db/sc/t", "type": "references"})
    assert r == OpResult("op-0100", "applied")
    assert seen == [(OpContext, True, True)]
    assert log_row(store, "op-0100").op == "add_edge"


def test_apply_op_commits(store, fakes):
    run(store, op="delete_node", node="conn/db/sc/t/k")
    assert store.con.in_transaction is False
    assert "conn/db/sc/t/k" not in live_names(store)
    assert store.log.removals("op-0100") == (["k"], [])


def test_apply_op_rolls_back_on_error(store, monkeypatch):
    before = snapshot(store)

    def handler(ctx, op):
        ctx.store.nodes.insert(Node("x", "conn/x", "database", "", "", ()))
        ctx.store.edges.insert("c", "x", "contains")
        ctx.store.log.record(LoggedOp.of(op, "applied"))
        raise NodeNotFound.for_field(op.op_id, "node", "conn/nope")

    monkeypatch.setitem(HANDLERS, "delete_node", handler)
    with pytest.raises(NodeNotFound) as ei:
        run(store, op="delete_node", node="conn/nope")
    assert str(ei.value) == "op-0100 node_not_found: node 'conn/nope' is not a live node"
    assert snapshot(store) == before
    assert not store.con.in_transaction


def test_apply_op_rolls_back_on_bug(store, monkeypatch):
    before = snapshot(store)

    def handler(ctx, op):
        ctx.store.nodes.mark_deleted(["t"], op.op_id)
        raise ZeroDivisionError

    monkeypatch.setitem(HANDLERS, "delete_node", handler)
    with pytest.raises(ZeroDivisionError):
        run(store, op="delete_node", node="conn/db/sc/t")
    assert snapshot(store) == before


def test_handler_that_writes_no_log_row_is_a_bug(store, monkeypatch):
    before = snapshot(store)

    def handler(ctx, op):
        ctx.store.nodes.mark_deleted(["t"], op.op_id)
        return OpResult(op.op_id, "applied")

    monkeypatch.setitem(HANDLERS, "delete_node", handler)
    with pytest.raises(RuntimeError, match="op-log row"):
        run(store, op="delete_node", node="conn/db/sc/t")
    assert snapshot(store) == before


def test_rerun_same_op_is_skipped(store, fakes):
    assert run(store, op="edit_node", node="conn/db/sc/t", set={"owner": "x"}).effect == "applied"
    before = snapshot(store)
    assert run(store, op="edit_node", node="conn/db/sc/t", set={"owner": "x"}) == OpResult("op-0100", "skipped")
    assert snapshot(store) == before


def test_op_id_conflict(store, fakes):
    run(store, op="edit_node", node="conn/db/sc/t", set={"owner": "x"})
    before = snapshot(store)
    with pytest.raises(OpIdConflict) as ei:
        run(store, op="edit_node", node="conn/db/sc/t", set={"owner": "y"})
    assert ei.value.code == "op_id_conflict" and ei.value.op_id == "op-0100"
    assert str(ei.value).startswith("op-0100 op_id_conflict: ")
    assert snapshot(store) == before


def test_conflict_compares_payload_as_received(store, fakes):
    # same op, keys in another order and same tags: identical canonical payload -> skipped, not a conflict
    d = op_dict(op="edit_node", node="conn/db/sc/t", set={"tags": ["b", "a"]})
    apply_op(store, parse_op(d))
    shuffled = dict(reversed(list(d.items())))
    assert apply_op(store, parse_op(shuffled)).effect == "skipped"
    # normalised-equal but differently written tags: a different payload -> conflict
    with pytest.raises(OpIdConflict):
        apply_op(store, parse_op({**d, "set": {"tags": ["a", "b"]}}))
    assert log_row(store, "op-0100").payload == canonical(d)


def test_unknown_kind_without_handler(store, monkeypatch):
    monkeypatch.delitem(HANDLERS, "delete_node")
    with pytest.raises(MalformedOp) as ei:
        run(store, op="delete_node", node="conn")
    assert ei.value.op_id == "op-0100"


def test_rejected_op_writes_no_log_row_and_can_be_retried(store, fakes):
    with pytest.raises(NodeNotFound):
        run(store, op="edit_node", node="conn/nope", set={"owner": "x"})
    assert log_row(store, "op-0100") is None
    assert run(store, op="edit_node", node="conn/db/sc/t", set={"owner": "x"}).effect == "applied"


def test_require_live(store):
    ctx = OpContext(store, parse_op(op_dict(op="delete_node", node="conn/db/sc/t")))
    assert require_live(ctx, "node", "conn/db/sc/t").id == "t"
    store.nodes.mark_deleted(["k"], "op-0001")
    for field in ("node", "parent", "from", "to"):
        with pytest.raises(NodeNotFound) as ei:
            require_live(ctx, field, "conn/db/sc/t/k")
        assert str(ei.value) == f"op-0100 node_not_found: {field} 'conn/db/sc/t/k' is not a live node"


# ---- apply_file ----

def test_apply_file_counts_and_order(store, fakes, tmp_path):
    ops = write_ops(tmp_path, [edit_line("op-0001", "conn/db/sc/t"),
                               json.dumps(op_dict(op_id="op-0002", op="delete_node", node="conn/db/sc/t/k")),
                               edit_line("op-0003", "conn/db/sc/u")])
    s = apply_file(ops, db_path(store), on_reject=lambda *a: pytest.fail(f"rejected {a}"))
    assert (s.applied, s.noop, s.skipped, s.rejected, s.rejections) == (3, 0, 0, 0, [])
    seqs = list(store.con.execute("SELECT seq, op_id FROM ops ORDER BY seq"))
    assert seqs == [(1, "op-0001"), (2, "op-0002"), (3, "op-0003")]


def test_bad_line_mid_file(store, fakes, tmp_path):
    ops = write_ops(tmp_path, [edit_line("op-0001", "conn/db/sc/t"), "{not json",
                               edit_line("op-0002", "conn/db/sc/u"), "[1]",
                               edit_line("op-0003", "conn/db/sc")])
    got = []
    s = apply_file(ops, db_path(store), on_reject=lambda line, e: got.append((line, e)))
    assert (s.applied, s.rejected) == (3, 2)
    assert [(l, type(e)) for l, e in got] == [(2, MalformedOp), (4, MalformedOp)]
    assert s.rejections == got
    assert str(got[1][1]) == "malformed: line is not a JSON object"
    assert {r[0] for r in store.con.execute("SELECT op_id FROM ops")} == {"op-0001", "op-0002", "op-0003"}


def test_line_numbers_count_newlines_only(store, fakes, tmp_path):
    p = tmp_path / "ops.jsonl"
    weird = json.dumps(op_dict(op_id="op-0001", op="edit_node", node="conn/db/sc/t",
                               set={"description": "a b\x85c\x0cd\re"}), ensure_ascii=False)
    p.write_text(weird + "\n\n   \n" + edit_line("op-0002", "conn/nope") + "\r\n", encoding="utf-8")
    got = []
    s = apply_file(p, db_path(store), on_reject=lambda line, e: got.append((line, str(e))))
    assert s.applied == 1
    assert got == [(4, "op-0002 node_not_found: node 'conn/nope' is not a live node")]
    assert store.nodes.by_id("t").description == "a b\x85c\x0cd\re"


def test_invalid_utf8_line_rejected(store, fakes, tmp_path):
    p = tmp_path / "ops.jsonl"
    p.write_bytes(b'{"op_id":"op-0001"\xff}\n' + edit_line("op-0002", "conn/db/sc/t").encode() + b"\n")
    got = []
    s = apply_file(p, db_path(store), on_reject=lambda line, e: got.append(line))
    assert (s.applied, s.rejected, got) == (1, 1, [1])


def test_duplicate_json_keys_rejected(store, fakes, tmp_path):
    line = '{"op_id":"op-0001","op_id":"op-0002","actor":{"kind":"human","name":"a"},"op":"delete_node","node":"conn"}'
    got = []
    s = apply_file(write_ops(tmp_path, [line]), db_path(store), on_reject=lambda l, e: got.append(e))
    assert s.rejected == 1 and isinstance(got[0], MalformedOp)


def test_reject_text_equals_single_op_error(store, fakes, tmp_path):
    bad = op_dict(op_id="op-0001", op="edit_node", node="conn/nope", set={"owner": "x"})
    with pytest.raises(OpError) as ei:
        apply_op(store, parse_op(bad))
    got = []
    apply_file(write_ops(tmp_path, [json.dumps(bad)]), db_path(store), on_reject=lambda l, e: got.append(e))
    assert str(got[0]) == str(ei.value)


def test_rerun_skips_everything(store, fakes, tmp_path):
    ops = write_ops(tmp_path, [edit_line("op-0001", "conn/db/sc/t"),
                               json.dumps(op_dict(op_id="op-0002", op="delete_node", node="conn/db/sc/u")),
                               edit_line("op-0003", "conn/nope")])
    first = apply_file(ops, db_path(store), on_reject=lambda *a: None)
    assert (first.applied, first.rejected) == (2, 1)
    before = snapshot(store)
    again = []
    second = apply_file(ops, db_path(store), on_reject=lambda l, e: again.append((l, str(e))))
    assert (second.applied, second.skipped, second.rejected) == (0, 2, 1)
    assert again == [(3, "op-0003 node_not_found: node 'conn/nope' is not a live node")]
    assert snapshot(store) == before


def test_apply_file_refuses_to_run(store, tmp_path):
    ops = write_ops(tmp_path, [edit_line("op-0001", "conn/db/sc/t")])
    with pytest.raises(ApplyError, match="cannot read"):
        apply_file(tmp_path / "nope.jsonl", db_path(store))
    with pytest.raises(ApplyError, match="uv run load"):
        apply_file(ops, tmp_path / "nope.db")
    assert not (tmp_path / "nope.db").exists()
    store.con.execute("PRAGMA user_version = 1")
    with pytest.raises(ApplyError, match=r"database needs migrations \[2\]; run: uv run migrate --db "):
        apply_file(ops, db_path(store))
    store.con.execute("PRAGMA user_version = 99")
    with pytest.raises(ApplyError, match="newer"):
        apply_file(ops, db_path(store))
