# `delete_node` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (or superpowers:subagent-driven-development) to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `apply` handles human `delete_node`: cascade-flag the live subtree and every live edge touching it, and record exactly those rows in `removals` plus one op-log row, in one transaction.

**Architecture:** `graph_undo/api/delete_node.py: handle(ctx, op: DeleteNode) -> OpResult` resolves the root, calls `nodes.live_subtree_ids`, `edges.live_touching`, `edges.mark_deleted`, `nodes.mark_deleted`, `log.record`, `log.add_removals` (in that order) and returns the counts. All SQL stays in `graph_undo/db/`. Registered as `HANDLERS["delete_node"]`.

**Tech Stack:** Python ≥ 3.11 stdlib (`sqlite3`, `json`), uv, pytest.

**Spec:** `docs/superpowers/specs/2026-11-19-op-delete-node-design.md`

**Depends on:** `docs/superpowers/plans/2026-11-19-apply-foundation.md` Tasks 1–4 (package, `parse_op`, schema v2, repositories, registry, `tests/apply/conftest.py` helpers `store`, `run`, `live_names`, `log_row`, `snapshot`). Independent of the other four op plans; tests that need another handler are marked and skip until it is registered.

## Global Constraints

- **Commands:** every command runs as `uv run …` (`uv run pytest tests/apply/test_delete_node.py -q`).
- **Git:** commit directly on `main`; no branches or worktrees.
- **Runtime:** stdlib only. No new fields on nodes or edges.
- **README:** update the status table (`delete_node` ✓) and the errors table in the same commit as the handler registration.
- **Layers:** no SQL in `api/`; `tests/test_layers.py` must stay green.
- **Error text:** `NodeNotFound` message is exactly `node '{qn}' is not a live node`.

## Review Focus

1. **Exactness:** flagged set == `removals` set, both directions, per op. Test: `test_removals_equal_flagged_both_directions`.
2. **Dead rows untouched:** earlier `deleted_by` values survive, no extra `removals` rows. Tests: `test_dead_reference_keeps_old_deleted_by`, `test_moved_out_child_survives`.
3. **Recreated name:** second incarnation's delete touches only new rows. Test: `test_delete_recreated_node_only_new_rows`.
4. **Atomicity:** a failure after flagging leaves the db unchanged. Test: `test_failure_mid_handler_rolls_back`.
5. **Set-based SQL:** op-0001 (614 nodes) in < 1 s, constant statement count. Tests: `test_seed42_op0001`, `test_statement_count_constant`.

---

### Task 1: Repository support (only if the foundation lacks it)

**Files:** Modify `src/graph_undo/db/nodes.py`, `src/graph_undo/db/edges.py`. Test: `tests/apply/test_repos.py`.

**Interfaces:** `NodeRepo.mark_deleted(node_ids, op_id) -> int`, `EdgeRepo.mark_deleted(edge_ids, op_id) -> int` (rows updated). Spec "Foundation changes requested" 1–2.

- [ ] **Step 1: Write failing tests:** `test_mark_deleted_returns_rowcount` (3 live ids + 1 dead id → returns 3; dead row's `deleted_by` unchanged); `test_bulk_methods_accept_2000_ids` (`live_touching`, `mark_deleted`, `add_removals` with 2,000 ids, no `OperationalError: too many SQL variables`).
- [ ] **Step 2: Run** `uv run pytest tests/apply/test_repos.py -q` → expected FAIL.
- [ ] **Step 3: Implement:** pass id lists as `json.dumps(ids)` into `… IN (SELECT value FROM json_each(?))`; return `cur.rowcount`. `add_removals` = one `INSERT … SELECT … FROM json_each(?)` per kind.
- [ ] **Step 4: Run** → expected PASS; full suite green.
- [ ] **Step 5: Commit** `feat(db): set-based mark_deleted returns rowcount`.

### Task 2: Handler and its tests

**Files:** Create `src/graph_undo/api/delete_node.py`, `tests/apply/test_delete_node.py`.

**Interface:** `def handle(ctx: OpContext, op: DeleteNode) -> OpResult` (no body here; behaviour per spec "Semantics").

**Test fixture `dstore`** (local to the test file; loaded with the foundation's `load_seed` into `tmp_path`, wrapped in `Store`):

```
c   conn                    tags [conn-tag]
d   conn/db       database  tags [db-tag]
s   conn/db/sc    schema    tags [pii]          <- usual delete target
t   conn/db/sc/t  table     k conn/db/sc/t/k column, k2 conn/db/sc/t/k2 column
u   conn/db/sc/u  table
s2  conn/db/sc2   schema;   v conn/db/sc2/v table tags [gold]; w conn/db/sc2/w table
contains: c>d d>s d>s2 s>t s>u t>k t>k2 s2>v s2>w
references: t->v (outbound), v->u (inbound), t->u (internal), w->v (unrelated)
second tree: c9 conn9 > d9 conn9/db; reference d9 -> d (cross-tree, into d)
```

**Test helpers** (in the test file): `ids(dstore)` → `{qn: id}` captured before the op; `eid(dstore, f, t, type)` → edge id; `flagged(store, op_id)` → `(set node ids, set edge ids)` with `deleted_by = op_id`; `earlier_op(store, op_id, *, dead_nodes=(), dead_edges=(), new_contains=())` simulates an already-applied `delete_edge`/move purely through repositories in one transaction (mark, `record`, `add_removals`, `edges.insert`), so these tests need no other handler. `delete(store, qn, op_id="op-0100")` → `run(store, op="delete_node", node=qn, op_id=op_id)`.

- [ ] **Step 1: Write the failing tests** (`tests/apply/test_delete_node.py`):

  *Cascade shape*
  - `test_leaf_delete`: delete `conn/db/sc/t/k`. Flagged nodes == {k}; flagged edges == {t>k}. `live_names` lacks only k. `OpResult.removed_nodes == 1`, `removed_edges == 1`.
  - `test_leaf_with_reference`: insert a live reference k->w with `edges.insert`; delete k → flagged edges == {t>k, k->w}.
  - `test_subtree_delete`: delete `conn/db/sc`. Flagged nodes == {s,t,u,k,k2}; flagged edges == {d>s, s>t, s>u, t>k, t>k2, t->v, v->u, t->u}. Survivors c,d,s2,v,w,c9,d9 live; edges c>d, d>s2, s2>v, s2>w, w->v, c9>d9, d9->d live.
  - `test_parent_edge_removed`: after deleting sc, `edges.live_parent(s_id)` is `None` and d>s has `deleted_by == "op-0100"`.
  - `test_inbound_and_outbound_references_removed`: after deleting sc, edges t->v and v->u are flagged; v is still live.
  - `test_delete_root_connection`: delete `conn`. Every node of the `conn` tree (10) flagged, every edge touching it flagged, including cross-tree d9->d. c9 and d9 live; c9>d9 live. Counts 10 nodes / 14 edges.
  - `test_cross_tree_reference_into_subtree`: delete `conn/db` → d9->d flagged, c9/d9 live.

  *Bookkeeping exactness*
  - `test_removals_equal_flagged_both_directions`: after deleting sc: `log.removals("op-0100")` == `flagged(store, "op-0100")` as sets; every removals row's target has `deleted_by == "op-0100"`; no row with that `deleted_by` lacks a removals row.
  - `test_deleted_by_values`: each flagged node and edge has `deleted_by == "op-0100"` exactly; every other row is `NULL`.
  - `test_counts_match_removals`: `OpResult.removed_nodes == len(removals nodes) == 5`, `removed_edges == len(removals edges) == 8`.
  - `test_op_log_row`: `log_row(store, "op-0100")` has `op='delete_node'`, `actor_kind='human'`, actor name from `run`, `effect='applied'`, `node_id == s_id`, `edge_id IS NULL`, `before IS NULL`, `payload` == compact sorted JSON of the op dict; `seq` = previous max + 1.
  - `test_op_result`: equals `OpResult("op-0100", "applied", node_id=s_id, edge_id=None, removed_nodes=5, removed_edges=8)`.

  *Previously dead rows*
  - `test_dead_reference_keeps_old_deleted_by`: `earlier_op("op-0050", dead_edges=[t->v])`; delete sc as op-0100. t->v still has `deleted_by == "op-0050"`; it is not in `removals("op-0100")`; `removals("op-0050")` unchanged (== {t->v}); op-0100 flagged edges == the 7 others.
  - `test_moved_out_child_survives`: `earlier_op("op-0050", dead_edges=[s>u], new_contains=[(s2,u)])`; delete sc. u live with live parent s2; old s>u keeps `deleted_by == "op-0050"`, absent from `removals("op-0100")`; u's references t->u and v->u: t->u flagged (t removed), v->u **survives** (both ends live).
  - `test_moved_in_child_removed`: `earlier_op("op-0050", dead_edges=[s2>v], new_contains=[(s,v)])`; delete sc. v flagged with op-0100 and in its removals; new s>v flagged by op-0100; old s2>v keeps `"op-0050"`; w->v flagged (touches v); v's tag `gold` gone from `effective_tags`.
  - `test_no_live_edge_to_dead_node`: after each of the deletes above, every `live_edges` row has both endpoints in `live_nodes`.

  *Derived state and names*
  - `test_survivor_effective_tags_unchanged`: `effective_tags` rows for survivors before == after deleting sc; removed ids have no row.
  - `test_name_freed_repo_level`: after deleting t, `nodes.live_by_name("conn/db/sc/t")` is `None`; `nodes.insert` of a new `Node` with that name and a new id succeeds; old row still present via `by_id` with `deleted_by == "op-0100"`.
  - `test_name_freed_add_node` *(skip unless `"add_node" in HANDLERS`)*: after deleting t, human `add_node conn/db/sc/t` under sc succeeds; new id != old id.
  - `test_second_delete_same_name`: delete sc (op-0100), then delete sc (op-0101) raises `NodeNotFound`; `str(e) == "op-0101 node_not_found: node 'conn/db/sc' is not a live node"`; no op-0101 log row.
  - `test_delete_descendant_of_deleted`: after deleting sc, delete `conn/db/sc/t` → `NodeNotFound`, same template.
  - `test_delete_recreated_node_only_new_rows`: delete t (op-0100); recreate via repos (`nodes.insert` t′ named `conn/db/sc/t` under s, plus column k′ `conn/db/sc/t/k`, contains s>t′, t′>k′, reference t′->v) in `earlier_op("op-0101", …)`-style setup; delete `conn/db/sc/t` as op-0102. Flagged by op-0102 == {t′, k′} / {s>t′, t′>k′, t′->v}; original t, k, k2 and their edges still `deleted_by == "op-0100"`; `removals("op-0100")` unchanged; `removals("op-0102")` contains no original id.

  *Rejections and atomicity*
  - `test_unknown_name_writes_nothing`: `snapshot` before; delete `conn/db/nope` as op-0100 → `NodeNotFound`, `str(e) == "op-0100 node_not_found: node 'conn/db/nope' is not a live node"`; `snapshot` after == before (no flags, no `ops`, no `removals`).
  - `test_failure_mid_handler_rolls_back`: monkeypatch `store.log.add_removals` to raise `RuntimeError`; delete sc raises; `snapshot` unchanged, s still live.
  - `test_rerun_same_op_skipped`: apply the same delete op twice → second result `effect == "skipped"`, snapshot unchanged.

  *Performance shape*
  - `test_statement_count_constant`: with `con.set_trace_callback` counting statements, deleting k and deleting conn issue the same number of statements (± 0).

  *Seed-42 integration* (load `generate_seed(42)`; take `ops[0]` from `generate_ops(42, …)`; this needs only the `delete_node` handler)
  - `test_seed42_op0001`: `apply_op` on op-0001 (`postgres/ops/core`): `removed_nodes == 614`, `removed_edges == 774`; time < 1 s; removed qualified names == names live in `replay(nodes, edges, [])` minus names live in `replay(nodes, edges, [ops[0]])`; removed reference pairs (by name) == replay refs diff (160); `removals` == flagged set; every live edge has live endpoints.
  - `test_seed42_e5_e6_keep_earlier_flags` *(skip unless all five handlers registered)*: apply full `ops.jsonl`. For op-0247 (E5): every edge with `deleted_by == "op-0001"` before it still has it after; `removals("op-0247")` ∩ `removals("op-0001")` == ∅. For op-0248 (E6): its removed node ids are all not in the seed; `removals("op-0001")` unchanged in size (614 / 774).

- [ ] **Step 2: Run** `uv run pytest tests/apply/test_delete_node.py -q` → expected FAIL (`ImportError: graph_undo.api.delete_node`).
- [ ] **Step 3: Implement** `handle`: `live_by_name` → `NodeNotFound(op.op_id, f"node '{op.node}' is not a live node")`; `live_subtree_ids`; `live_touching`; `edges.mark_deleted`; `nodes.mark_deleted` (assert rowcounts); `log.record(LoggedOp(...))`; `log.add_removals`; return `OpResult`. No loops issuing SQL.
- [ ] **Step 4: Run** → expected PASS (the two marked tests may skip).

### Task 3: Register, README, commit

**Files:** Modify `src/graph_undo/api/registry.py`, `README.md`.

- [ ] **Step 1:** add `"delete_node": delete_node.handle` to `HANDLERS`.
- [ ] **Step 2: Run** `uv run pytest -q` → full suite PASS, including `tests/test_layers.py`.
- [ ] **Step 3:** README: status row `apply: delete_node` done; errors table row for `node_not_found` template; note that deletes write `removals`.
- [ ] **Step 4: Commit** `feat(apply): delete_node cascade with exact removals log`.

---

### Parse tests requested from the foundation plan (Task 2, `tests/apply/test_parse.py`)

Not in this plan's test file; append under "parse tests requested by op plans":
- `test_parse_delete_node_ingest_rejected`: ingest `{"op":"delete_node","node":"a"}` → `ActorNotAllowed`, `str(e)` starts with `"op-0001 actor_not_allowed: "`.
- `test_parse_delete_node_extra_key`: human with extra `"cascade": true` → `MalformedOp`.
- `test_parse_delete_node_missing_node`: no `node` key → `MalformedOp`.
- `test_parse_delete_node_node_not_string`: `"node": 7` → `MalformedOp`.
- `test_parse_delete_node_ok`: returns `DeleteNode(op_id, Actor("human", …), raw, node="a")`.
