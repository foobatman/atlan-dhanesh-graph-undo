# graph-undo

Undo for subtree deletes on a small SQLite-backed graph. The brief is in [problem.md](problem.md).

## Status

| Part | State |
|---|---|
| Seed fixture generator (`seed.jsonl`) | done |
| Ops fixture generator (`ops.jsonl`) | spec in progress |
| `load` / `apply` / `undo` / `dump` | not started |
| Checker | not started |

## Setup

Requires [uv](https://docs.astral.sh/uv/) and Python ≥ 3.11. Every command runs through `uv run`, so no virtualenv activation is needed.

```sh
uv sync
```

## Generate the fixture

```sh
uv run gen-fixture --seed 42              # writes fixtures/seed.jsonl
uv run gen-fixture --seed 42 --out DIR    # writes DIR/seed.jsonl
```

The same seed always produces a byte-identical file. A summary line goes to stderr:

```
wrote fixtures/seed.jsonl: nodes=2503 edges=2801 big_schema=postgres/ops/core big_size=614
```

`seed.jsonl` holds one JSON object per line: all nodes, then all edges. A line with `from_id` is an edge.

- **Nodes:** `id`, `qualified_name`, `type` (`connection`/`database`/`schema`/`table`/`column`), `description`, `owner`, `tags`.
- **Edges:** `from_id`, `to_id`, `type` (`contains` or `references`).
- **Shape:**
  - `contains` is a strict tree, 2 connections → 4 databases → 12 schemas → tables → columns.
  - One schema is the delete target, with ≥ 550 nodes.
  - About 300 table→table `references` edges, at least 80 of them crossing that schema's boundary.

## Run the tests

```sh
uv run pytest
```

## Layout

```
src/graph_undo/fixture.py   seed generator (gen-fixture)
tests/                      pytest suite
docs/superpowers/specs/     design specs
docs/superpowers/plans/     implementation plans
```
