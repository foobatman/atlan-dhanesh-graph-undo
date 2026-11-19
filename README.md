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

Schema (`src/graph_undo/schema.sql`), flag-in-place: deleted rows stay with `deleted_by = <op_id>`; `NULL` means live.

| object | what |
|---|---|
| `nodes` | `id`, `qualified_name`, `type`, `description`, `owner`, `tags` (sorted JSON array), `deleted_by` |
| `edges` | `id` (row id), `from_id`, `to_id`, `type`, `deleted_by` |
| unique indexes | among live rows only: `qualified_name`; `(from_id, to_id, type)`; one `contains` parent per node |
| `live_nodes`, `live_edges` | views without deleted rows: read graph state through these |
| `effective_tags` | view: `(node_id, tags)`, own tags ∪ every ancestor's along live `contains` |

Full design: `docs/superpowers/specs/2026-11-19-load-design.md`.

## Run the tests

```sh
uv run pytest
```

## Layout

```
src/graph_undo/fixture/seed.py  seed generator
src/graph_undo/fixture/ops.py   ops generator (name-keyed simulator validates every op)
src/graph_undo/fixture/cli.py   gen-fixture CLI
src/graph_undo/schema.sql       SQLite schema (tables, live views, effective_tags)
src/graph_undo/store.py         connect + load_seed
src/graph_undo/cli.py           load CLI
tests/replay.py                independent replay of seed + ops, used by the tests
tests/                         pytest suite
docs/superpowers/specs/     design specs
docs/superpowers/plans/     implementation plans
```
