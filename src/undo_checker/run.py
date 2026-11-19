"""Drive the tool through its CLI only: load, apply, dump, undo (spec: "The run").

Never opens the database; every fact comes from what a command prints.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

TIMED_STEPS = (1, 2, 4, 6, 8, 9)
# Files a run writes; cleared at the start so a reused workdir never mixes two runs.
ARTIFACTS = ("graph.db", "graph.db-journal", "ops_pre.jsonl", "ops_target.jsonl", "D0.jsonl", "D1.jsonl",
             "D2.jsonl", "report.jsonl", "D3.jsonl", "report2.jsonl", "undo2.stderr", "D4.jsonl")
REJECTED = re.compile(r"rejected=(\d+)")


class RunError(Exception):
    """The scenario couldn't be run or set up; str() is the stderr message (exit 2)."""


@dataclass
class Target:
    op_id: str
    qualified_name: str
    pre: list[str]
    line: str
    ops_lines: list[dict]


def slice_ops(ops_path: Path, op_id: str) -> Target:
    pre: list[str] = []
    parsed: list[dict] = []
    found: tuple[str, dict] | None = None
    for n, raw in enumerate(Path(ops_path).read_text(encoding="utf-8").split("\n"), 1):
        if not raw.strip():
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            raise RunError(f"{ops_path}:{n}: not JSON") from None
        if not isinstance(obj, dict):
            raise RunError(f"{ops_path}:{n}: not a JSON object")
        parsed.append(obj)
        if found is None:
            if obj.get("op_id") == op_id:
                found = (raw, obj)
            else:
                pre.append(raw)
    if found is None:
        raise RunError(f"op '{op_id}' not found in {ops_path}")
    raw, obj = found
    if obj.get("op") != "delete_node":
        raise RunError(f"only delete_node ops can be checked ({op_id} is {obj.get('op')})")
    return Target(op_id, obj.get("node"), pre, raw, parsed)


@dataclass
class Artifacts:
    workdir: Path
    d0: str | None = None
    d1: str | None = None
    d2: str | None = None
    d3: str | None = None
    d4: str | None = None
    report: str | None = None
    undo2_stderr: str | None = None
    timings: dict[int, float] = field(default_factory=dict)


def _run(art: Artifacts, step: int, cmd: str, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    argv = ["uv", "run", cmd, *args, "--db", str(art.workdir / "graph.db")]
    t0 = time.perf_counter()
    r = subprocess.run(argv, capture_output=True, encoding="utf-8")
    art.timings[step] = time.perf_counter() - t0
    if check and r.returncode != 0:
        raise RunError(f"run failed at step {step} ({cmd}): {r.stderr.strip()}")
    return r


def _apply(art: Artifacts, step: int, ops_file: Path) -> None:
    r = _run(art, step, "apply", str(ops_file))
    m = REJECTED.search(r.stderr)
    k = int(m.group(1)) if m else None
    if k != 0:
        raise RunError(f"step {step}: apply rejected {k if k is not None else '?'} ops; "
                       "the scenario is not the one being checked")


def _dump(art: Artifacts, step: int, name: str) -> str:
    text = _run(art, step, "dump").stdout
    (art.workdir / name).write_text(text, encoding="utf-8")
    return text


def prepare(seed: Path, ops: Path, target: Target, workdir: Path) -> Artifacts:
    """Steps 1-7: load, apply up to the target, dump D0, apply it, dump D1, apply all, dump D2."""
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    for name in ARTIFACTS:
        (workdir / name).unlink(missing_ok=True)
    art = Artifacts(workdir)
    pre = workdir / "ops_pre.jsonl"
    tgt = workdir / "ops_target.jsonl"
    pre.write_text("".join(l + "\n" for l in target.pre), encoding="utf-8")
    tgt.write_text(target.line + "\n", encoding="utf-8")
    _run(art, 1, "load", str(seed))
    if target.pre:
        _apply(art, 2, pre)
    art.d0 = _dump(art, 3, "D0.jsonl")
    _apply(art, 4, tgt)
    art.d1 = _dump(art, 5, "D1.jsonl")
    _apply(art, 6, Path(ops))
    art.d2 = _dump(art, 7, "D2.jsonl")
    return art


def finish(art: Artifacts, op_id: str) -> Artifacts:
    """Steps 8-11: undo, dump D3, undo again (any exit code), dump D4."""
    r = _run(art, 8, "undo", op_id)
    art.report = r.stdout
    (art.workdir / "report.jsonl").write_text(r.stdout, encoding="utf-8")
    art.d3 = _dump(art, 9, "D3.jsonl")
    r2 = _run(art, 10, "undo", op_id, check=False)
    (art.workdir / "report2.jsonl").write_text(r2.stdout, encoding="utf-8")
    art.undo2_stderr = r2.stderr
    (art.workdir / "undo2.stderr").write_text(r2.stderr, encoding="utf-8")
    art.d4 = _dump(art, 11, "D4.jsonl")
    return art


def scenario_seconds(art: Artifacts) -> float:
    return sum(art.timings.get(s, 0.0) for s in TIMED_STEPS)
