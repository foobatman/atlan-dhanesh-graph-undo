from pathlib import Path

from undo_checker import cli, run
from undo_checker.invariants import Result
from undo_checker.run import RunError


def test_format_results_truncates():
    out = cli.format_results([(1, Result("FAIL", "12 violations", tuple(f"v{i}" for i in range(12))))],
                             "op-0001", show_all=False)
    lines = out.splitlines()
    assert lines[0] == "1 FAIL  12 violations"
    assert lines[1:11] == [f"  v{i}" for i in range(10)]
    assert lines[11] == "  … 2 more (use --all)"
    assert lines[12] == "check op-0001: 0 passed, 1 failed, 0 skipped"


def test_format_results_show_all():
    out = cli.format_results([(1, Result("FAIL", "12 violations", tuple(f"v{i}" for i in range(12))))],
                             "op-0001", show_all=True)
    assert "  v11" in out and "more" not in out


def test_format_results_summary_counts():
    res = [(1, Result("PASS", "ok")), (2, Result("FAIL", "1 violations", ("x",))), (7, Result("SKIP", "s"))]
    out = cli.format_results(res, "op-0007", show_all=False)
    assert out.splitlines()[0] == "1 PASS  ok"
    assert out.endswith("check op-0007: 1 passed, 1 failed, 1 skipped\n")


def test_main_bad_op_exit2(fixture42, capsys):
    seed, ops = fixture42
    assert cli.main(["--seed", str(seed), "--ops", str(ops), "--op", "op-9999"]) == 2
    err = capsys.readouterr().err
    assert f"op 'op-9999' not found in {ops}" in err


def test_main_missing_seed_exit2(fixture42, tmp_path, capsys):
    _, ops = fixture42
    assert cli.main(["--seed", str(tmp_path / "nope.jsonl"), "--ops", str(ops)]) == 2
    assert "nope.jsonl" in capsys.readouterr().err


def test_main_step_failure_keeps_workdir(fixture42, monkeypatch, capsys):
    seed, ops = fixture42

    def boom(art, op_id):
        raise RunError("run failed at step 8 (undo): boom")

    monkeypatch.setattr(run, "finish", boom)
    assert cli.main(["--seed", str(seed), "--ops", str(ops)]) == 2
    err = capsys.readouterr().err
    assert "run failed at step 8 (undo): boom" in err
    kept = err.split("artifacts kept in ", 1)[1].strip()
    assert (Path(kept) / "D2.jsonl").exists()
