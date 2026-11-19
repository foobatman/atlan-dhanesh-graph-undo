# graph-undo

Undo for subtree deletes on a small SQLite-backed graph. The brief is in [problem.md](problem.md).

## Status

| Part | State |
|---|---|
| Seed fixture generator (`seed.jsonl`) | done |
| Ops fixture generator (`ops.jsonl`) | done |
| `load` / `apply` / `undo` / `dump` | not started |
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
wrote fixtures/seed.jsonl: nodes=2503 edges=2801 big_schema=postgres/ops/core big_size=614 ops=307 recreated=71
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
4. An ingest run recreating 71 deleted names under new ids.
5. 9 human edits to recreated nodes.
6. Edge cases:
   - a reference to a recreated table;
   - a human claiming a deleted name, then an ingest no-op on it;
   - deleting a boundary table;
   - deleting a recreated table.
7. Removing a tag from the parent database.

Filler edits and edge changes are mixed in between. Full catalogue: `docs/superpowers/specs/2026-11-19-ops-fixture-design.md`.

## Run the tests

```sh
uv run pytest
```

## Layout

```
src/graph_undo/fixture.py      seed generator + gen-fixture CLI
src/graph_undo/ops_fixture.py  ops generator (name-keyed simulator validates every op)
tests/replay.py                independent replay of seed + ops, used by the tests
tests/                         pytest suite
docs/superpowers/specs/     design specs
docs/superpowers/plans/     implementation plans
```
