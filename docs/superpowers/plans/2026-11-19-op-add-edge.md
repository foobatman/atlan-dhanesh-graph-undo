# `add_edge` Operation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (or superpowers:subagent-driven-development) to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `apply` accepts `add_edge` ops from human and ingest actors. It creates a new live edge row with a new id, writes one op-log row, and rejects each invalid case with the foundation's typed `OpError`.

**Architecture:**
- **Handler:** one module, `graph_undo/api/add_edge.py`, with `handle(ctx: OpContext, op: AddEdge) -> OpResult`, registered in `api/registry.HANDLERS["add_edge"]`.
- **Database access:** only through `NodeRepo`, `EdgeRepo` and `OpLogRepo`, with no SQL in `api/`.
- **Transactions:** owned by `apply_op`.

**Tech Stack:** Python ≥ 3.11 stdlib, SQLite, uv, pytest.

**Spec:** `docs/superpowers/specs/2026-11-19-op-add-edge-design.md`

**Depends on:** `docs/superpowers/plans/2026-11-19-apply-foundation.md`, which provides:
- `AddEdge`, `parse_op` and the errors;
- the repositories;
- `apply_op` and `HANDLERS`;
- `tests/apply/conftest.py` with `store`, `run`, `live_names` and `log_row`.

This plan does **not** depend on any other op plan. Test setup that needs a deleted edge, a deleted subtree or a recreated node writes through the foundation repositories directly (see the helpers below).

## Global Constraints

- **Commands:** every command runs as `uv run …` (`uv run pytest …`). Never call `python` or `pytest` directly.
- **Git:** commit directly on `main`, with no branches or worktrees.
- **Runtime:** stdlib only, and no new fields on nodes or edges.
- **README:** update the status table, ops section and layout in the same commit as the handler, or the one right after it.
- **Errors:** foundation classes only. `str(e)` is `"<op_id> <code>: <message>"`, using the spec's templates exactly.

## Review Focus

1. **Dead identical edge:** a new row, the dead row byte-identical, never revived. Test: `test_readd_identical_to_dead_reference_inserts_new_row`.
2. **Identical live `contains`:** gives `duplicate_edge`, not `already_has_parent`. Test: `test_identical_live_contains_is_duplicate_not_already_has_parent`.
3. **Move:** `effective_tags` of the child and its descendants follow the new parent, and `qualified_name` is unchanged. Test: `test_move_contains_onto_parentless_child`.
4. **Recreated names:** resolve to the new id, never the dead one. Test: `test_edge_to_recreated_node_uses_new_id`.
5. **A rejected op writes nothing:** no edge row and no op-log row, with exact `str(error)`. Test: `test_rejected_op_writes_nothing`.

---

### Test fixture assumed (foundation `tests/apply/conftest.py`)

`BASE` seed:
- **Nodes:** `conn` (connection, tags `[]`), `conn/db` (database), `conn/db/sc` (schema, tags `["a"]`), `conn/db/sc/t` and `conn/db/sc/u` (tables), `conn/db/sc/t/k` (column).
- **Edges**, ids 1–6 in seed order:
  - contains `conn→db`=1, `db→sc`=2, `sc→t`=3, `sc→u`=4, `t→k`=5;
  - references `t→u`=6.

Some tests need more. A local fixture `wide_store` in the test file loads `BASE` plus:
- a schema `conn/db/sc2` (tags `["b"]`, contains `db→sc2`=7);
- a table `conn/db/sc2/v` (contains `sc2→v`=8).

If the foundation's `BASE` names differ, adapt the names and keep the shapes.

**Local helpers** in `tests/apply/test_add_edge.py`. Each runs inside `transaction(store.con)` and uses repositories only:
- `kill_edge(store, f, t, type, op_id="op-9000")`: `edges.mark_deleted([edges.live(id(f), id(t), type).id], op_id)`.
- `kill_subtree(store, qn, op_id="op-9001")`: marks `live_subtree_ids` and `live_touching` dead.
- `recreate(store, qn, type, parent, op_id)`:
  - `nodes.insert(Node(mint_node_id(op_id), qn, type, "", "", (), None))`;
  - then `edges.insert(parent_id, new_id, "contains")`.
- `edge_row(store, id)`: reads `SELECT * FROM edges WHERE id=?`. Tests may use SQL; the layer rule covers `src/` only.
- `etags(store, qn)`: reads `effective_tags` for the live node of that name.
- `add(store, f, t, type="references", kind="human", op_id="op-0100")`: calls `run(store, op="add_edge", actor={"kind":kind,"name":"ann" or "ingest-crawler"}, **{"from":f,"to":t,"type":type})`.

### Task 1: `add_edge` handler

**Files:**
- Create: `src/graph_undo/api/add_edge.py`
- Modify: `src/graph_undo/api/registry.py`, `README.md`
- Test: `tests/apply/test_add_edge.py`

**Interface:** `def handle(ctx: OpContext, op: AddEdge) -> OpResult`

- [ ] **Step 1: Write the failing tests** in `tests/apply/test_add_edge.py`.

  **Happy paths**
  - `test_add_reference_happy_path`: `add(store, "conn/db/sc/u", "conn/db/sc/t")` returns `OpResult(op_id="op-0100", effect="applied", edge_id=7)`.
    - `edge_row(7)` is `(7, id(u), id(t), "references", None)`.
    - `log_row(store,"op-0100")` has `actor_kind="human"`, `actor_name="ann"`, `op="add_edge"`, `effect="applied"`, `node_id=None`, `edge_id=7`, `before=None`, and `payload ==` the canonical op JSON.
    - The `removals` table has no rows for the op.
  - `test_ingest_add_reference`: the same with `kind="ingest"`. It is applied, with `actor_kind="ingest"` and `actor_name="ingest-crawler"` in the log.
  - `test_reverse_direction_allowed`: the seed has `t→u` (6). `add(u→t)` is applied as id 7. Both 6 and 7 are live.
  - `test_reference_between_any_types`: `add("conn/db/sc/t/k", "conn/db")` (column → database) is applied.
  - `test_reference_alongside_contains_same_pair`: `add("conn/db/sc","conn/db/sc/t","references")` is applied. Contains edge 3 stays live.
  - `test_ingest_add_contains_onto_parentless_child`: `kill_edge(sc→u, contains)`, then an ingest `add(sc2→u, "contains")` on `wide_store`. It is applied, and `live_parent(id(u)).from_id == id(sc2)`.

  **Move** (`wide_store`)
  - `test_move_contains_onto_parentless_child`: `kill_edge(sc→t, contains)`, then `add(sc2→t, "contains")`. It is applied, with a new id 9.
    - `live_parent(id(t)).from_id == id(sc2)`.
    - `live_by_name("conn/db/sc/t")` still exists, with the same id and an unchanged `qualified_name`.
    - `etags(t) == ["b"]` and `etags(k) == ["b"]`, where both were `["a"]` before.
    - `etags(u) == ["a"]` is unchanged.
    - Old edge 3 still has `deleted_by="op-9000"`.

  **Dead edges and recreated nodes**
  - `test_readd_identical_to_dead_reference_inserts_new_row`:
    - Capture `before = edge_row(6)`. Run `kill_edge(t→u, references)`, then `add(t→u)`.
    - It is applied with `edge_id=7`, and `edge_row(6) == before` with `deleted_by="op-9000"` (never revived).
    - `edge_row(7).deleted_by is None`.
    - Exactly one live `t→u references` exists (`edges.live(...).id == 7`).
  - `test_readd_identical_to_dead_contains_inserts_new_row`: `kill_edge(sc→t, contains)`, then `add(sc→t, contains)`. The result is new id 7, row 3 is still dead, and `live_parent(t).id == 7`.
  - `test_edge_to_recreated_node_uses_new_id`:
    - Capture `old = id(t)`, then run `kill_subtree("conn/db/sc/t")`.
    - Then `recreate("conn/db/sc/t","table","conn/db/sc","op-0050")` gives `new = mint_node_id("op-0050")`.
    - Then `add(u→t)` is applied, with `edge_row(...).to_id == new != old`.
    - No live edge touches `old`.
  - `test_edge_from_recreated_node_uses_new_id`: the same, with the recreated node as `from`.

  **Rejections.** Each one asserts `pytest.raises(<Class>)`, the exact `str(e)`, `e.code`, and that the db is unchanged (see the last test).
  - `test_duplicate_live_reference`: `add(t→u)` gives `DuplicateEdge`, with `"op-0100 duplicate_edge: references edge 'conn/db/sc/t' -> 'conn/db/sc/u' already exists (edge 6)"`.
  - `test_ingest_duplicate_reference_is_error`: the same with `kind="ingest"`. It is `DuplicateEdge`, not a noop, and no log row is written.
  - `test_self_loop`: `add(t→t)` gives `SelfLoop`, with `"op-0100 self_loop: references edge from 'conn/db/sc/t' to itself"`. A `contains` self loop also gives `SelfLoop`, not `type_order`.
  - `test_from_never_existed`: `from="conn/nope"` gives `NodeNotFound`, with `"op-0100 node_not_found: from 'conn/nope' is not a live node"`.
  - `test_to_never_existed`: `to="conn/nope"` gives `NodeNotFound`, with `"... to 'conn/nope' is not a live node"`.
  - `test_endpoint_dead`: after `kill_subtree("conn/db/sc/u")`, `add(t→u)` gives `NodeNotFound`, naming `to 'conn/db/sc/u'`.
  - `test_both_missing_reports_from_first`: `from="x/1"` and `to="x/2"`. The message names `from 'x/1'` only.
  - `test_self_loop_on_dead_name_is_node_not_found`: `kill_subtree(u)`, then `add(u→u)` gives `NodeNotFound`.
  - `test_contains_type_order`, parametrised. Each raises `TypeOrderViolation` with code `type_order`, and the first case also checks the exact message:
    - same level `t→u` (table→table): `"op-0100 type_order: contains 'conn/db/sc/t' (table) -> 'conn/db/sc/u' (table): a table can only contain a column"`;
    - two levels down `db→t`, after `kill_edge(sc→t)` so that the parent check cannot fire;
    - upward `t→sc`;
    - `k→u`, from a column: `"…: a column cannot contain anything"`.
  - `test_contains_onto_child_with_live_parent` (`wide_store`): `add(sc2→t, contains)` gives `AlreadyHasParent`, with `"op-0100 already_has_parent: 'conn/db/sc/t' already has parent 'conn/db/sc' (edge 3); delete that contains edge first"`.
  - `test_identical_live_contains_is_duplicate_not_already_has_parent`: `add(sc→t, contains)` gives `DuplicateEdge` (edge 3).
  - `test_rejected_op_writes_nothing`, parametrised over every rejection above:
    - Snapshot `SELECT * FROM edges ORDER BY id`, `SELECT * FROM ops` and `SELECT * FROM nodes` before and after. All are equal, and `log_row(op_id) is None`.
    - `str(e)` equals the message that `apply_op` raises for the same op on a fresh store, so the text does not depend on state history.
    - A later valid `add` with the same `op_id` succeeds, which shows the rejection left no log row.

  **Re-run**
  - `test_rerun_same_op_is_skipped`: `add(u→t)` twice with the same op dict. The second call returns `effect="skipped"`, and there is still one live `u→t` edge and one log row.

- [ ] **Step 2: Run** `uv run pytest tests/apply/test_add_edge.py -q`. Expected: FAIL (`KeyError: 'add_edge'` from `HANDLERS`, or `ModuleNotFoundError: graph_undo.api.add_edge`).

- [ ] **Step 3: Implement** `src/graph_undo/api/add_edge.py`, following the spec's "Validation order" and "Writes":
  ```python
  def handle(ctx: OpContext, op: AddEdge) -> OpResult: ...
  def _resolve(ctx: OpContext, field: str, qn: str) -> Node: ...      # raises NodeNotFound
  def _check_type_order(op_id: str, parent: Node, child: Node) -> None: ...  # raises TypeOrderViolation
  ```
  - Use no SQL.
  - Never call `edges.insert` before every check has passed.

- [ ] **Step 4: Run** `uv run pytest tests/apply/test_add_edge.py -q`. Expected: PASS.

- [ ] **Step 5: Register** the handler with `HANDLERS["add_edge"] = add_edge.handle` in `api/registry.py`. Then run `uv run pytest -q`. Expected: all pass, including `tests/test_layers.py`, and the foundation integration test gets further, or passes once every handler is registered.

- [ ] **Step 6: Update the README.** In the status table, `add_edge` becomes done. In the apply section, list the `add_edge` rules and error codes.

- [ ] **Step 7: Commit** `feat(apply): add_edge handler` on `main`.

### Foundation parse tests (NOT part of this plan; listed for the foundation's `tests/apply/test_parse.py`)

These are shape checks done by `parse_op`. They are listed here so nothing is lost. If they're missing from the foundation plan, add them there, not in `test_add_edge.py`.
- `test_parse_add_edge_human_ok` / `test_parse_add_edge_ingest_ok`: the result is `AddEdge(from_=…, to=…, type=…)`, and ingest does **not** raise `ActorNotAllowed`.
- `test_parse_add_edge_unknown_type`: `type="likes"` gives `MalformedOp`.
- `test_parse_add_edge_missing_key`, parametrised over a missing `from`, `to` or `type`: `MalformedOp`.
- `test_parse_add_edge_extra_key`: adding `"tags": []` gives `MalformedOp`.
- `test_parse_add_edge_non_string_endpoint`: `from=7` gives `MalformedOp`.
