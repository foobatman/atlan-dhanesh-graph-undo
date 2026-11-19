# Invariant checker: design

**Status:** draft, awaiting approval
**Brief:** `problem.md`, "Invariants your checker must enforce": "it must test these against the dump and the report, and it must not share code with undo."
**Builds on (contracts only, never code):**
- `2026-11-19-dump-design.md`: the dump format.
- `2026-11-19-undo-design.md`: the undo CLI and its JSONL report.
- README `apply`: re-runs skip ops that are already logged.

## Intent

`uv run check` is an independent witness for undo. It runs the tool as a black box through its CLI commands (`load`, `apply`, `undo`, `dump`) and reads only what those commands print: dumps, the undo report, and the fixture files. It never opens the SQLite file, and it imports nothing from `graph_undo`. If the checker reused undo's code, tables or queries, it would inherit undo's bugs and agree with them.

## Goals

- Check invariants 1, 2, 3, 4, 5, 6 and 8 for one undone `delete_node` op (default `op-0001`) on a given fixture.
- Each failure names the invariant, the offending node or edge (id and `qualified_name`), and what was expected.
- Exit 0 only if every checked invariant passes. Output is deterministic.

## Non-goals

- **Invariant 7 (`CRASH_AFTER`, resumability).** The user's decision is to leave it out of this spec. The checker prints `7 SKIP (not covered by this checker)`.
- Checking `apply`, `load` or the fixture generator for their own sake. They have their own tests. The checker trusts them only as far as the guard below allows.
- Undoing several ops in one run.
- **`delete_edge` targets (Claude's call).** problem.md's invariants are about undoing a subtree delete, and for a `delete_edge` op invariant 2 has nothing to check. `--op` naming any op other than a `delete_node` exits 2 with `only delete_node ops can be checked`.

## Anti-goals

- Any `import graph_undo`, any `sqlite3` use, or any read of the db file.
- Re-implementing undo's rules (B2) in the checker. The checker tests the invariants as stated in problem.md, plus the readings chosen below. It does not test "did undo do what B2 says".
- A check that passes without examining anything. If an invariant has nothing to check, it fails with `vacuous` instead of passing.

## Approaches

**How the checker gets its inputs:**
- **Chosen: the checker runs the CLI itself** in a temp workdir. Invariants 6 and 8 need commands run anyway (undo twice, timing).
- **Rejected: the checker only reads files a separate scenario script produced.** Correctness of 6 and 8 would then depend on that script.

**How the checker learns what the delete removed:**
- **Chosen: dumps taken before and after the delete op.** Apply the ops before it and dump (D0), apply the op itself and dump (D1). The removed set is D0 − D1. This works for any `delete_node` op, not only op-0001, and needs only dumps.
- **Rejected: replaying `seed.jsonl` + `ops.jsonl` independently.** Ops mint uuid5 ids the checker can't know, so it would have to match by name. That is fragile exactly where it matters most, around recreated names.

**Reading of invariant 2's "whose parent is live":**
- **Chosen (user's decision): by name.** After undo, a live node of the same type holds the original parent's `qualified_name`.
- **Rejected: by id** (the original parent row is live). On seed 42 the root schema's name is recreated, so the root is skipped, no child has a live original parent, and the invariant would pass while checking nothing. By name implies by id, so checking by name covers both.

**Strength of invariant 1 (Claude's call):**
- **Chosen: a frame check.** Undo may only add things. Everything live before undo is unchanged after it, and everything new is a removed item. This is stricter than "no human change is lost", needs no op replay, and holds under B2.
- **Rejected: attributing each change to a human op** by replaying `ops.jsonl` by name. It is weaker, and fragile for the same reason as the replay above.

## The run

The workdir is a fresh temp directory, or `--workdir DIR`. Let `T` be the target op's line in `ops.jsonl`, found by parsing each line's `op_id`.

| step | command | artifact |
|---|---|---|
| 1 | `load <seed> --db W/graph.db` | |
| 2 | `apply W/ops_pre.jsonl` (lines before T; skipped if empty) | |
| 3 | `dump` | `D0.jsonl` (before the delete) |
| 4 | `apply W/ops_target.jsonl` (line T only) | |
| 5 | `dump` | `D1.jsonl` (after the delete) |
| 6 | `apply <ops>` (the full file; T and earlier are skipped as already logged) | |
| 7 | `dump` | `D2.jsonl` (before undo) |
| 8 | `undo <op_id>`, stdout captured | `report.jsonl` |
| 9 | `dump` | `D3.jsonl` (after undo) |
| 10 | `undo <op_id>` again, any exit code | `report2.jsonl`, `undo2.stderr` |
| 11 | `dump` | `D4.jsonl` (after the second undo) |

- Every command runs as `uv run <cmd> … --db W/graph.db` via `subprocess`. Steps 1–9 must exit 0, otherwise the checker stops with `run failed at step N` and that command's stderr.
- The apply summary for steps 2, 4 and 6 must show `rejected=0`. A rejected op means the scenario isn't the one being checked.
- **Timing (invariant 8):** the wall-clock sum of steps 1, 2, 4, 6, 8 and 9 must be under 60 s. Steps 2, 4 and 6 together are one full apply of `ops.jsonl`, split in three, so the sum is the scenario a user runs: load, apply, undo, dump. Only the checker's extra dumps (steps 3, 5, 7) and the second undo (steps 10, 11) are left out. The total run time is printed too.

**Guard on the removed set:** before any invariant check runs, the checker verifies three things:
- D1 adds nothing to D0. Nodes are compared on identity and authored fields, not `effective_tags`.
- No node left in D1 changed.
- The removed nodes are exactly the `contains` subtree, in D0, of the node the op names (looked up by `qualified_name`), and the removed edges are exactly the D0 edges that touch them.

If the guard fails, the checker stops with `guard failed`, because every later check would be meaningless.

## Inputs, as the checker models them

- **Dump:** node lines (they have `qualified_name`) are keyed by `id`. Edge lines (they have `from_id`) are keyed by the triple `(from_id, to_id, type)`, which is unique among live edges.
- **Authored fields:** `description`, `owner`, `tags`. **Identity:** `id`, `qualified_name`, `type`.
- **Report:** one JSON object per line, as in the undo spec. Node lines have `kind="node"` and `id`. Edge lines have `kind="edge"`, `from_id`, `to_id` and `type`. Edges are matched to dumps by their triple and never by the report's row `id`, because dumps don't carry row ids.
- **Removed set:** N = nodes in D0 and not in D1, by id. E = edge triples in D0 and not in D1. For each n in N, `parent0(n)` is the `from_id` of n's `contains` edge in D0, or none.

## Invariant checks (pure functions over the parsed inputs)

**1. No human change is lost or altered (frame check).**
- Every node in D2 is in D3 with the same identity and authored fields. `effective_tags` is excluded here because invariant 5 covers it.
- Every edge in D2 is in D3.
- Every node in D3 but not in D2 is in N, by id.
- Every edge in D3 but not in D2 is either in E, or a `contains` edge whose `to_id` is a node in D3 − D2. The second case is a merged edge that hangs a restored node under a new parent.

Each failure message also lists the `ops.jsonl` lines by a human actor that mention the node's `qualified_name`. That is an aid for reading the failure, not part of the check.

**2. Eligible removed nodes are restored intact (by name).** For each n in N, n is *eligible* when both hold:
- n's `qualified_name` is not live in D2;
- either n had no parent in D0, or some node h in D3 has `parent0(n)`'s `qualified_name` and its type.

An eligible n must be in D3 with the same id, the same identity and the same authored fields as in D0. Its `contains` parent in D3 must be h, or it must have no parent if it had none in D0. Its report line must say `restored`. Zero eligible nodes is a `vacuous` failure.

**3. Names are unique.** No two nodes in D3 share a `qualified_name`. The same check runs on D4.

**4. The report accounts for every removed item exactly once.**
- Every line parses, and has `kind` in {node, edge} and `outcome` in {restored, merged, skipped}. `merged` and `skipped` lines have a non-empty `reason`.
- The node ids in the report are exactly N, with no duplicates. The edge triples in the report are exactly E, with no duplicates.
- The outcomes agree with D3:
  - a `restored` node is in D3 by id;
  - a `skipped` node is not in D3 by id;
  - a `restored` edge's triple is in D3;
  - a `merged` edge's child (`to_id`) is in D3 and has a `contains` parent there.

**5. `effective_tags` is correct.**
- First, structural checks on D3: every edge endpoint is a node in D3, every node has at most one `contains` parent, and the `contains` edges have no cycle.
- Then every node's `effective_tags` must equal the sorted union of its own `tags` and the tags of all its `contains` ancestors, computed by the checker from D3.
- The same checks run on D4.

**6. Undo is idempotent.** D4 is byte-identical to D3. The second undo's exit code is ignored, since the undo design refuses with exit 1. Its first stderr line is printed for information. On a mismatch, the first differing lines are shown.

**7.** `SKIP`.

**8. The scenario runs in under 60 s.** See Timing above.

## Output and exit codes

stdout gets one line per invariant, then a final summary line:

```
1 PASS  frame: 2410 nodes, 2690 edges unchanged; 489 nodes, 1102 edges added, all from op-0001
2 PASS  431 eligible nodes restored intact
3 PASS
4 PASS  614 nodes, 774 edges, each reported once
5 PASS  2899 nodes
6 PASS  D3 == D4
7 SKIP  not covered by this checker
8 PASS  scenario 3.1 s (checker total 4.0 s)
check op-0001: 6 passed, 0 failed, 1 skipped
```

- A failing invariant prints `N FAIL <count> violations` followed by up to 10 indented violation lines. `--all` prints all of them.
- **Exit codes:**
  - 0: no `FAIL`;
  - 1: at least one `FAIL`;
  - 2: the run or the guard failed, or the input is bad (e.g. the op id isn't in `ops.jsonl`). The message goes to stderr.
- The workdir and its artifacts (D0–D4, both reports, the ops slices) are kept when anything fails, or with `--keep`, and their path is printed. Otherwise the temp directory is removed.

## CLI

```
uv run check [--seed fixtures/seed.jsonl] [--ops fixtures/ops.jsonl] [--op op-0001] [--workdir DIR] [--keep] [--all]
```

The checker does not generate the fixture. README already has `uv run gen-fixture`.

## Code layout

A separate top-level package that shares no code with `graph_undo`:

| file | what |
|---|---|
| `src/undo_checker/run.py` | the steps above: subprocess calls, ops slicing, timing, artifacts |
| `src/undo_checker/model.py` | parses dumps and reports into plain dicts and sets: nodes by id, edges by triple, parent map |
| `src/undo_checker/invariants.py` | one pure function per invariant, plus the guard. Each takes parsed inputs and returns a `Result(status, summary, violations)`. No I/O. |
| `src/undo_checker/cli.py` | `main(argv=None) -> int`: argument parsing, printing, exit codes |

- `pyproject.toml`: add `check = "undo_checker.cli:main"`. The build backend must ship both packages (uv_build `module-name = ["graph_undo", "undo_checker"]`). The builder verifies this works with the pinned uv_build.
- Stdlib only, per CLAUDE.md.
- `tests/test_layers.py` gets a rule: no file under `src/undo_checker/` imports `graph_undo` or `sqlite3`, and no file under `src/graph_undo/` imports `undo_checker`.

## Testing

- **Unit tests** (`tests/checker/test_invariants.py`): small hand-written dumps and reports. Each invariant gets a passing case and at least one doctored case that must fail, for example:
  - an edited field reverted;
  - a human-added edge dropped;
  - a node added that was never removed;
  - an eligible node missing, or restored with an old field value or under the wrong parent;
  - two nodes with the same name;
  - a report line missing, duplicated or extra;
  - a wrong `effective_tags` value;
  - a dangling edge;
  - D3 ≠ D4;
  - a vacuous invariant 2.

  The guard gets its own pass and fail cases.
- **Model tests:** dump and report parsing, edge matching by triple, `parent0`.
- **End-to-end** (`tests/checker/test_check_e2e.py`): `uv run check` on the seed-42 fixture exits 0 with invariant 7 skipped. `fixtures/` is gitignored, so the test generates the fixture into a temp dir with `uv run gen-fixture --out`. It is skipped while the `undo` entry point doesn't exist (undo is being built in a separate worktree). The checker counts as done when the unit tests pass. The e2e run against real undo happens once undo lands, and any undo bugs it finds go to the undo work.
- **Mutation end-to-end:** run the checker's model and invariants on the real seed-42 D0–D4 with one line doctored in D3, and assert that the matching invariant fails. This proves the checks bite on real data, not only on toy dumps. It needs undo too, so it is skipped the same way. The guard's tests don't need undo and always run on real seed-42 D0/D1.

## Documentation

- README:
  - status row "Checker": done, with 7 noted as skipped;
  - a `## Check` section with the command, the steps, the output format and the exit codes;
  - layout entries for `src/undo_checker/`.
- `DECISIONS.md` "what I verified": no entries written by Claude. The user fills it in. The checker's output is the evidence.

## Left to the builder

- internal helper structure and the exact wording of violation lines;
- whether `run.py` invokes `uv run <cmd>` or the venv's entry-point scripts directly (timing must cover the same commands either way);
- temp directory handling details.

## Calls made by Claude (flag any to change)

1. Invariant 1 is checked as the stricter frame check rather than by attributing changes to human ops.
2. Invariant 2 also requires the restored node to hang under the name holder h, and its report line to say `restored`.
3. An invariant with nothing to check fails as `vacuous`.
4. Timing counts load, the full apply (steps 2, 4 and 6), undo and dump. It leaves out the checker's extra dumps and the second undo.
5. The second undo's exit code is ignored. Only D3 == D4 matters.
6. A failed guard or setup step exits 2, separate from invariant failures, which exit 1.
7. Default target op is `op-0001`, and one op is checked per run. Only `delete_node` targets are accepted.
8. The checker relies on the undo spec's report contract: JSONL on stdout, one line per item, edges with their original `from_id`/`to_id`/`type`. The user chose that format. If the undo work changes it, `model.py` and invariant 4 follow.
