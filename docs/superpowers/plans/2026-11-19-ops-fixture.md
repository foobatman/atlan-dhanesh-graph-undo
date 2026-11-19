# Ops Fixture Generator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `uv run gen-fixture --seed N` also writes a deterministic `fixtures/ops.jsonl` that exercises every case in the ops spec.

**Architecture:** New module `graph_undo/ops_fixture.py`. A name-keyed simulator `_Graph` validates and applies each op as it is emitted. `generate_ops` plans its picks up front (recreated tables, E1/E2/E5/E6/E7 targets), then emits the phases with seeded filler between them. Tests replay the output through an independent applier in `tests/replay.py`.

**Tech Stack:** Python ≥ 3.11 stdlib, uv, pytest.

**Spec:** `docs/superpowers/specs/2026-11-19-ops-fixture-design.md`

## Global Constraints

- Five ops only: `add_node`, `edit_node`, `delete_node`, `add_edge`, `delete_edge`. Every node reference is a qualified name (`node`, `parent`, `from`, `to`). No ids in ops.
- Common fields: `op_id` (`op-0001`…), `actor` `{"kind","name"}` (human names from `fixture.OWNERS`, ingest name `ingest-crawler`), `op`.
- Ingest ops: only `add_node` (no `description`/`owner`/`tags`) and `add_edge`. `edit_node`, `delete_node` and `delete_edge` are human only.
- RNG: `random.Random(f"ops:{seed}")`. `seed.jsonl` bytes unchanged.
- Ops after op-0001: 290–320.
- README updated in the same task as the CLI change (CLAUDE.md rule).

## Review Focus

1. A filler op that touches a node a later scenario needs (e.g. deletes X2's only outgoing ref before E2) → the scenario must still be emitted, via a setup op. (`test_cases_present` over all seeds)
2. Ingest `add_edge` landing on the exact edge E1 later adds → a duplicate-edge failure. (Replay validity test)
3. Moved tables keep their old path as qualified name, and later name-based lookups still resolve. (Replay validity test)
4. `set` with `tags` unsorted or duplicated → the applier normalises it; the generator emits sorted lists. (`test_op_fields`)
5. Seeds where P has no tags at delete time → phase 7 still emits. (`test_cases_present`, seeds 0/61/252)

---

### Task 1: Independent replay applier + op-shape tests (RED)

**Files:** Create `tests/replay.py`, `tests/test_ops.py`.

**Interfaces:** Produces `replay(nodes, edges, ops) -> State`, where `State` has `nodes: dict[qn, dict]`, `parent: dict[qn, qn]`, `refs: set[tuple[qn, qn]]`, `seed_ids: set[str]`. It raises `AssertionError` naming the op_id on any invalid op. It mints ids `f"replay-{n}"`.

- [ ] Write `replay` from the spec's semantics table, without importing anything from `graph_undo.ops_fixture`.
- [ ] Write tests (parametrised over `conftest.SEEDS`, fixture `ops_case` = `(nodes, edges, ops, manifest)`):
  - `test_op_fields`: exact field sets per (op, actor kind). Ingest add_node has `{op_id, actor, op, qualified_name, type, parent}`. Human add_node adds `description, owner, tags`. edit_node has `{…, node, set}`, `set` non-empty ⊆ {description, owner, tags}, tags sorted and unique. delete_node has `{…, node}`. Edges have `{…, from, to, type}`.
  - `test_op_ids_and_first_delete`: consecutive ids. op-0001 is a human delete_node whose subtree in the seed is ≥ 500.
  - `test_op_count`: 290 ≤ len(ops) − 1 ≤ 320.
  - `test_replay_valid`: `replay(...)` raises nothing.
  - `test_recreated`: ≥ 50 D names live with an id ∉ seed ids. ≥ 5 human edit_node ops target such names (at the time of the op).
  - `test_cases_present`: manifest keys `neighbour_edits ≥15, repoints ≥10 pairs, moves ≥2, ingest_recreate ≥50, ingest_retouch ≥5, ingest_edges ≥3, recreated_edits ≥8, E1..E7 ≥1, late_parent_edit 1`. E1/E2 X had a seed ref to T.
  - `test_end_states`: states (a)–(d) from the spec each have ≥ 1 D node at the end of replay.
  - `test_contains_moves_adjacent`, `test_repoint_pairs` (≥ 10 adjacent delete/add references with the same `from`, different `to`).
  - `test_ops_deterministic`: same seed gives an identical ops list. Different seed gives a different one.
- [ ] Run: `uv run pytest tests/test_ops.py -q` → collection ImportError (no `graph_undo.ops_fixture`).

### Task 2: Simulator + generator

**Files:** Create `src/graph_undo/ops_fixture.py`.

**Interfaces:** `generate_ops(seed: int, nodes: list[dict], edges: list[dict]) -> tuple[list[dict], dict[str, list[str]]]`.

- [ ] `_Graph` (name-keyed: `nodes`, `parent`, `children` (ordered lists), `refs` set plus ordered list, `origin` qn → "seed"/"ops"). It has `apply(op)`, which validates per the spec and raises `ValueError`.
- [ ] Up-front planning from the seed: R (largest schema), D, removed refs, boundary tables, recreate order (inbound targets first), X1/T1, X2/T2, B, the E6 and E7 tables.
- [ ] Emit phases 1–7 per the spec, with filler chunks (`rng.randint(20, 30)`) after phases 2–6 and a top-up after phase 7 to `rng.randint(295, 315)`. Filler: edits on seed-origin live nodes except P; add/delete/repoint references between seed-origin live tables.
- [ ] Run: `uv run pytest -q` → all pass (seed + ops tests).
- [ ] Commit `feat: ops fixture generator`.

### Task 3: CLI + README

**Files:** Modify `src/graph_undo/fixture.py` (`main`), `tests/test_cli.py`, `README.md`.

- [ ] Test `test_main_writes_ops`: after `main([...])`, `ops.jsonl` == `serialize_ops(generate_ops(seed, *generate_seed(seed))[0])`, and `seed.jsonl` is unchanged. The stderr summary has `ops=`.
- [ ] Run → FAIL. Implement: `main` writes `ops.jsonl` with the same JSONL serialisation. Run → PASS.
- [ ] README: status table, ops file format, op table.
- [ ] Commit `feat: gen-fixture writes ops.jsonl`.
