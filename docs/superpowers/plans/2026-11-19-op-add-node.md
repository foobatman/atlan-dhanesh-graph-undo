# `add_node` Op Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (or superpowers:subagent-driven-development) to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `apply` handles `add_node` for human and ingest actors as specified, with typed errors and an op-log row.

**Architecture:**
- **Handler:** `graph_undo/api/add_node.py: handle(ctx: OpContext, op: AddNode) -> OpResult`. It resolves names through `NodeRepo`, checks name → parent → type order, inserts the node and its `contains` edge, and records its `ops` row.
- **Registration:** `api/registry.py` `HANDLERS["add_node"]`.
- **Transaction:** `apply_op` owns it.

**Depends on:** `docs/superpowers/plans/2026-11-19-apply-foundation.md`, which must be done first. It provides `domain/`, `db/` repos, `Store.mint_node_id`, `apply_op`, and `tests/apply/conftest.py` (`store`, `run`, `live_names`, `log_row`).

**Tech Stack:** Python ≥ 3.11 stdlib (`sqlite3`, `json`, `uuid`), uv, pytest.

**Spec:** `docs/superpowers/specs/2026-11-19-op-add-node-design.md`

## Global Constraints

- **Commands:** every command runs as `uv run …` (`uv run pytest …`). Never call `python` or `pytest` bare.
- **Git:** commit directly on `main`. No branches or worktrees.
- **Runtime:** stdlib only. No SQL outside `graph_undo/db/`; `tests/test_layers.py` must stay green.
- **Data model:** don't add fields to nodes or edges.
- **README:** update it in the same commit as the logical change (the `apply` status row now lists `add_node`).

## Review Focus

1. **Ingest on a live name is a pure noop**, whatever its `type`, `parent` or parent liveness. Test: `test_ingest_noop_even_when_type_and_parent_differ`.
2. **A dead row with the same name must not block, and must stay untouched.** Test: `test_recreate_name_whose_only_row_is_dead`.
3. **Validation order is name → parent → type.** Test: `test_human_name_taken_wins_over_bad_parent`.
4. **A rejected op leaves the db byte-identical:** no node, no edge, no log row. Test: `test_rejected_op_writes_nothing`.
5. **Ids are `uuid5(NAMESPACE, op_id)` and stable across fresh dbs.** Test: `test_same_op_same_id_on_two_fresh_dbs`.

## Test conventions (`tests/apply/test_add_node.py`)

The conftest `BASE` uses ids `c d s t u k` for `conn`, `conn/db`, `conn/db/sc`, `conn/db/sc/t`, `conn/db/sc/u`, `conn/db/sc/t/k` (k is a column under t), plus one reference `u→t`. If the conftest's ids differ, adjust the literals. Local helpers in the test module:

```python
NS = uuid.UUID("6f1c3a52-0d8e-4c1e-9b7a-2f4d5e6a7b8c")       # literal; do not import from src
def human(op_id, qn, type, parent, description="d", owner="alice", tags=()): ...  # op dict
def ingest(op_id, qn, type, parent): ...                                          # op dict
def snapshot(store) -> tuple: ...   # all rows of nodes, edges, ops, removals, ordered by key
def kill(store, qn, op_id="op-0000"): ...  # inside transaction(store.con): mark_deleted the
    # live subtree of qn and live_touching edges, via repos only (no delete_node dependency)
def node_row(store, qn) -> Node   # store.nodes.live_by_name(qn)
def eff_tags(store, node_id)      # SELECT tags FROM effective_tags WHERE node_id=?  (test-side SQL is fine)
```

`run(store, **d)` parses the dict and calls `apply_op`, returning an `OpResult` or raising an `OpError`.

---

### Task 1: Create path (human and ingest)

**Files:** Create `src/graph_undo/api/add_node.py` and `tests/apply/test_add_node.py`. Modify `src/graph_undo/api/registry.py` and `README.md`.

- [ ] **Step 1: Write the failing tests.**
  - **`test_human_add_creates_node_and_edge`**
    - Action: `r = run(store, **human("op-0001", "conn/db/sc/x", "table", "conn/db/sc", description="hand", owner="bob", tags=["pii","gold","pii"]))`.
    - Assert `r.effect == "applied"` and `r.node_id == str(uuid.uuid5(NS, "op-0001"))`.
    - Assert `node_row(store, "conn/db/sc/x")` equals `Node(r.node_id, "conn/db/sc/x", "table", "hand", "bob", ("gold","pii"), None)`.
    - Assert `store.edges.live_parent(r.node_id)` has `id == r.edge_id`, `from_id == "s"` and type `contains`.
  - **`test_human_add_effective_tags_include_ancestors`**
    - Setup: inside a transaction, `store.nodes.update_fields("d", {"tags": ("gold",)})`.
    - Action: add `conn/db/sc/x` with `tags=["pii"]`.
    - Assert `eff_tags(store, r.node_id) == ["gold","pii"]`, and that the node's own tags are still `("pii",)`.
  - **`test_human_add_log_row`**
    - Assert `log_row(store, "op-0001")` has `op="add_node"`, `effect="applied"`, `actor_kind="human"`, `actor_name="alice"`, `node_id=r.node_id`, `edge_id=r.edge_id` and `before is None`.
    - Assert `json.loads(payload)` equals the op dict.
  - **`test_ingest_add_creates_blank_node`**
    - Action: `run(store, **ingest("op-0002", "conn/db/sc/t/z", "column", "conn/db/sc/t"))`.
    - Assert the node has `description == ""`, `owner == ""` and `tags == ()`.
    - Assert there is a `contains` edge `t → new`.
    - Assert the log row has `effect="applied"`, `actor_kind="ingest"` and `edge_id` set.
  - **`test_add_under_new_node`**
    - Add table `conn/db/sc/x` (op-0001), then column `conn/db/sc/x/c` (op-0002) under it.
    - Assert the column's `live_parent` has `from_id` equal to op-0001's `node_id`.
- [ ] **Step 2: Run** `uv run pytest tests/apply/test_add_node.py -q`. Expected: FAIL (`KeyError: 'add_node'` / ImportError).
- [ ] **Step 3: Implement** `handle(ctx, op: AddNode) -> OpResult` per spec §Semantics/§Writes (signature only shown here; uses `live_by_name`, `mint_node_id`, `nodes.insert`, `edges.insert`, `log.record`).
- [ ] **Step 4: Register:** `HANDLERS["add_node"] = add_node.handle` in `api/registry.py`. `run()` dispatches through it, so tests cannot pass before this step.
- [ ] **Step 5: Run** `uv run pytest -q`. Expected: PASS, including `tests/test_layers.py`.
- [ ] **Step 6: README.** Mark `add_node` done in the status table. Commit `feat(apply): add_node handler (create path)`.

### Task 2: Ingest upsert noop

- [ ] **Step 1: Write the failing tests.**
  - **`test_ingest_noop_on_live_name`**
    - Setup: `before = snapshot(store)` (minus the `ops` table).
    - Action: `r = run(store, **ingest("op-0003", "conn/db/sc/t", "table", "conn/db/sc"))`.
    - Assert `r.effect == "noop"` and `r.node_id == "t"`.
    - Assert the nodes, edges and removals tables are unchanged.
    - Assert the log row has `effect="noop"`, `node_id="t"`, `edge_id is None` and `before is None`.
  - **`test_ingest_noop_even_when_type_and_parent_differ`**
    - Action: `ingest("op-0003", "conn/db/sc/t", "column", "conn/db/sc/u")`. This gives a wrong type and a different parent, and a column under a table would even be valid.
    - Assert a noop. `t` is still a `table`, still under `s`, and `u` has no new children.
  - **`test_ingest_noop_even_when_parent_missing`**
    - Action: `ingest("op-0003", "conn/db/sc/t", "table", "nope/nowhere")`.
    - Assert a noop: no error, and a noop log row.
  - **`test_ingest_noop_keeps_human_fields`** (E4)
    - Setup: human-add `conn/db/sc/x` with `owner="bob"` and `tags=["certified"]`.
    - Action: ingest the same name.
    - Assert a noop, and that the node still has `owner == "bob"`, `tags == ("certified",)` and the same id.
- [ ] **Step 2: Run** the tests. Expected: FAIL only if Task 1 left out the noop branch. Otherwise they PASS, which is acceptable, and the tests stand as regression guards.
- [ ] **Step 3: Implement** the noop branch if it's missing.
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit** `test(apply): add_node ingest upsert noop`.

### Task 3: Rejections and validation order

- [ ] **Step 1: Write the failing tests.** Each `pytest.raises(...) as ei` test also asserts `str(ei.value)` exactly.
  - **`test_human_on_live_name_name_taken`**
    - Action: `human("op-0001", "conn/db/sc/t", "table", "conn/db/sc")`.
    - Expect `NameTaken`, with `str == "op-0001 name_taken: conn/db/sc/t is already live as node t"`.
  - **`test_parent_never_existed_node_not_found`**
    - Action: parent `"conn/db/zz"`.
    - Expect `"op-0001 node_not_found: parent 'conn/db/zz' is not a live node"`. Run it for both a human and an ingest op (parametrize).
  - **`test_parent_dead_node_not_found`**
    - Setup: `kill(store, "conn/db/sc/u")`.
    - Action: add column `conn/db/sc/u/c` under `conn/db/sc/u`.
    - Expect `NodeNotFound`, for both human and ingest.
  - **`test_type_order_same_level`**
    - Action: table `conn/db/sc/t2` under table `conn/db/sc/t`.
    - Expect `"op-0001 type_order: table conn/db/sc/t2 cannot go under table conn/db/sc/t: contains must go exactly one level down"`.
  - **`test_type_order_skip_level`**: a column under the schema `conn/db/sc` gives `TypeOrderViolation`.
  - **`test_type_order_column_as_parent`**: a column under the column `conn/db/sc/t/k` gives `TypeOrderViolation`.
  - **`test_type_order_upward`**: a `schema` under the table `conn/db/sc/t` (connection-as-child direction) gives `TypeOrderViolation`.
  - **`test_connection_cannot_be_added`**
    - Action: `human(..., "conn2", "connection", "conn")`.
    - Expect `"op-0001 type_order: connection conn2 cannot go under connection conn: contains must go exactly one level down"`.
    - Run it for ingest too. Root-connection decision: spec §Semantics.
  - **`test_ingest_type_order_enforced_when_name_free`**: an ingest table under a table gives `TypeOrderViolation`.
  - **`test_human_name_taken_wins_over_bad_parent`**: `human("op-0001", "conn/db/sc/t", "table", "nope")` gives `NameTaken`, not `NodeNotFound`.
  - **`test_parent_check_wins_over_type_order`**: a `database` under a missing parent gives `NodeNotFound`.
  - **`test_rejected_op_writes_nothing`** (parametrized over the three error cases above)
    - Setup: `before = snapshot(store)`.
    - Action: run the op inside `pytest.raises(OpError)`.
    - Assert `snapshot(store) == before`, `log_row(store, op_id) is None`, and `uuid5(NS, op_id)` is not in `nodes`.
  - **`test_rejected_op_id_can_be_reused`**
    - Setup: an `op-0001` that hits `NameTaken`.
    - Action: a valid `op-0001` with a different name.
    - Assert it is applied. This proves nothing was logged for the rejected one.
- [ ] **Step 2: Run** `uv run pytest tests/apply/test_add_node.py -q`. Expected: FAIL on the message text or the order, until the handler matches the spec.
- [ ] **Step 3: Implement** the checks in spec order, with the spec's message templates.
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(apply): add_node validation and errors`.

### Task 4: Dead rows, recreated parents, determinism

- [ ] **Step 1: Write the failing tests.**
  - **`test_recreate_name_whose_only_row_is_dead`**
    - Setup: `kill(store, "conn/db/sc/t")`, then `dead = store.nodes.by_id("t")`.
    - Action: `r = run(store, **ingest("op-0005", "conn/db/sc/t", "table", "conn/db/sc"))`.
    - Assert `r.effect == "applied"`, `r.node_id != "t"`, and `store.nodes.by_id("t") == dead` (still `deleted_by="op-0000"`, fields frozen).
    - Assert the dead edge `s→t` is still dead, and that the new live edge `s→new` has a different edge id.
    - Repeat with a human op: allowed, and the name is now live with the human's fields.
  - **`test_parent_resolved_to_recreated_node`**
    - Setup: kill `conn/db/sc`. Ingest-recreate `conn/db/sc` under `conn/db` (op-0005).
    - Action: ingest table `conn/db/sc/t` (op-0006).
    - Assert its parent edge has `from_id` equal to op-0005's `node_id`, not `"s"`.
  - **`test_ingest_after_recreate_is_noop`**: recreate a dead name (op-0005), then ingest it again (op-0006). Assert a noop, with `node_id` equal to op-0005's id.
  - **`test_same_op_same_id_on_two_fresh_dbs`**
    - Setup: two `store`-style fixtures in separate `tmp_path` subdirs.
    - Action: apply the same human op to each.
    - Assert both `node_id`s and both edge ids are equal, and both equal `uuid5(NS, op_id)`.
  - **`test_rerun_same_op_is_skipped`**: apply op-0001, then apply the identical op again. Assert `effect == "skipped"`, a single live row, and a single `ops` row (the `apply_op` guard protects minting).
- [ ] **Step 2: Run** the tests. Expected: PASS if Tasks 1–3 are correct. A FAIL here means the handler read dead rows or used `by_id` for names.
- [ ] **Step 3: Fix** whatever failed, then run `uv run pytest -q`. Expected: PASS.
- [ ] **Step 4: Commit** `test(apply): add_node dead rows and determinism`.

### Parse-level cases (for the foundation's `parse_op` test file, NOT `test_add_node.py`)

These are owned by the foundation plan. If they're missing there, add them there:
- **Missing `parent`** (either actor): `MalformedOp`.
- **`parent: null`**: `MalformedOp`.
- **Missing `description`, `owner` or `tags` on a human op**: `MalformedOp`.
- **Ingest op with any of `description`, `owner` or `tags`**: `ActorNotAllowed`.
- **`type: "view"`**: `MalformedOp`.
- **Bad tags**: `tags: "pii"` or `tags: [1]` gives `MalformedOp`.
- **Unknown extra key**: `MalformedOp`.
- **`op_id: "7"`**: `MalformedOp`.
- **Duplicate and unsorted human tags:** `["pii","gold","pii"]` gives `AddNode.tags == ("gold","pii")` (Foundation change requested 3).
