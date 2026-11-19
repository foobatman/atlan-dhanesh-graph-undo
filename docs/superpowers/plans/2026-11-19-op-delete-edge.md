# `delete_edge` Operation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (or superpowers:subagent-driven-development) to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `apply` handles human `delete_edge`. It soft-deletes exactly one live edge row (`deleted_by = op_id`) and writes one `removals` row and one op-log row, all in the op's transaction. Invalid requests raise the foundation's `OpError`s and write nothing.

**Architecture:** One API module, `graph_undo/api/delete_edge.py: handle(ctx: OpContext, op: DeleteEdge) -> OpResult`, registered in `api/registry.HANDLERS["delete_edge"]`. It talks to the database only through `NodeRepo`, `EdgeRepo` and `OpLogRepo`. There's no new SQL and no schema change.

**Tech Stack:** Python ≥ 3.11 stdlib, SQLite (schema v2), uv, pytest.

**Spec:** `docs/superpowers/specs/2026-11-19-op-delete-edge-design.md`
**Depends on:** `docs/superpowers/plans/2026-11-19-apply-foundation.md`, fully landed: package layout, `parse_op`, repositories, `apply_op`, `tests/apply/conftest.py` (`store`, `run`, `log_row`, `live_names`).

## Global Constraints

- **Commands:** every command runs as `uv run …` (`uv run pytest tests/apply/test_delete_edge.py -q`). Never call bare `python`/`pytest`.
- **Git:** commit directly on `main`. No branches or worktrees.
- **Runtime:** stdlib only. No SQL outside `graph_undo/db/` (`tests/test_layers.py` must stay green).
- **README:** update its status table (`delete_edge` handler: done) in the same commit as the handler.
- **Data model:** don't add fields to nodes or edges. Use the foundation names exactly (`DeleteEdge.from_`, `EdgeRepo.live`, `mark_deleted`, `OpLogRepo.record`, `add_removals`).

## Review Focus

1. **Exactly one row changes:** only the id returned by `EdgeRepo.live` gets `deleted_by`. Dead same-triple rows, the reverse edge and the other type are untouched. Tests: `test_dead_duplicate_keeps_original_deleted_by`, `test_reverse_direction_untouched`, `test_wrong_type_is_edge_not_found`.
2. **Never a hard delete:** the `edges` row count is unchanged and `nodes` is byte-identical. Test: `test_delete_reference_soft_deletes_one_row`.
3. **Write order:** `log.record` comes before `log.add_removals` (foreign key), all inside `apply_op`'s transaction. Test: `test_removals_match_deleted_by`.
4. **A rejection writes nothing:** the snapshot of nodes, edges, ops and removals is identical, and the error text is exact. Test: `test_rejection_writes_nothing[*]`.
5. **Parentless child is legal:** no look-ahead and no re-attach check. `effective_tags` drops the old ancestors' tags through the view. Test: `test_delete_contains_leaves_child_parentless`.

---

### Test setup (shared by all tasks)

The `store` fixture (foundation) holds `conn` → `conn/db` → `conn/db/sc` → tables `conn/db/sc/t`, `conn/db/sc/u` → column `conn/db/sc/t/k`. Local helpers in `tests/apply/test_delete_edge.py`, used only in **setup**, before the op under test:

- `seed_ref(store, f, t, type="references") -> int`: `store.edges.insert(...)` plus commit. It returns the edge id. It's used instead of relying on the conftest's one reference.
- `seed_tags(store, qn, tags)`: `store.nodes.update_fields(id, {"tags": tags})` plus commit. Tags: `conn`=`[c]`, `conn/db`=`[d]`, `conn/db/sc`=`[s]`, `t`=`[tt]`, `u`=`[uu]`, `k`=`[kk]`.
- `eff(store, qn) -> list[str]`: reads the `effective_tags` view through a db-layer read helper. If no repo method exists, use a test-only query in `tests/apply/conftest.py`; tests may contain SQL.
- `snapshot(store) -> dict`: all rows of `nodes`, `edges`, `ops` and `removals`, ordered.
- `edge_row(store, id) -> (from_id, to_id, type, deleted_by)`.
- `de(f, t, type="references", op_id="op-0001", who="ana")`: the op dict for `run`.

Short names below: T=`conn/db/sc/t`, U=`conn/db/sc/u`, K=`conn/db/sc/t/k`, S=`conn/db/sc`.

### Task 1: Handler, happy path and errors (references)

**Files:** Create `src/graph_undo/api/delete_edge.py`. Modify `src/graph_undo/api/registry.py` and `README.md`. Test: `tests/apply/test_delete_edge.py`.

**Interface:** `def handle(ctx: OpContext, op: DeleteEdge) -> OpResult`.

- [ ] **Step 1: Write the failing tests.**
  - `test_delete_reference_soft_deletes_one_row`:
    - Setup: `id = seed_ref(T, U)`; `n_edges` = `edges` row count; `nodes_before` = all `nodes` rows.
    - `run(de(T, U))`.
    - Assert:
      - the result is `OpResult("op-0001", "applied", edge_id=id, removed_edges=1)`;
      - `edge_row(id)` is `(t.id, u.id, "references", "op-0001")`;
      - the `edges` row count is still `n_edges`;
      - the `nodes` rows equal `nodes_before`;
      - `store.edges.live(t.id, u.id, "references") is None`;
      - `live_edges` lacks `id`, and every other edge id is still live.
  - `test_log_row`: after the above, `log_row("op-0001")` has:
    - `op="delete_edge"`, `effect="applied"`, `actor_kind="human"`, `actor_name="ana"`;
    - `node_id is None`, `edge_id == id`, `before is None`;
    - `payload == canonical(op dict)`.

    `ops` has exactly one row.
  - `test_removals_match_deleted_by`: after the delete, `store.log.removals("op-0001") == ([], [id])`. The raw `removals` table has exactly one row, `("op-0001", "edge", str(id))`. For every `removals` row of kind `edge`, `edges.deleted_by` of that `item_id` equals its `op_id`. Run this check after every multi-op test too, through the helper `assert_removals_consistent(store)`.
  - `test_reverse_direction_untouched`: `a = seed_ref(T, U)` and `b = seed_ref(U, T)`. Delete T→U. `a` is dead, `b` has `deleted_by is None` and is still in `live_edges`.
  - `test_wrong_type_is_edge_not_found`: S→T `contains` exists, and there's no reference S→T. `run(de(S, T, "references"))` raises `EdgeNotFound`, with `str` equal to `"op-0001 edge_not_found: no live references edge 'conn/db/sc' -> 'conn/db/sc/t'"`. The contains edge is still live.
  - `test_other_type_same_endpoints_survives`: `seed_ref(T, K, "references")` alongside the contains T→K. Delete the reference. The contains T→K is still live and `live_parent(k)` is unchanged.
  - `test_from_not_live` / `test_to_not_live`: `run(de("conn/nope", U))` gives `"op-0001 node_not_found: from 'conn/nope' is not a live node"`. `run(de(T, "conn/nope"))` gives `"… to 'conn/nope' is not a live node"`.
  - `test_both_missing_reports_from`: `de("conn/x", "conn/y")` reports `from 'conn/x'`.
  - `test_self_loop_is_edge_not_found`: `de(T, T)` gives `EdgeNotFound` with the message `no live references edge 'conn/db/sc/t' -> 'conn/db/sc/t'`, not `SelfLoop`.
  - `test_rejection_writes_nothing[case]`, parametrized over the five rejections above:
    - setup: `seed_ref(T, U)`;
    - `before = snapshot()`; the rejected run raises; `snapshot() == before`;
    - `log.get("op-0001") is None`;
    - a following valid `run(de(T, U, op_id="op-0002"))` succeeds, which shows the transaction was rolled back cleanly.
- [ ] **Step 2: Run** `uv run pytest tests/apply/test_delete_edge.py -q`. Expected: FAIL (`KeyError: 'delete_edge'` in `HANDLERS`, or ImportError).
- [ ] **Step 3: Implement** `handle`, following the spec's validation order:
  - resolve `from_` then `to` (`NodeNotFound`);
  - `edges.live` (`EdgeNotFound`);
  - `edges.mark_deleted([e.id], op_id)`;
  - `log.record(LoggedOp(...))`;
  - `log.add_removals(op_id, [], [e.id])`;
  - return the `OpResult`.

  No `try`/`except` and no commit, because `apply_op` owns the transaction.
- [ ] **Step 4: Register.** Add `HANDLERS["delete_edge"] = delete_edge.handle`. Tests reach the handler only through `apply_op`.
- [ ] **Step 5: Run** `uv run pytest tests/apply/test_delete_edge.py tests/test_layers.py -q`. Expected: PASS.
- [ ] **Step 6:** Update the README status table and commit: `feat(apply): delete_edge handler (soft delete + removals)`.

### Task 2: `contains` deletes, `effective_tags`, move

**Files:** Test: `tests/apply/test_delete_edge.py`. The handler should need no change. If one does, it's a bug in Task 1.

- [ ] **Step 1: Write the tests.**
  - `test_delete_contains_leaves_child_parentless`:
    - Setup: `seed_tags`. Read `cid` = the id of contains T→K.
    - `run(de(T, K, "contains"))`.
    - Assert:
      - `edge_row(cid).deleted_by == "op-0001"`;
      - `store.edges.live_parent(k.id) is None`;
      - K is still in `live_names` and `nodes.deleted_by` is NULL for K;
      - `eff(K) == ["kk"]`, having lost `c, d, s, tt`;
      - `eff(T) == ["c","d","s","tt"]`, unchanged.
  - `test_delete_contains_subtree_loses_ancestor_tags`: delete S→T `contains`. Then:
    - `eff(T) == ["tt"]` and `eff(K) == ["kk","tt"]`, so K keeps T's tag and loses `s, d, c`;
    - `eff(U) == ["c","d","s","uu"]`, unchanged;
    - T and K are both live, and T→K is still live.
  - `test_parentless_state_is_accepted`: after the contains delete, a later unrelated op (`run(de(T, U))` on a seeded reference, `op-0002`) applies fine. Nothing re-checks K's orphan state.
  - `test_move_k_from_t_to_u` (integration, uses the add_edge handler): mark it `pytest.mark.skipif("add_edge" not in HANDLERS, reason="add_edge handler not landed")`. Steps:
    - `op-0001` delete_edge T→K contains;
    - `op-0002` add_edge U→K contains, same human.

    Assert:
    - the old row has `deleted_by="op-0001"`;
    - `live_parent(k).from_id == u.id`;
    - `eff(K) == ["c","d","kk","s","uu"]`;
    - `removals` holds only the op-0001 edge row;
    - two `ops` rows, with seq 1 and 2;
    - `assert_removals_consistent`.
  - `test_repoint_reference` (same skipif): delete T→U, then add T→K references. The old row is dead with op-0001, the new row is live, and there are two distinct edge ids.
- [ ] **Step 2: Run** `uv run pytest tests/apply/test_delete_edge.py -q`. Expected: PASS for the new non-skipped tests, since the handler is unchanged. If any fail, fix the handler, not the test.
- [ ] **Step 3:** Commit: `test(apply): delete_edge on contains edges, effective_tags, move`.

### Task 3: Lifecycle: dead rows, re-add, cascades

- [ ] **Step 1: Write the tests.**
  - `test_already_dead_is_edge_not_found`:
    - `id = seed_ref(T, U)`; `op-0001` deletes it.
    - `op-0002` deletes the same triple. It raises `EdgeNotFound` with `"op-0002 edge_not_found: no live references edge 'conn/db/sc/t' -> 'conn/db/sc/u'"`.
    - `edge_row(id).deleted_by` is still `"op-0001"`. There's no `op-0002` log row and no `op-0002` removals.
  - `test_dead_duplicate_keeps_original_deleted_by`: `a = seed_ref(T, U)`. Delete it (op-0001). `b = seed_ref(T, U)` re-adds it in setup (a new row id). Delete again (op-0002). Then:
    - `edge_row(a).deleted_by == "op-0001"` and `edge_row(b).deleted_by == "op-0002"`;
    - `removals` is `{("op-0001","edge",str(a)), ("op-0002","edge",str(b))}`;
    - `log.removals("op-0002") == ([], [b])`;
    - `assert_removals_consistent`.
  - `test_readd_via_add_edge_then_redelete`: the same flow, but with the add_edge handler as op-0002 (skipif not registered). There are three ops rows, and the add_edge row writes no removals.
  - `test_cascade_removed_edge_endpoint_not_live`:
    - Setup: `r = seed_ref(U, K)`. Remove T and K as a cascade would:
      - if `"delete_node" in HANDLERS`, run `op-0001` `delete_node T`;
      - otherwise simulate in setup: `nodes.mark_deleted([t,k], "op-0001")`, `edges.mark_deleted([S→T, T→K, r], "op-0001")`, with a matching `log.record` and `add_removals`, then commit.
    - `run(de(U, K, op_id="op-0002"))` gives `"op-0002 node_not_found: to 'conn/db/sc/t/k' is not a live node"`.
    - `edge_row(r).deleted_by` is still `"op-0001"`, and `removals` for op-0002 is empty.
  - `test_cascade_removed_edge_name_recreated` (skipif add_node is not registered):
    - After the cascade above, ingest `add_node K` under U recreates the name with a new id.
    - `de(U, K)` gives `EdgeNotFound`. The lookup uses the new id, so the old row stays dead with `"op-0001"`.
  - `test_nodes_never_touched`: across a mixed run (delete a reference, delete a contains edge, then a rejected op), the `nodes` table rows are identical to the start.
- [ ] **Step 2: Run** `uv run pytest tests/apply -q`. Expected: PASS.
- [ ] **Step 3:** Commit: `test(apply): delete_edge lifecycle (dead rows, re-add, cascade)`.

### Task 4: Parse-level cases (FOUNDATION parse tests, not this handler)

These belong in the foundation's `tests/apply/test_parse.py`. If they're missing there, add them, clearly marked `# delete_edge parse cases`. They call `parse_op(d)` directly and never touch a db.

- [ ] `test_parse_delete_edge_ingest_actor_not_allowed`: an ingest actor with otherwise valid keys gives `ActorNotAllowed`, with `str == "op-0001 actor_not_allowed: ingest may not delete_edge"`.
- [ ] `test_parse_delete_edge_ingest_with_bad_keys_still_actor_not_allowed`: ingest and a missing `to` still give `ActorNotAllowed` (spec, Foundation changes requested 4).
- [ ] `test_parse_delete_edge_unknown_type`: `type="likes"` gives `MalformedOp`.
- [ ] `test_parse_delete_edge_missing_key[from|to|type]` gives `MalformedOp`.
- [ ] `test_parse_delete_edge_extra_key`: adding `description` gives `MalformedOp`.
- [ ] `test_parse_delete_edge_non_string_endpoint`: `from=7` gives `MalformedOp`.
- [ ] `test_parse_delete_edge_ok`: the result is a `DeleteEdge` with `from_`, `to`, `type` and `raw` set.
- [ ] Run `uv run pytest tests/apply -q`. Expected: PASS. Commit: `test(parse): delete_edge parse cases`.

### Task 5: Whole-file check

- [ ] Run `uv run gen-fixture --seed 42`, `uv run load fixtures/seed.jsonl --db /tmp/g.db`, then `uv run apply fixtures/ops.jsonl --db /tmp/g.db`. Expected: no `delete_edge` rejections on stderr.

  Spot-check: the number of `ops` rows with `op='delete_edge'` equals the number of `removals` rows of kind `edge` belonging to `delete_edge` ops, and equals the fixture's `delete_edge` line count. Any mismatch is a bug.
- [ ] Update the README if commands or status changed. Commit if anything changed.
