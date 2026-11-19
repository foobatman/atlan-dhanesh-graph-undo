# Apply Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The layered package (`domain` / `db` / `api` / `cli`), schema v2 (`ops`, `removals`), typed op parsing and errors, the handler registry, and `uv run apply`. Per-op handlers then plug in.

**Architecture:**
- The dependency direction is `cli → api → db → domain`, and `api` also imports `domain`.
- SQL lives only in `db/`.
- Each op is one transaction, owned by `api.registry.apply_op`.
- An invalid op is printed, rolled back and skipped.

**Tech Stack:** Python ≥ 3.11 stdlib, uv, pytest.

**Spec:** `docs/superpowers/specs/2026-11-19-apply-foundation-design.md`. The per-op specs are `2026-11-19-op-*-design.md`.

## Global Constraints

- **Runtime:** stdlib only.
- **Commands:** every command runs as `uv run …`.
- **Git:** commit directly on `main`.
- **README:** updated in the task that changes the CLI or layout.
- **Schema version:** `user_version = 2`. `apply` refuses any other version with exit 1 and "run load first".
- **Ids:** `mint_node_id(op_id) = str(uuid.uuid5(UUID("6f1c3a52-0d8e-4c1e-9b7a-2f4d5e6a7b8c"), op_id))`.
- **Error text:** `str(OpError) == f"{op_id} {code}: {message}"`. `apply` prints it as `<ops path>:<line>: <str(e)>`.
- **Summary line:** `applied <ops> to <db>: applied=N noop=N skipped=N rejected=N`. The exit code is 0 whenever the file was processed, rejected ops included. It is 1 only when apply can't run at all (missing or unreadable ops file, missing db, wrong `user_version`).
- **Moved load code:** existing load behaviour and tests are unchanged except for import paths.

## Review Focus

1. **Rollback:** a handler raising partway through writes nothing; no row and no log entry survive. Test: `test_apply_op_rolls_back_on_error`.
2. **Re-running a file:** every op is skipped and the database is byte-for-byte identical in content. Test: `test_rerun_skips_everything`.
3. **Same `op_id`, different payload:** raises `OpIdConflict`, and the op is rejected and printed. Test: `test_op_id_conflict`.
4. **Bad lines:** a malformed JSON line or a non-object line in the middle of the file is rejected with the right line number, and the next lines still apply. Test: `test_bad_line_mid_file`.
5. **Layer leaks:** `sqlite3` imported, or SQL written, outside `db/`. Test: `test_layers`.

---

### Task 1: Package move and layer test

**Files:**
- Create the packages `domain/`, `db/`, `api/` and `cli/`, each with an `__init__.py`.
- Move `schema.sql` to `db/schema.sql`.
- Split `store.py` into `db/connection.py`, `db/store.py` (bulk load) and `api/load.py`.
- Move `cli.py` to `cli/load.py`.
- Modify `pyproject.toml` (`load = "graph_undo.cli.load:main"`), `tests/test_load*.py` (imports only) and `README.md` (layout).
- Test: `tests/test_layers.py`.

**Interfaces produced:**
- `db.connection.connect(path)`.
- `db.connection.init_schema(con)`.
- `db.connection.transaction(con)`, a context manager that runs `BEGIN IMMEDIATE`, then `COMMIT`, or `ROLLBACK` on an exception. Set `isolation_level=None` in `connect` so that transactions are explicit.
- `api.load.load_seed(seed_path, db_path) -> (int, int)` and `api.load.LoadError`.
- `cli.load.main(argv=None) -> int`.

- [ ] **Step 1: Write `test_layers`.**
  - Parse every module under `src/graph_undo` with `ast`.
  - Assert the import rules:
    - `domain` imports nothing from the project;
    - `db` imports only `domain`;
    - `api` imports only `domain` and `db`;
    - `cli` imports only `api` and `domain`.
  - `fixture` is exempt and must not be imported by the other four packages.
  - Assert that `import sqlite3` appears only in `db/`.
  - Assert that no string literal outside `db/` matches `\b(SELECT|INSERT|UPDATE|DELETE FROM|CREATE)\b`.
- [ ] **Step 2: Run** `uv run pytest tests/test_layers.py -q`. Expected: FAIL, because the packages are missing.
- [ ] **Step 3: Do the move.** Update the test imports (`from graph_undo.api.load import LoadError, load_seed`, `from graph_undo.db.connection import connect`, `from graph_undo.cli.load import main as load_main`). Run `uv sync`.
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: all pass, and `uv run load fixtures/seed.jsonl` still prints `nodes=2503 edges=2801`.
- [ ] **Step 5: Commit** `refactor: layered package (domain/db/api/cli); load moved, behaviour unchanged`.

### Task 2: Domain types, errors and `parse_op`

**Files:** Create `domain/model.py`, `domain/errors.py` and `domain/ops.py`. Test: `tests/apply/test_parse.py`.

**Interfaces produced** are exactly as in the spec's "Domain types" and "Errors" sections:
- `Actor`, `Node`, `Edge`, `normalise_tags(list) -> tuple`;
- `AddNode`, `EditNode`, `DeleteNode`, `AddEdge`, `DeleteEdge`, each with `op_id`, `actor`, `raw` and a `kind` property;
- `parse_op(d: dict) -> Op`;
- `canonical(d: dict) -> str`, which is compact, sorted-key JSON;
- `OpError` and its ten subclasses with their `code` values.

- [ ] **Step 1: Write the tests.**
  - `test_parse_each_kind`: one valid dict per (op, actor kind) parses to the right class with the right fields. Tags come back normalised.
  - `test_error_str`: `str(NodeNotFound.for_field("op-0007", "node", "a/b")) == "op-0007 node_not_found: node 'a/b' is not a live node"`, and every subclass has a unique `code`.
  - `test_parse_rejects`, parametrised. Each case raises `MalformedOp` (or `ActorNotAllowed` where marked) and the error carries `op_id` whenever the input has one. The cases:
    - the input is not a dict;
    - `op_id` is missing;
    - `op_id` is `"7"`;
    - `actor` is not a dict;
    - an unknown actor kind;
    - an unknown `op`;
    - a missing field;
    - an extra field;
    - a non-string field;
    - `tags` is not a list of strings;
    - an unknown node `type`;
    - an unknown edge `type`;
    - `edit_node` with an empty `set`;
    - `edit_node` with an unknown key in `set`;
    - `ActorNotAllowed`: ingest sending `edit_node`, `delete_node` or `delete_edge`;
    - `ActorNotAllowed`: ingest `add_node` carrying `owner`.
  - Then append the parse-level cases listed in each per-op plan, under the heading "parse tests requested by op plans".
- [ ] **Step 2: Run** → FAIL with an ImportError.
- [ ] **Step 3: Implement** the code. `domain` must not import `sqlite3`, `json` I/O or `pathlib`.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat: domain ops, errors and parse_op`.

### Task 3: Schema v2 and repositories

**Files:** Modify `db/schema.sql` (add `ops` and `removals`, set `user_version = 2`). Create `db/nodes.py`, `db/edges.py` and `db/oplog.py`, and extend `db/store.py` with `Store(con)` and `mint_node_id`. Test: `tests/apply/test_repos.py`.

**Interfaces produced** are the method lists in the spec's "Repositories" section, plus any additions the per-op specs request under "Foundation changes requested". Also `LoggedOp`, a dataclass mirroring the `ops` columns except `seq`.

- [ ] **Step 1: Write the tests** against a loaded small seed.
  - `live_by_name` returns `None` for a dead row.
  - `by_id` returns dead rows.
  - `live_subtree_ids` returns the root first, follows live `contains` only and skips dead edges.
  - `live_touching` returns both directions and both types, live only.
  - `mark_deleted` leaves already-dead rows' `deleted_by` unchanged.
  - `EdgeRepo.insert` returns an `Edge` with a new id.
  - `OpLogRepo.record` and `get` round-trip.
  - `add_removals` and `removals` round-trip.
  - Recording an `op_id` twice raises `sqlite3.IntegrityError`.
  - `mint_node_id("op-0001")` is stable and equals the uuid5 above.
  - `load` produces `user_version` 2 and an empty `ops` table.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** the code. `live_subtree_ids` is a recursive CTE over `live_edges`, so it is one query. Every repository method is set-based; no per-row loops over a subtree.
- [ ] **Step 4: Run** → PASS, and the full suite is green.
- [ ] **Step 5: Commit** `feat: schema v2 (ops, removals) and repositories`.

### Task 4: Registry, `apply_file` and `uv run apply`

**Files:** Create `api/context.py`, `api/resolve.py` (`require_live(ctx, field, qn) -> Node`), `api/registry.py`, `api/apply.py` and `cli/apply.py`. Modify `pyproject.toml` (`apply = "graph_undo.cli.apply:main"`), `.gitignore` (unchanged) and `README.md` (status table, an "Apply ops" section, errors table, layout). Tests: `tests/apply/conftest.py`, `tests/apply/test_registry.py` and `tests/test_apply_cli.py`.

**Interfaces produced:**
- `OpContext`, `OpResult`, and `HANDLERS: dict[str, Handler]`. It starts empty; each per-op plan registers its handler.
- `apply_op(store, op) -> OpResult`.
- `apply_file(ops_path, db_path, on_reject) -> ApplySummary`.
- `cli.apply.main(argv=None) -> int`.
- The test helpers in `tests/apply/conftest.py`:
  - `store`, a fixture holding a loaded small seed;
  - `run(store, **op_fields) -> OpResult`, which fills in a default `op_id` and actor;
  - `live_names(store)`;
  - `log_row(store, op_id)`;
  - `snapshot(store)`, every row of every table, used for "nothing changed" assertions.

- [ ] **Step 1: Write the tests.** Fake handlers are registered via `monkeypatch.setitem(HANDLERS, …)`.
  - `test_apply_op_dispatches`.
  - `test_apply_op_rolls_back_on_error`: the fake handler inserts a node and then raises `NodeNotFound`. Afterwards, `snapshot` is unchanged.
  - `test_op_id_conflict`.
  - `test_rerun_skips_everything`.
  - `test_unknown_kind_without_handler`: this raises `MalformedOp`. It can't happen after `parse_op`, but `apply_op` guards it anyway.
  - `test_bad_line_mid_file`: lines are good, bad JSON, good, `[1]`, good. Expect `rejected=2`, the right line numbers, and all three good ops applied.
  - CLI tests:
    - the summary line format;
    - exit 0 with rejected ops (`rejected=2`, each rejection on stderr);
    - exit 1 for a missing ops file, a missing db and `user_version` 1, with no op attempted;
    - the stderr reject line equals `f"{path}:{line}: {e}"`;
    - `uv run apply` works through its entry point.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** the code.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat: apply registry, apply_file and uv run apply`.

### Task 5: End-to-end, after all five per-op plans

**Test:** `tests/test_apply_e2e.py`.

- [ ] **`test_seed42_scenario`:** load seed 42, then apply `ops.jsonl` 42. Expect exit 0, `rejected=0` and `applied + noop == 309`. Live state compared by name equals `tests/replay.py`'s `replay(...)`: node fields, `contains` parent names and the reference pairs. The run takes under 5 s.
- [ ] **`test_seed42_bookkeeping`:** every `removals` row points at a row whose `deleted_by` is that `op_id`. Every row with a non-null `deleted_by` has a `removals` row for that op. Every live edge has live endpoints.
- [ ] **`test_seed42_rerun`:** a second apply gives `skipped=309` and the snapshot is unchanged.
- [ ] **`test_seed42_deterministic`:** two fresh load-and-apply runs produce identical snapshots, ids included.
- [ ] **`test_bad_op_mid_file`:** the spec's integration case.
- [ ] **Commit** `test: apply end-to-end on the seed-42 scenario`. Update the README status to show apply done.
