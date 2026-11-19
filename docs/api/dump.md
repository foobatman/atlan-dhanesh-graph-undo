# dump

Prints the **live** graph as JSONL, using the same compact, sorted-key format as `seed.jsonl`. Two equal graphs always dump byte-identically, so dumps can be compared with `cmp` or `diff`. Dump is read-only: it opens the database with `mode=ro`, reads it in one transaction and never writes.

## CLI

```sh
uv run dump                          # reads ./graph.db
uv run dump --db PATH > graph.dump.jsonl
```

The exit code is 0 when the graph is printed. It is 1, with a message on stderr and nothing on stdout, if the database is missing, isn't SQLite, has pending migrations or is newer than the code.

## Python

```python
# doctest-run
import json
from graph_undo.api.dump import DumpError, dump_lines

lines = dump_lines(tmp_path / "docs.db")              # raises DumpError instead of printing
objs = [json.loads(line) for line in lines]
assert objs[0]["qualified_name"] == "conn" and objs[0]["effective_tags"] == []
assert sum(1 for o in objs if "from_id" in o) == 6    # BASE has 6 live edges
```

## Output

| lines | order | keys |
|---|---|---|
| nodes, first | by `qualified_name` | `id`, `qualified_name`, `type`, `description`, `owner`, `tags`, `effective_tags` |
| edges, after | by `(from_id, to_id, type)` | `from_id`, `to_id`, `type` |

Deleted rows, edge row ids, the op log and the undo log never appear in a dump.

## Errors

These are `DumpError`, not `OpError`. `str(e)` is printed as it is:

| when | message |
|---|---|
| no file | `no database at {db}; run: uv run load <seed.jsonl> --db {db}` |
| pending migrations | `database needs migrations [N…]; run: uv run migrate --db {db}` |
| not SQLite / unreadable | `{db}: cannot open database: …` / `{db}: cannot read database: …` |

Design: `docs/superpowers/specs/2026-11-19-dump-design.md`.
