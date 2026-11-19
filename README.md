# graph-undo

Undo for subtree deletes on a small SQLite-backed graph. The brief is in [problem.md](problem.md).

## Status

| Part | State |
|---|---|
| Seed fixture generator (`seed.jsonl`) | done |
| Ops fixture generator (`ops.jsonl`) | done |
| `load` (seed → SQLite) | done |
| `apply` / `undo` / `dump` | not started |
| Checker | not started |

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

Schema (migration `src/graph_undo/db/migrations/0001_graph.sql`), flag-in-place: deleted rows stay with `deleted_by = <op_id>`; `NULL` means live.

| object | what |
|---|---|
| `nodes` | `id`, `qualified_name`, `type`, `description`, `owner`, `tags` (sorted JSON array), `deleted_by` |
| `edges` | `id` (row id), `from_id`, `to_id`, `type`, `deleted_by` |
| unique indexes | among live rows only: `qualified_name`; `(from_id, to_id, type)`; one `contains` parent per node |
| `live_nodes`, `live_edges` | views without deleted rows: read graph state through these |
| `effective_tags` | view: `(node_id, tags)`, own tags ∪ every ancestor's along live `contains` |

Full design: `docs/superpowers/specs/2026-11-19-load-design.md`.

## Schema migrations

```sh
uv run migrate                # migrates ./graph.db
uv run migrate --db PATH
```

The schema is a numbered list of SQL files in `src/graph_undo/db/migrations/` (`NNNN_<name>.sql`, gap-free, never edited once committed). `PRAGMA user_version` holds the last one applied, and only the in-house runner (`db/migrate.py`, stdlib only) sets it. `load` always builds a db at the latest version. `migrate` upgrades an older db in place, one transaction per migration, and prints `migrated graph.db: 1 -> 2` or `graph.db is up to date (2)`. It exits 1 if the db is missing or newer than the code. No command migrates implicitly.

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
src/graph_undo/db/              the only package with SQL: connection, migrations, Store
src/graph_undo/db/migrations/   numbered schema migrations (0001_graph.sql: tables, live views, effective_tags)
src/graph_undo/api/             graph rules; load.py = seed parsing/validation + atomic db swap
src/graph_undo/cli/             entry points: load, migrate
tests/replay.py                independent replay of seed + ops, used by the tests
tests/                         pytest suite
docs/superpowers/specs/     design specs
docs/superpowers/plans/     implementation plans
```
