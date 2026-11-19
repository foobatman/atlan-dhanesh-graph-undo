# graph-undo

Undo for subtree deletes on a small SQLite-backed graph. The brief is in [problem.md](problem.md).

## Status

| Part | State |
|---|---|
| Seed fixture generator (`seed.jsonl`) | done |
| Ops fixture generator (`ops.jsonl`) | done |
| `load` (seed → SQLite) | done |
| `migrate` (schema migrations) | done |
| `apply` foundation (parse, errors, op log, registry, CLI) | done |
| `apply` op handlers (`add_node`, `edit_node`, `delete_node`, `add_edge`, `delete_edge`) | done |
| `dump` (live graph as sorted JSONL) | done |
| `undo` | in progress (separate session) |
| Checker (`uv run check`) | done: invariants 1–6 and 8 (7 skipped); its e2e tests run once `undo` is on this branch |

## Setup

Requires [uv](https://docs.astral.sh/uv/) and Python ≥ 3.11. Every command runs through `uv run`, so no virtualenv activation is needed.

```sh
uv sync
```

## Generate the fixture

```sh
uv run gen-fixture                        # seed 42 (default): writes fixtures/seed.jsonl and fixtures/ops.jsonl
uv run gen-fixture --seed 7 --out DIR     # writes DIR/seed.jsonl and DIR/ops.jsonl
```

The same seed always produces a byte-identical file. A summary line goes to stderr:

```
wrote fixtures/seed.jsonl: nodes=2503 edges=2801 big_schema=postgres/ops/core big_size=614 ops=309 recreated=77
```

`seed.jsonl` holds one JSON object per line: all nodes, then all edges. A line with `from_id` is an edge.

- **Nodes:** `id`, `qualified_name`, `type` (`connection`/`database`/`schema`/`table`/`column`), `description`, `owner`, `tags`.
- **Edges:** `from_id`, `to_id`, `type` (`contains` or `references`).
- **Shape:**
  - `contains` is a strict tree, 2 connections → 4 databases → 12 schemas → tables → columns.
  - One schema is the delete target, with ≥ 550 nodes.
  - About 300 table→table `references` edges, at least 80 of them crossing that schema's boundary.

### `ops.jsonl`

One operation per line, applied in order. Every line has `op_id` (`op-0001`, …), `actor` (`{"kind": "human"|"ingest", "name": …}`) and `op`. **Nodes are referred to by `qualified_name`**: `add_node` mints a new id when applied, so the file never contains ids.

| op | actor | fields | effect |
|---|---|---|---|
| `add_node` | human | `qualified_name`, `type`, `parent`, `description`, `owner`, `tags` | new id, plus `contains` from `parent`. Name must not be live. |
| `add_node` | ingest | `qualified_name`, `type`, `parent` | upsert by name: no-op if live, else new id with blank description/owner/tags |
| `edit_node` | human | `node`, `set` | replaces the given description/owner/tags |
| `delete_node` | human | `node` | removes the node, its `contains` subtree, and every edge touching them |
| `add_edge` | human / ingest | `from`, `to`, `type` | adds a `contains` (child must be parentless) or `references` edge |
| `delete_edge` | human | `from`, `to`, `type` | removes the edge. Re-point = `delete_edge` + `add_edge`. Move = the same with `contains`. |

What seed 42's file contains, in order:
1. op-0001 deletes the big schema (614 nodes).
2. Human edits to its neighbours, including a new tag on the parent database.
3. 10 re-pointed references and 2 table moves.
4. An ingest run recreating 77 deleted names under new ids.
5. 9 human edits to recreated nodes.
6. Edge cases:
   - a reference to a recreated table;
   - a human claiming a deleted name, then an ingest no-op on it;
   - deleting a boundary table;
   - deleting a recreated table.
7. Removing a tag from the parent database.

Filler edits and edge changes are mixed in between. Full catalogue: `docs/superpowers/specs/2026-11-19-ops-fixture-design.md`.

## Load the seed

```sh
uv run load fixtures/seed.jsonl              # (re)creates ./graph.db
uv run load fixtures/seed.jsonl --db PATH    # or any other file
```

On success it prints `loaded graph.db: nodes=2503 edges=2801` to stderr and exits 0. A bad seed prints `<seed>:<line>: <message>` and exits 1; the database is built in `<db>.tmp` and swapped in only on success, so a failed load leaves any previous database untouched.

Schema (migrations `src/graph_undo/db/migrations/0001_graph.sql` and `0002_oplog.sql`), flag-in-place: deleted rows stay with `deleted_by = <op_id>`; `NULL` means live.

| object | what |
|---|---|
| `nodes` | `id`, `qualified_name`, `type`, `description`, `owner`, `tags` (sorted JSON array), `deleted_by` |
| `edges` | `id` (row id), `from_id`, `to_id`, `type`, `deleted_by` |
| unique indexes | among live rows only: `qualified_name`; `(from_id, to_id, type)`; one `contains` parent per node |
| `live_nodes`, `live_edges` | views without deleted rows: read graph state through these |
| `effective_tags` | view: `(node_id, tags)`, own tags ∪ every ancestor's along live `contains` |
| `ops` (0002) | op log, one row per applied op: `seq` (apply order), `op_id`, `actor_kind`, `actor_name`, `op`, `effect` (`applied`/`noop`), `node_id`, `edge_id`, `before` (edit_node's previous values, JSON), `payload` (the op as received, compact sorted JSON) |
| `removed_nodes`, `removed_edges` (0002) | what each delete op removed: `(op_id, node_id)` / `(op_id, edge_id)`, foreign keys to `ops`, `nodes`, `edges` |

Full design: `docs/superpowers/specs/2026-11-19-load-design.md`.

## Schema migrations

```sh
uv run migrate                # migrates ./graph.db
uv run migrate --db PATH
```

The schema is a numbered list of SQL files in `src/graph_undo/db/migrations/` (`NNNN_<name>.sql`, gap-free, never edited once committed). `PRAGMA user_version` holds the last one applied, and only the in-house runner (`db/migrate.py`, stdlib only) sets it. `load` always builds a db at the latest version. `migrate` upgrades an older db in place, one transaction per migration, and prints `migrated graph.db: 1 -> 2` or `graph.db is up to date (2)`. It exits 1 if the db is missing or newer than the code. No command migrates implicitly.

## Apply ops

```sh
uv run apply fixtures/ops.jsonl              # applies to ./graph.db
uv run apply fixtures/ops.jsonl --db PATH
```

Each op is parsed (`parse_op`: shape only), then applied in **its own transaction** by the handler for its kind. Every applied op gets one row in `ops`; deletes also write `removed_nodes` / `removed_edges`. An invalid op is rolled back, printed to stderr and skipped, and the run continues:

```
fixtures/ops.jsonl:17: op-0017 node_not_found: node 'conn/db/x' is not a live node
applied fixtures/ops.jsonl to graph.db: applied=302 noop=7 skipped=0 rejected=0
```

- Everything after `<line>: ` is exactly `str(OpError)` (`<op_id> <code>: <message>`, or `<code>: <message>` when the line has no usable `op_id`), the same text any caller of the API gets for that op. Line numbers count `\n` only; blank lines are ignored.
- The summary line is always printed. **Exit 0 whenever the file was processed, even with rejected ops.**
- Exit 1, with no op attempted, only when apply cannot run: the ops file is missing or unreadable, the db is missing, the db has pending migrations (`database needs migrations [2]; run: uv run migrate --db graph.db`) or is newer than the code.
- **Re-runs:** an `op_id` already in `ops` with the same payload is `skipped`; with a different payload it is rejected as `op_id_conflict`. Rejected ops are never logged, so a re-run tries them again.
- New node ids are `uuid5(6f1c3a52-0d8e-4c1e-9b7a-2f4d5e6a7b8c, op_id)`, so the same seed and ops give the same db, ids included.

**Seed 42:** `uv run load fixtures/seed.jsonl && uv run apply fixtures/ops.jsonl` applies all 309 ops in about 0.2 s. The 7 no-ops are ingest `add_node`s on names that were already live. op-0001 removes 614 nodes and 774 edges. A second run reports `skipped=309` and changes nothing. The live result matches an independent replay (`tests/test_apply_e2e.py`).

What each op records:

| op | graph writes | `ops` row | removals |
|---|---|---|---|
| `add_node` | a new node (id from `op_id`) and its `contains` edge, or nothing if ingest finds the name live (`noop`) | `node_id`, `edge_id` = the new `contains` edge | none |
| `edit_node` | replaces the fields in `set`. An edit that changes nothing still counts as `applied`. | `node_id`, `before` = previous values of the fields set | none |
| `delete_node` | `deleted_by = op_id` on the node, its live subtree, and every live edge touching them | `node_id` = the root | one `removed_nodes` / `removed_edges` row per flagged row |
| `add_edge` | a new edge row (a dead identical edge is never revived) | `edge_id` | none |
| `delete_edge` | `deleted_by = op_id` on that one live edge | `edge_id` | one `removed_edges` row |

| code | raised when |
|---|---|
| `malformed` | bad JSON, not an object, wrong/missing/extra keys, wrong types, unknown `op` or `type`, bad `op_id` |
| `actor_not_allowed` | ingest sends `edit_node`, `delete_node` or `delete_edge`, or authored fields on `add_node` |
| `node_not_found` | a `node`/`parent`/`from`/`to` name is not live |
| `name_taken` | human `add_node` of a live name |
| `type_order` | a `contains` edge (or `add_node` parent) is not exactly one level down |
| `self_loop` | `from == to` |
| `duplicate_edge` | the live edge `(from, to, type)` already exists |
| `already_has_parent` | `add_edge contains` onto a child that has a live parent |
| `edge_not_found` | `delete_edge` of an edge that isn't live |
| `op_id_conflict` | the `op_id` is already logged with a different payload |

Full design: `docs/superpowers/specs/2026-11-19-apply-foundation-design.md` and the per-op specs `2026-11-19-op-*-design.md`.

## Dump

```sh
uv run dump                  # reads ./graph.db
uv run dump --db PATH > graph.dump.jsonl
```

Prints the **live** graph as JSONL on stdout, using the same compact, sorted-key serialisation as `seed.jsonl`:

- **Node lines** come first, sorted by `qualified_name`. Keys are `id`, `qualified_name`, `type`, `description`, `owner`, `tags` and `effective_tags`.
- **Edge lines** follow, sorted by `(from_id, to_id, type)`. Keys are `from_id`, `to_id` and `type`.
- Deleted rows, edge row ids and the op log are never included, so two equal graphs always dump byte-identically.

```
{"description":"…","effective_tags":["gold","pii"],"id":"…","owner":"dave","qualified_name":"snowflake/sales/public/orders/amount","tags":["pii"],"type":"column"}
{"from_id":"…","to_id":"…","type":"contains"}
```

**Read-only.** The db is opened with `mode=ro` and read in one transaction, so a dump issues no writes and always shows one consistent moment. If a writer such as `undo` crashed mid-transaction and left a hot journal, dump reopens the db normally so SQLite's crash recovery rolls back to the last commit, and prints that state. That's the dump invariant 7 asks for between a crashed and a resumed undo. It exits 1 with a message, and no output, if the db is missing, isn't SQLite, has pending migrations or is newer than the code.

## Check

```sh
uv run check                                   # fixtures/seed.jsonl + fixtures/ops.jsonl, undoes op-0001
uv run check --seed S --ops O --op op-0001     # any delete_node op in O
uv run check --workdir DIR --keep --all        # keep artifacts in DIR; print every violation
```

An independent witness for undo. It runs the tool as a black box through its CLI commands, and reads only what they print: dumps, the undo report and the fixture files. **It never opens the db, and it imports nothing from `graph_undo`** (`tests/test_layers.py` enforces both). Each command runs as `uv run <cmd> … --db <workdir>/graph.db`:

| step | command | artifact |
|---|---|---|
| 1 | `load <seed>` | |
| 2 | `apply ops_pre.jsonl` (the lines before the target op; skipped if there are none) | |
| 3 | `dump` | `D0.jsonl`: before the delete |
| 4 | `apply ops_target.jsonl` (the target op only) | |
| 5 | `dump` | `D1.jsonl`: after the delete |
| 6 | `apply <ops>` (the full file; ops already applied are skipped) | |
| 7 | `dump` | `D2.jsonl`: before undo |
| 8 | `undo <op>` | `report.jsonl` |
| 9 | `dump` | `D3.jsonl`: after undo |
| 10 | `undo <op>` again (any exit code) | `report2.jsonl`, `undo2.stderr` |
| 11 | `dump` | `D4.jsonl`: after the second undo |

- **Removed set:** D0 − D1. Before any invariant is checked, a guard confirms that it is exactly the target's `contains` subtree plus every edge touching it, and that nothing else changed.
- **1:** frame check. Every node and edge in D2 is unchanged in D3. Anything new is a removed item, or a `contains` edge into a restored node.
- **2:** by name. A removed node whose name is free in D2, and whose original parent's name is held in D3 by a node of the same type (or which had no parent), must be in D3 with its id and authored fields, under that holder, and its report line must say `restored`. Zero such nodes is a failure (`vacuous`).
- **3:** unique names in D3 and D4.
- **4:** the report lists every removed node id and edge triple exactly once, with valid outcomes and reasons, and the outcomes agree with D3.
- **5:** no dangling edges, at most one parent per node, no cycles, and `effective_tags` recomputed from D3/D4.
- **6:** D3 and D4 are byte-identical.
- **7:** skipped; not covered by this checker.
- **8:** steps 1, 2, 4, 6, 8 and 9 (load, a full apply, undo, dump) take under 60 s.

Seed 42, against the undo branch:

```
1 PASS  frame: 1949 nodes, 2104 edges unchanged; 546 nodes, 671 edges added
2 PASS  546 eligible nodes restored intact
3 PASS  2495 names unique
4 PASS  614 nodes, 774 edges, each reported once
5 PASS  2495 nodes
6 PASS  D3 == D4
7 SKIP  not covered by this checker
8 PASS  scenario 0.4 s (checker total 0.9 s)
check op-0001: 7 passed, 0 failed, 1 skipped
```

**Exit codes:**
- 0: no `FAIL`;
- 1: at least one invariant fails (up to 10 violations each are shown; `--all` shows all of them);
- 2: the run, the guard or the input failed (an unknown op, a target op that isn't a `delete_node`, a command that failed at some step), with the message on stderr.

The workdir is a temp dir, removed on success. It is kept, and its path printed, on any failure, with `--keep`, or when `--workdir` is given. Full design: `docs/superpowers/specs/2026-11-19-checker-design.md`.

## Known limitation: undo of undo is not supported

`undo <op_id>` is designed for **delete ops** (`delete_node`, `delete_edge`) recorded by `apply`. Undoing an undo, which amounts to redo, is **not supported, and adding it is not trivial**:

- **What the design already gives an undo of undo.** Every delete records exactly what it removed (`removed_nodes`, `removed_edges`). Removed rows stay in place with `deleted_by` and are never edited. So whatever an undo restores is identifiable by id, and the original rows are still there to re-flag.
- **What is missing:**
  1. **Undo does not log its own writes.** An undo of an undo would need, for each item, the outcome (restored, merged or skipped) and the exact changes: which rows it un-flagged, which fields it copied into a recreated node in a merge (with their previous values), and which edges it created. That log belongs to the undo spec and isn't designed yet.
  2. **Merges aren't simple flag flips.** Reversing a merge means restoring the recreated node's previous field values without overwriting any human edit made after the undo (invariant 1 again, one level up).
  3. **Changes made after the undo conflict again.** A human might edit, move or delete a restored node after the undo. Reversing the undo then needs its own conflict rules (independent, conflicting, caused), the same as undo needs for the original delete.
  4. **The op log only accepts apply's five ops.** `ops.op` and `ops.effect` have fixed allowed values. Logging undo entries in `ops` needs a migration that rebuilds that table; giving undo its own tables avoids that.
- **What it would take:** have undo log each item's outcome, plus the before-values of any merge, as an op of its own. Then define "undo of undo" as a re-delete of exactly the rows that undo restored, together with a reverse of the logged merges, using the same conflict rules. Redo is the extension problem.md mentions, and it is out of scope for now.

## Run the tests

```sh
uv run pytest
```

## Layout

```
src/graph_undo/fixture/seed.py  seed generator
src/graph_undo/fixture/ops.py   ops generator (name-keyed simulator validates every op)
src/graph_undo/fixture/cli.py   gen-fixture CLI
src/graph_undo/domain/          pure Python graph values (no I/O, no sqlite3)
src/graph_undo/db/              the only package with SQL: connection, migrations, Store (.nodes, .edges, .log repositories)
src/graph_undo/db/migrations/   numbered schema migrations (0001 graph tables + views, 0002 op log + removals)
src/graph_undo/domain/ops.py    typed ops, parse_op, canonical(); domain/errors.py: OpError classes
src/graph_undo/db/nodes.py, edges.py, oplog.py   NodeRepo, EdgeRepo, OpLogRepo (+ LoggedOp)
src/graph_undo/api/             graph rules: load.py (seed), registry.py (HANDLERS, apply_op), apply.py (apply_file)
src/graph_undo/api/<op>.py      one handler per op kind
src/graph_undo/api/dump.py      dump_lines(db); db/dump.py: live_graph(con), one read transaction
src/graph_undo/api/resolve.py   require_live(ctx, field, qn): shared name resolution
src/graph_undo/cli/             entry points: load, migrate, apply, dump
tests/replay.py                independent replay of seed + ops, used by the tests
tests/apply/                   apply tests; conftest.py has store, run, live_names, log_row, snapshot, requires
tests/test_layers.py           enforces cli -> api -> db -> domain and no SQL outside db/
src/undo_checker/              the checker: separate package, no graph_undo or sqlite3 imports
src/undo_checker/model.py      parse dumps and reports; removed set (D0 − D1)
src/undo_checker/invariants.py guard plus one pure check per invariant
src/undo_checker/run.py        drives load/apply/dump/undo through uv run; ops slicing, timing
src/undo_checker/cli.py        `check` entry point: output and exit codes
tests/checker/                 checker tests; e2e and mutation tests need the undo entry point
tests/                         pytest suite
docs/superpowers/specs/     design specs
docs/superpowers/plans/     implementation plans
```
