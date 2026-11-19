# `edit_node` Handler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (or superpowers:subagent-driven-development) to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `apply` handles human `edit_node`. It replaces the given authored fields on the live node with that name, and logs the previous values of exactly those fields in `ops.before`.

**Architecture:** One handler, `graph_undo/api/edit_node.py: handle(ctx: OpContext, op: EditNode) -> OpResult`, registered in `api/registry.HANDLERS["edit_node"]`. It reaches the db only through `ctx.store.nodes` and `ctx.store.log`. The checks that need no graph live in the foundation's `parse_op`.

**Tech Stack:** Python ≥ 3.11 stdlib (`json`), uv, pytest.

**Spec:** `docs/superpowers/specs/2026-11-19-op-edit-node-design.md`
**Depends on:** `docs/superpowers/plans/2026-11-19-apply-foundation.md`, which must be complete. It provides `Store`, `NodeRepo`, `OpLogRepo`, `EditNode`, `parse_op`, `apply_op`, `HANDLERS`, `tests/apply/conftest.py` (`store`, `run`, `log_row`, `live_names`).

## Global Constraints

- **Commands:** every command runs as `uv run …` (`uv run pytest tests/apply/test_edit_node.py -q`). Never call bare `python` or `pytest`.
- **Git:** commit directly on `main`, with no branches or worktrees.
- **Runtime:** stdlib only. No SQL outside `graph_undo/db/`. `tests/test_layers.py` must stay green.
- **Data model:** no new node, edge or log columns.
- **README:** update the status table (`edit_node` ✓) and the apply notes in the same commit as the handler.

## Review Focus

1. **`before`:** holds exactly the keys in `set`, with old values, as compact sorted JSON. It never holds the new values or the untouched fields. Test: `test_before_json_exact`.
2. **Recreated name:** the edit lands on the new live id, and the dead row is byte-unchanged. Test: `test_edit_recreated_name_hits_live_row`.
3. **A no-change edit:** it is `applied` with a log row, not `noop` (spec decision). Test: `test_noop_values_still_applied`.
4. **A rejected op writes nothing:** no log row and no field change, and its `str(e)` is exact. Test: `test_unknown_name_writes_nothing`.
5. **No extra writes for `effective_tags`:** descendants change through the view alone. Tests: `test_ancestor_tag_*`.

---

### Task 1: `edit_node` handler

**Files:**
- Create: `src/graph_undo/api/edit_node.py`
- Modify: `src/graph_undo/api/registry.py` (one `HANDLERS` entry)
- Test: `tests/apply/test_edit_node.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: `NodeRepo.live_by_name`, `NodeRepo.update_fields`, `OpLogRepo.record`, `LoggedOp`, `canonical`, `NodeNotFound`.
- Produces: `def handle(ctx: OpContext, op: EditNode) -> OpResult`.

**Test setup:**
- The conftest `BASE` tree: `conn` → `conn/db` → `conn/db/sc` → `conn/db/sc/t` (→ column `conn/db/sc/t/k`) and `conn/db/sc/u`, plus a reference `t → u`. Every node has `description "d"`, `owner "o"`, `tags []`. If the foundation conftest uses other literals, change these to match.
- Local helpers in the test file:
  - `edit(store, qn, op_id="op-0100", **set)` calls `run(store, op_id=…, actor={"kind":"human","name":"ana"}, op="edit_node", node=qn, set=set)`.
  - `fields(store, qn)` returns `(description, owner, tags)` through `store.nodes.live_by_name`.
  - `eff(store, qn)` reads the `effective_tags` view through `store.con`.
  - `snapshot(store)` dumps all `nodes`, `edges` and `ops` rows.

- [ ] **Step 1: Write the failing tests** in `tests/apply/test_edit_node.py`.

  **Single fields, combinations, untouched fields**
  - `test_edit_description`: `edit(t, description="new")` gives `fields(t) == ("new","o",())` and `OpResult(effect="applied", node_id=<t id>, edge_id=None)`.
  - `test_edit_owner`: `owner="bob"` gives `("d","bob",())`.
  - `test_edit_tags`: `tags=["pii"]` gives `("d","o",("pii",))`.
  - `test_edit_all_three`: `description="x", owner="y", tags=["b","a"]` gives `("x","y",("a","b"))`. `log_row.before == '{"description":"d","owner":"o","tags":[]}'`.
  - `test_other_fields_untouched`: take a full row snapshot of `t` before `edit(t, owner="z")`. Afterwards only `owner` differs: `id`, `qualified_name`, `type`, `description`, `tags` and `deleted_by` are equal. The `edges` table is unchanged, and the other nodes' rows are unchanged.
  - `test_successive_edits_accumulate`: `owner="a"`, then `description="b"`, gives `("b","a",())`. The second `before == '{"description":"d"}'`.

  **Tag normalisation**
  - `test_tags_unsorted_sorted`: `tags=["z","a","m"]` is stored as `'["a","m","z"]'` (raw column).
  - `test_tags_duplicates_removed`: `tags=["pii","gold","pii"]` gives `("gold","pii")`.
  - `test_tags_empty_list_clears`: first `tags=["a","b"]`, then `tags=[]`. Own tags are `()`, and the second `before == '{"tags":["a","b"]}'`.
  - `test_payload_keeps_raw_tags`: after `tags=["b","a","b"]`, `log_row.payload` contains `"tags":["b","a","b"]` (as received), while the stored tags are `["a","b"]`.

  **The op-log row**
  - `test_log_row_shape`: after `edit(t, op_id="op-0100", owner="bob")`, the row has `op="edit_node"`, `actor_kind="human"`, `actor_name="ana"`, `effect="applied"`, `node_id=<t id>`, `edge_id IS NULL`, and `payload == canonical(raw op dict)`. There are no `removals` rows for `op-0100`.
  - `test_before_json_exact`: set `tags=["x"]`, then `edit(owner="p", tags=["y"])`. `before == '{"owner":"o","tags":["x"]}'`: exact string, sorted keys, compact, and no `description` key.

  **`effective_tags`**
  - `test_ancestor_tag_flows_to_descendants`: `edit(conn/db, tags=["gold"])`. `eff` of `sc`, `t`, `u` and `k` each contains `"gold"`, and `eff(conn)` doesn't. Only one `nodes` row changed (compare `snapshot`).
  - `test_ancestor_tag_removal_drops_unless_own`: `edit(conn/db, tags=["gold","pii"])`, then `edit(conn/db/sc/t, tags=["pii"])`, then `edit(conn/db, tags=["gold"])`. Now `"pii"` is in `eff(t)` (its own tag) and in `eff(k)` (inherited from `t`), but not in `eff(u)` or `eff(sc)`. `"gold"` is still in all four.

  **Live and dead rows**
  - `test_edit_recreated_name_hits_live_row`:
    1. `run delete_node conn/db/sc/t` (op-0101). Record the dead row's `id` and its fields.
    2. Human `add_node conn/db/sc/t` under `conn/db/sc` (op-0102) with `description "new-d"`, `owner "o2"`, `tags []`.
    3. `edit(t, op_id="op-0103", description="edited")`.

    The live row (new id ≠ old id) has `description "edited"` and `owner "o2"`. The dead row is unchanged (`deleted_by == "op-0101"`, `description "d"`). `log_row("op-0103").node_id == new id`, and `before == '{"description":"new-d"}'`.
  - `test_unknown_name_writes_nothing`: `edit("conn/nope", op_id="op-0100", owner="x")` raises `NodeNotFound`. `str(e) == "op-0100 node_not_found: node 'conn/nope' is not a live node"`, `snapshot` is unchanged, and `log_row("op-0100") is None`.
  - `test_dead_only_name_not_found`: `run delete_node conn/db/sc/u` (op-0101), then `edit(u, op_id="op-0102", owner="x")` raises `NodeNotFound` with exact text `"op-0102 node_not_found: node 'conn/db/sc/u' is not a live node"`. The dead `u` row is unchanged, and there's no `op-0102` log row.
  - `test_rejected_then_valid_same_op_id`: a failed `op-0100` (unknown name) leaves no log row, so a valid `edit(t, op_id="op-0100", owner="x")` afterwards applies (it is not `skipped`, and there's no `OpIdConflict`).

  **No-op edit (spec decision: `applied`)**
  - `test_noop_values_still_applied`: `edit(t, owner="o")`, the same value as now. The result is `effect="applied"`. The log row exists with `effect="applied"` and `before == '{"owner":"o"}'`, and the fields are unchanged.
  - `test_noop_tags_after_normalisation`: own tags are `["a","b"]`, then `edit(tags=["b","a","a"])`. The result is `applied`, and `before == '{"tags":["a","b"]}'`.

  **Values that round-trip**
  - `test_unicode_round_trip`: `description="données ✓ 数据"`, `owner="Zoë"`, `tags=["é","ä"]` are read back exactly, with tags `("ä","é")` (code-point order). `before` and `payload` keep the characters unescaped (`ensure_ascii=False`).
  - `test_empty_strings_round_trip`: `description=""`, `owner=""` are stored as `""` (not NULL), and `tags=[""]` is stored as `[""]`.
  - `test_registry_dispatch`: `HANDLERS["edit_node"] is edit_node.handle`.

- [ ] **Step 2: Run, expected FAIL:** `uv run pytest tests/apply/test_edit_node.py -q`. The tests fail with `ImportError` / `KeyError: 'edit_node'`.

- [ ] **Step 3: Implement** `src/graph_undo/api/edit_node.py`:
  ```python
  def handle(ctx: OpContext, op: EditNode) -> OpResult: ...
  ```
  It follows the spec's order: resolve the name (or raise `NodeNotFound`), build `before`, call `update_fields`, `record`, then return.

- [ ] **Step 4: Run, expected PASS:** `uv run pytest tests/apply/test_edit_node.py -q`.

- [ ] **Step 5: Register** `"edit_node": edit_node.handle` in `HANDLERS`. Then run `uv run pytest -q`: the whole suite must pass, including `test_layers.py`.

- [ ] **Step 6: Update the README** (status table, plus a note that a no-change edit counts as `applied`). Then commit:
  ```bash
  git add src/graph_undo/api/edit_node.py src/graph_undo/api/registry.py tests/apply/test_edit_node.py README.md
  git commit -m "feat(apply): edit_node handler"
  ```

---

### Foundation parse tests (FOUNDATION-OWNED: they go in the foundation's `tests/apply/test_parse.py`, not in this plan's file)

These cases are listed here so they don't get lost. If the foundation plan already has them, check that the messages match the spec. Each one calls `parse_op(d)` on a valid human `edit_node` dict with one change:
- `test_parse_edit_empty_set`: `set={}` raises `MalformedOp`, `"op-0100 malformed: set must be a non-empty object"`.
- `test_parse_edit_set_not_object`: `set=["owner"]` raises `MalformedOp` (same message).
- `test_parse_edit_unknown_key`: `set={"colour":"red"}` raises `MalformedOp`, `"... set has unknown key 'colour'"`. The same goes for `set={"qualified_name":"x"}` and `set={"type":"table"}`.
- `test_parse_edit_owner_not_string`: `set={"owner":7}` raises `MalformedOp`, `"... set.owner must be a string"`. `owner=None` does too.
- `test_parse_edit_description_not_string`: `set={"description":["x"]}` raises `MalformedOp`, `"... set.description must be a string"`.
- `test_parse_edit_tags_not_list`: `set={"tags":"pii"}` raises `MalformedOp`, `"... set.tags must be a list of strings"`.
- `test_parse_edit_tags_non_string_item`: `set={"tags":["a",1]}` raises `MalformedOp` (same message).
- `test_parse_edit_ingest_actor`: `actor={"kind":"ingest",…}` raises `ActorNotAllowed`, `"op-0100 actor_not_allowed: edit_node is human only; actor kind is 'ingest'"`.
- `test_parse_edit_extra_top_key`: an extra top-level `parent` key raises `MalformedOp`. A missing `node` key does too.
- `test_parse_edit_normalises_tags`: `set={"tags":["b","a","b"]}` gives `EditNode.set["tags"] == ("a","b")`, while `raw` is unchanged.
