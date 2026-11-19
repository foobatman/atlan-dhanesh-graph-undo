"""`uv run check`: the independent invariant checker (spec: 2026-11-19-checker-design.md).

Runs the tool through its CLI, reads only dumps and the undo report, prints one line per invariant.
Exit 0: nothing failed. 1: an invariant failed. 2: the run, the guard or the input failed.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from . import invariants as inv
from . import run
from .model import ModelError, parse_dump, parse_report, removed

MAX_SHOWN = 10
DEFAULT_WORKDIR = Path("out/check")


def format_results(results: list[tuple[int, inv.Result]], op_id: str, show_all: bool) -> str:
    out = []
    for n, r in results:
        out.append(f"{n} {r.status}  {r.summary}")
        shown = r.violations if show_all else r.violations[:MAX_SHOWN]
        out.extend(f"  {v}" for v in shown)
        if len(r.violations) > len(shown):
            out.append(f"  … {len(r.violations) - len(shown)} more (use --all)")
    count = {s: sum(1 for _, r in results if r.status == s) for s in ("PASS", "FAIL", "SKIP")}
    out.append(f"check {op_id}: {count['PASS']} passed, {count['FAIL']} failed, {count['SKIP']} skipped")
    return "\n".join(out) + "\n"


def _check(seed: Path, ops: Path, op_id: str, workdir: Path, t0: float) -> list[tuple[int, inv.Result]]:
    for p in (seed, ops):
        if not p.is_file():
            raise run.RunError(f"{p}: no such file")
    target = run.slice_ops(ops, op_id)
    art = run.prepare(seed, ops, target, workdir)
    d0, d1, d2 = parse_dump(art.d0), parse_dump(art.d1), parse_dump(art.d2)
    bad = inv.guard(d0, d1, target.qualified_name)
    if bad:
        raise run.RunError("guard failed: " + "; ".join(bad[:MAX_SHOWN]))
    art = run.finish(art, op_id)
    d3, d4 = parse_dump(art.d3), parse_dump(art.d4)
    rem = removed(d0, d1)
    report = parse_report(art.report)
    return [
        (1, inv.inv1_frame(d2, d3, rem, target.ops_lines)),
        (2, inv.inv2_restored(d0, d2, d3, rem, report)),
        (3, inv.inv3_unique(d3, d4)),
        (4, inv.inv4_report(report, rem, d3, d2)),
        (5, inv.inv5_effective_tags(d3, d4)),
        (6, inv.inv6_idempotent(art.d3, art.d4, art.undo2_stderr or "")),
        (7, inv.INV7),
        (8, inv.inv8_timing(run.scenario_seconds(art), time.perf_counter() - t0)),
    ]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="check", description=__doc__.splitlines()[0])
    ap.add_argument("--seed", type=Path, default=Path("fixtures/seed.jsonl"))
    ap.add_argument("--ops", type=Path, default=Path("fixtures/ops.jsonl"))
    ap.add_argument("--op", default="op-0001", help="the delete_node op to undo and check")
    ap.add_argument("--workdir", type=Path, default=DEFAULT_WORKDIR,
                    help="where the run's db, dumps and reports go (default: out/check; cleared per run)")
    ap.add_argument("--all", action="store_true", help="print every violation, not just the first 10")
    args = ap.parse_args(argv)

    t0 = time.perf_counter()
    workdir = args.workdir
    try:
        results = _check(args.seed, args.ops, args.op, workdir, t0)
    except (run.RunError, ModelError) as e:
        print(f"check: {e}", file=sys.stderr)
        print(f"artifacts kept in {workdir}", file=sys.stderr)
        return 2
    sys.stdout.write(format_results(results, args.op, args.all))
    failed = any(r.status == "FAIL" for _, r in results)
    print(f"artifacts kept in {workdir}", file=sys.stderr)
    return 1 if failed else 0
