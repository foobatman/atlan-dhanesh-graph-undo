import json
import subprocess

from graph_undo.fixture.cli import main
from graph_undo.fixture.seed import generate_seed, serialize


def test_deterministic_bytes():
    assert serialize(*generate_seed(42)) == serialize(*generate_seed(42))
    assert serialize(*generate_seed(42)) != serialize(*generate_seed(43))


def test_serialize_format():
    nodes, edges = generate_seed(42)
    text = serialize(nodes, edges)
    lines = text.split("\n")
    assert text.endswith("\n") and lines[-1] == ""
    assert lines[0] == json.dumps(nodes[0], sort_keys=True, separators=(",", ":"))
    parsed = [json.loads(line) for line in lines[:-1]]
    assert parsed == nodes + edges


def test_main_creates_out_dir(tmp_path, capsys):
    out = tmp_path / "a" / "b"
    assert main(["--seed", "7", "--out", str(out)]) == 0
    assert (out / "seed.jsonl").read_text() == serialize(*generate_seed(7))
    assert "nodes" in capsys.readouterr().err


def test_main_overwrites(tmp_path):
    main(["--seed", "1", "--out", str(tmp_path)])
    main(["--seed", "2", "--out", str(tmp_path)])
    assert (tmp_path / "seed.jsonl").read_text() == serialize(*generate_seed(2))


def test_unusual_seeds():
    for s in (0, 2**64):
        a, b = serialize(*generate_seed(s)), serialize(*generate_seed(s))
        assert a == b and 4500 <= a.count("\n") <= 5500


def test_entry_point(tmp_path):
    r = subprocess.run(["uv", "run", "gen-fixture", "--seed", "42", "--out", str(tmp_path)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "seed.jsonl").read_text() == serialize(*generate_seed(42))


def test_main_writes_ops(tmp_path, capsys):
    from graph_undo.fixture.ops import generate_ops

    assert main(["--seed", "42", "--out", str(tmp_path)]) == 0
    nodes, edges = generate_seed(42)
    ops, _ = generate_ops(42, nodes, edges)
    assert (tmp_path / "seed.jsonl").read_text() == serialize(nodes, edges)
    assert (tmp_path / "ops.jsonl").read_text() == serialize(ops, [])
    assert "ops=" in capsys.readouterr().err
