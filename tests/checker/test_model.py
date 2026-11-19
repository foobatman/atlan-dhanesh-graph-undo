import pytest

from checker.helpers import dump_text, node
from undo_checker.model import ModelError, parse_dump, parse_report, removed


def test_parse_dump_nodes_and_edges():
    d = parse_dump(dump_text([node("a", "x"), node("b", "x/y", "column")], [("a", "b", "contains")]))
    assert d.nodes.keys() == {"a", "b"}
    assert d.edges == {("a", "b", "contains")}
    assert d.parent == {"b": "a"}
    assert d.multi_parent == frozenset()


def test_parse_dump_bad_line():
    with pytest.raises(ModelError, match="line 1"):
        parse_dump("{not json\n")


def test_parse_dump_neither_node_nor_edge():
    with pytest.raises(ModelError, match="line 1"):
        parse_dump('{"foo":1}\n')


def test_parse_dump_duplicate_id():
    text = dump_text([node("a", "x"), node("a", "y")], [])
    with pytest.raises(ModelError, match="duplicate"):
        parse_dump(text)


def test_names_lists_duplicates():
    d = parse_dump(dump_text([node("b", "x"), node("a", "x")], []))
    assert d.names()["x"] == ["a", "b"]


def test_multi_parent_recorded():
    d = parse_dump(dump_text([node("a", "p"), node("b", "q"), node("c", "r")],
                             [("a", "c", "contains"), ("b", "c", "contains")]))
    assert d.multi_parent == {"c"}
    assert d.parent["c"] == "a"


def test_removed_subtree():
    nodes = [node("a", "s", "schema"), node("b", "s/t"), node("c", "s/t/c", "column"), node("x", "s2/x")]
    d0 = parse_dump(dump_text(nodes, [("a", "b", "contains"), ("b", "c", "contains"), ("x", "c", "references")]))
    d1 = parse_dump(dump_text([nodes[0], nodes[3]], []))
    r = removed(d0, d1)
    assert r.nodes.keys() == {"b", "c"}
    assert r.edges == {("a", "b", "contains"), ("b", "c", "contains"), ("x", "c", "references")}
    assert r.parent0 == {"b": "a", "c": "b"}


def test_parse_report_mixed():
    lines = parse_report('{"kind":"node"}\n\nnope\n')
    assert [l.lineno for l in lines] == [1, 2, 3]
    assert lines[0].obj == {"kind": "node"} and lines[0].error is None
    assert lines[1].obj is None and lines[1].error == "blank line"
    assert lines[2].obj is None and lines[2].error


def test_parse_report_non_object():
    (l,) = parse_report("[1]\n")
    assert l.obj is None and "object" in l.error
