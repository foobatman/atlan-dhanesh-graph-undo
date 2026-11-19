# load

Creates a fresh database from a seed file. The database is built in `<db>.tmp` and only swapped in once the build succeeds, so a failed load leaves any previous database untouched. The new database is migrated to the latest schema.

## CLI

```sh
uv run load fixtures/seed.jsonl              # (re)creates ./graph.db
uv run load fixtures/seed.jsonl --db PATH
```

On success, stderr shows `loaded graph.db: nodes=2503 edges=2801` and the exit code is 0. A bad seed prints `<seed>:<line>: <message>` and exits 1.

## Python

```python
# doctest-run
from graph_undo.api.load import LoadError, load_seed

nodes, edges = load_seed(seed_path, tmp_path / "new.db")      # raises LoadError on a bad seed
assert (nodes, edges) == (6, 6)
```

## Input

`seed.jsonl` holds one JSON object per line: every node first, then every edge.
- **Node:** `{"id", "qualified_name", "type", "description", "owner", "tags"}`. `type` is one of `connection`, `database`, `schema`, `table` or `column`.
- **Edge:** `{"from_id", "to_id", "type"}`, with `type` set to `contains` or `references`.

## Records

The seed fills `nodes` and `edges`, and every row is live (`deleted_by` is NULL). The op log and undo log start empty.
