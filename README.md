# graph-undo

Undo for subtree deletes on a small SQLite-backed graph. The brief is in [problem.md](problem.md).

- **How to call each operation**, from the shell or from Python, with exact error messages: [docs/api/](docs/api/README.md).
- **The undo conflict rules** and what was rejected: [DECISIONS.md](DECISIONS.md).

## Setup

Requires [uv](https://docs.astral.sh/uv/) and Python ≥ 3.11. Every command runs through `uv run`, so no virtualenv activation is needed.

```sh
uv sync
```

## Quickstart

The four commands from [problem.md](problem.md), run on the seed-42 fixture. Each one is covered in detail further down.

```sh
uv run gen-fixture                        # writes fixtures/seed.jsonl and fixtures/ops.jsonl
uv run load fixtures/seed.jsonl           # load: create ./graph.db from the seed
uv run apply fixtures/ops.jsonl           # apply: run all 309 ops in order (op-0001 deletes postgres/ops/core)
mkdir -p out                              # output artifacts go in out/ (gitignored)
uv run dump > out/before-undo.jsonl       # dump: the live graph as sorted JSONL
uv run undo op-0001 > out/report.jsonl    # undo: reverse the big delete; JSONL report, one line per removed item
uv run dump > out/after-undo.jsonl
uv run undo op-0001                       # a second undo is refused (already_undone, exit 1); the dump is unchanged
uv run dump > out/after-second-undo.jsonl # byte-identical to out/after-undo.jsonl
uv run check                              # the invariant checker; its artifacts go in out/check/
```

Expected stderr:

```
wrote fixtures/seed.jsonl: nodes=2503 edges=2801 big_schema=postgres/ops/core big_size=614 ops=309 recreated=77
loaded graph.db: nodes=2503 edges=2801
applied fixtures/ops.jsonl to graph.db: applied=302 noop=7 skipped=0 rejected=0
undid op-0001 on graph.db: nodes restored=546 skipped=68; edges restored=584 merged=87 skipped=103
op-0001 already_undone: already undone (undo #1, after op-0309); restored 546 nodes, 584 edges, merged 87, skipped 171. Undo runs once per op.
```

`out/report.jsonl` has 1388 lines: one for each of the 614 nodes and 774 edges op-0001 removed. See [Undo a delete](#undo-a-delete) for the rules and the report format.

`load`, `apply`, `dump` and `undo` take `--db PATH` to work on a file other than `./graph.db`.

To check the invariants, run `uv run check` after `gen-fixture`. It runs load, apply, undo and dump itself, on its own database in `out/check/`, so it never touches `./graph.db`. It prints one PASS/FAIL/SKIP line per invariant; see [Check](#check).

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

Schema (migrations `src/graph_undo/db/migrations/0001_graph.sql`, `0002_oplog.sql` and `0003_undo.sql`), flag-in-place: deleted rows stay with `deleted_by = <op_id>`; `NULL` means live.

| object | what |
|---|---|
| `nodes` | `id`, `qualified_name`, `type`, `description`, `owner`, `tags` (sorted JSON array), `deleted_by` |
| `edges` | `id` (row id), `from_id`, `to_id`, `type`, `deleted_by` |
| unique indexes | among live rows only: `qualified_name`; `(from_id, to_id, type)`; one `contains` parent per node |
| `live_nodes`, `live_edges` | views without deleted rows: read graph state through these |
| `effective_tags` | view: `(node_id, tags)`, own tags ∪ every ancestor's along live `contains` |
| `ops` (0002) | op log, one row per applied op: `seq` (apply order), `op_id`, `actor_kind`, `actor_name`, `op`, `effect` (`applied`/`noop`), `node_id`, `edge_id`, `before` (edit_node's previous values, JSON), `payload` (the op as received, compact sorted JSON) |
| `removed_nodes`, `removed_edges` (0002) | what each delete op removed: `(op_id, node_id)` / `(op_id, edge_id)`, foreign keys to `ops`, `nodes`, `edges` |
| `undos` (0003) | one row per undone op (`op_id` unique: an op is undone at most once): `undo_seq`, `op_id`, `after_seq` (last applied op when it ran), `report` (the JSONL printed) |
| `undo_items` (0003) | the outcome of every removed item: `(op_id, kind node/edge, item_id)`, `outcome` (`restored`/`merged`/`skipped`), `reason_code`, `new_edge_id` (merged `contains` edges) |

Full design: `docs/superpowers/specs/2026-11-19-load-design.md`.

## Schema migrations

```sh
uv run migrate                # migrates ./graph.db
uv run migrate --db PATH
```

The schema is a numbered list of SQL files in `src/graph_undo/db/migrations/` (`NNNN_<name>.sql`, gap-free, never edited once committed). `PRAGMA user_version` holds the last one applied, and only the in-house runner (`db/migrate.py`, stdlib only) sets it. `load` always builds a db at the latest version. `migrate` upgrades an older db in place, one transaction per migration, and prints `migrated graph.db: 1 -> 3` or `graph.db is up to date (3)`. It exits 1 if the db is missing or newer than the code. No command migrates implicitly.

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
- Exit 1, with no op attempted, only when apply cannot run: the ops file is missing or unreadable, the db is missing, the db has pending migrations (`database needs migrations [3]; run: uv run migrate --db graph.db`) or is newer than the code.
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
| `op_not_found` | `undo` of an `op_id` that is not in the op log |
| `not_undoable` | `undo` of anything but an applied `delete_node` / `delete_edge` |
| `already_undone` | `undo` of an op that was already undone (an op is undone at most once) |

Full design: `docs/superpowers/specs/2026-11-19-apply-foundation-design.md` and the per-op specs `2026-11-19-op-*-design.md`.

## Undo a delete

```sh
uv run undo op-0001                   # undo against ./graph.db; JSONL report on stdout
uv run undo op-0001 --db PATH > out/report.jsonl
```

Only `delete_node` and `delete_edge` can be undone, and each op **at most once**. The whole undo is one transaction. Rule B2 decides each removed item, parents before children:

- **Node:** `restored` if its name is free, under its original parent if that is live (or restored by this undo), otherwise under the live node that now holds the parent's name, if it has the parent's type. Otherwise `skipped`, with `name_held`, `parent_type_mismatch` or `no_live_parent`. A restored node keeps its id and its fields from delete time. Live nodes are never edited.
- **Edge:** a `contains` edge into a node restored under a different parent is `merged`: a new edge is created from that parent, and `new_edge_id` says which. Any other edge is `restored` if both of its original endpoints are live. Otherwise it is `skipped`, with `endpoint_not_restored`, `endpoint_deleted`, `duplicate_edge` or `child_has_parent`. References are never redirected.

**Report:** one JSON object per line for every node and edge the op removed, nothing else. Node lines come first (by `qualified_name`, `id`), then edge lines (by `id`). The fields are:
- `kind`, `id`, `op_id`, `outcome`;
- for nodes, `qualified_name`, plus `parent_id` when restored;
- for edges, `from_id`, `to_id`, `type`, plus `new_edge_id` when merged;
- `reason_code` and `reason` on every merged or skipped item.

```
{"id":"ed5e…","kind":"node","op_id":"op-0001","outcome":"skipped","qualified_name":"postgres/ops/core","reason":"name held by 570a… created by op-0092 (ingest)","reason_code":"name_held"}
```

**Stderr** gets a summary line. On seed 42:

```
undid op-0001 on graph.db: nodes restored=546 skipped=68; edges restored=584 merged=87 skipped=103
```

**Exit codes:** `0` on success. `1` on `malformed`, `op_not_found`, `not_undoable`, `already_undone`, a missing database or pending migrations. In every failure case the database is untouched.

Full design: `docs/superpowers/specs/2026-11-19-undo-design.md`.

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
uv run check --workdir DIR --all              # artifacts in DIR instead of out/check; print every violation
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
- **1:** frame check. Every node and edge in D2 is unchanged in D3. Anything new is a removed item, with the fields it had before the delete, or a `contains` edge into a restored node.
- **2:** by name. A removed node whose name is free in D2, and whose original parent's name is held in D3 by a node of the same type (or which had no parent), must be in D3 with its id and authored fields, under that holder, and its report line must say `restored`. Zero such nodes is a failure (`vacuous`).
- **3:** unique names in D3 and D4.
- **4:** the report lists every removed node id and edge triple exactly once, with valid outcomes and reasons, and the outcomes agree with D3. Nodes are never `merged`. A `merged` edge is a `contains` edge whose original triple is not live, and whose child has a parent. A `skipped` edge is not live, unless it already was before undo.
- **5:** no dangling edges, at most one parent per node, no cycles, and `effective_tags` recomputed from D3/D4.
- **6:** D3 and D4 are byte-identical. The second undo's first stderr line is shown for information.
- **7:** skipped; not covered by this checker.
- **8:** steps 1, 2, 4, 6, 8 and 9 (load, a full apply, undo, dump) take under 60 s.

Seed 42, against the undo branch:

```
1 PASS  frame: 1949 nodes, 2104 edges unchanged; 546 nodes, 671 edges added
2 PASS  546 eligible nodes restored intact
3 PASS  2495 names unique
4 PASS  614 nodes, 774 edges, each reported once
5 PASS  2495 nodes
6 PASS  D3 == D4 (second undo: op-0001 already_undone: already undone (undo #1, after op-0309); …)
7 SKIP  not covered by this checker
8 PASS  scenario 0.4 s (checker total 0.9 s)
check op-0001: 7 passed, 0 failed, 1 skipped
```

**Exit codes:**
- 0: no `FAIL`;
- 1: at least one invariant fails (up to 10 violations each are shown; `--all` shows all of them);
- 2: the run, the guard or the input failed (an unknown op, a target op that isn't a `delete_node`, a command that failed at some step), with the message on stderr.

The workdir is `out/check/` by default (`out/` is gitignored), or `--workdir DIR`. It is always kept, and its path is printed. Each run first deletes the files a run writes there (`graph.db`, the ops slices, D0–D4 and both reports), so a reused workdir never mixes two runs. Other files in it are left alone. Full design: `docs/superpowers/specs/2026-11-19-checker-design.md`.

## Known limitation: undo of undo is not supported

`undo <op_id>` works on **delete ops** (`delete_node`, `delete_edge`), and each one at most once. Undoing an undo, which amounts to redo, is **not supported**. Here is how far the design gets towards it:

- **Already in place:**
  - Every delete records exactly what it removed (`removed_nodes`, `removed_edges`), and removed rows are never edited.
  - Every undo records each item's outcome in `undo_items`, including the id of every `contains` edge it created (`new_edge_id`).
  - Rule B2 never edits a live node, so there are no field merges to reverse.
- **What is missing:** changes made *after* the undo conflict again. A human might edit, move or delete a restored node, or claim one of its names. Redo needs its own conflict rules (independent, conflicting, caused), the same way undo does for the original delete.
- **What it would take:** define redo as re-flagging exactly the rows that undo restored, plus removing the edges it created, under those rules. Problem.md lists redo as the optional extension, and it is out of scope for now.

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
src/graph_undo/db/migrations/   numbered schema migrations (0001 graph tables + views, 0002 op log + removals, 0003 undo log)
src/graph_undo/domain/ops.py    typed ops, parse_op, canonical(); domain/errors.py: OpError classes
src/graph_undo/db/nodes.py, edges.py, oplog.py   NodeRepo, EdgeRepo, OpLogRepo (+ LoggedOp)
src/graph_undo/db/undolog.py    UndoLogRepo (undos, undo_items)
src/graph_undo/domain/undo.py   pure undo planner: rule B2 (plan_undo) and the JSONL report (report_lines)
src/graph_undo/api/undo.py      undo_op(store, op_id) / undo_db(path, op_id): checks, reads, writes, undo log
src/graph_undo/api/             graph rules: load.py (seed), registry.py (HANDLERS, apply_op), apply.py (apply_file)
src/graph_undo/api/<op>.py      one handler per op kind
src/graph_undo/api/dump.py      dump_lines(db); db/dump.py: live_graph(con), one read transaction
src/graph_undo/api/resolve.py   require_live(ctx, field, qn): shared name resolution
src/graph_undo/cli/             entry points: load, migrate, apply, dump, undo
tests/replay.py                independent replay of seed + ops, used by the tests
tests/apply/                   apply tests; conftest.py has store, run, live_names, log_row, snapshot, requires
tests/undo/                    undo tests: planner (pure), API, CLI, seed-42 invariants 1-6
tests/test_layers.py           enforces cli -> api -> db -> domain and no SQL outside db/
src/undo_checker/              the checker: separate package, no graph_undo or sqlite3 imports
src/undo_checker/model.py      parse dumps and reports; removed set (D0 − D1)
src/undo_checker/invariants.py guard plus one pure check per invariant
src/undo_checker/run.py        drives load/apply/dump/undo through uv run; ops slicing, timing
src/undo_checker/cli.py        `check` entry point: output and exit codes
tests/checker/                 checker tests; e2e and mutation tests need the undo entry point
tests/                         pytest suite
docs/api/                      API reference: one page per operation (CLI + Python), error codes
DECISIONS.md                   undo conflict rules (independent / conflicting / caused), rejected proposals
tests/test_docs.py             keeps docs/api in sync: every error code listed, every Python example runs
docs/superpowers/specs/        design specs
docs/superpowers/plans/        implementation plans
```

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
| `undo` (rule B2, once per op, JSONL report) | done |
| Checker (`uv run check`) | done: invariants 1–6 and 8; 7 skipped (not covered by this checker) |
