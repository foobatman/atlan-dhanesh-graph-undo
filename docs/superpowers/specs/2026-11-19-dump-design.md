# `dump`: design

**Status:** approved
**Brief:** `problem.md`, "dump. Print the live graph as sorted JSONL, for comparison."
**Builds on:**
- `2026-11-19-load-design.md`: the live views and `effective_tags`.
- `2026-11-19-apply-foundation-design.md`: the layers, migrations and `Store`.

## Intent

`uv run dump [--db graph.db]` prints the live graph as sorted JSONL. The checker compares dumps (invariants 6 and 7) and reads `effective_tags` from them (invariant 5). Dump is independent of undo: it reads only the live views, so whatever undo writes, dump reports the result.

## Goals, non-goals, anti-goals

**Goals:**
- The same database always gives byte-identical output.
- Dump never writes to the database.
- Every dump shows one consistent moment.

**Non-goals:**
- Dumping deleted rows, the op log, removals or edge row ids.
- Any filtering options.

**Anti-goals:**
- A dump that differs between two equal graphs because of storage details, such as edge row ids or `deleted_by`.
- A dump that changes the database file.

## Approaches

There was one sensible way. The live views already are the live graph, and "sorted JSONL" fixes the format. Adding storage columns was rejected because they would make equal graphs dump differently.

## Format

The lines use the same serialisation as `seed.jsonl`: `json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)`, one object per line, ending in `\n`.

- **Nodes come first,** sorted by `qualified_name`, which is unique among live nodes. Keys are `id`, `qualified_name`, `type`, `description`, `owner`, `tags` and `effective_tags`. `tags` and `effective_tags` are sorted, unique JSON arrays.
- **Edges come next,** sorted by `(from_id, to_id, type)`. Keys are `from_id`, `to_id` and `type`.
- As in the seed, a line with `from_id` is an edge.
- **Empty graph:** an empty output (no lines).

Example:

```
{"description":"…","effective_tags":["gold","pii"],"id":"…","owner":"dave","qualified_name":"snowflake/sales/public/orders/amount","tags":["pii"],"type":"column"}
{"from_id":"…","to_id":"…","type":"contains"}
```

## Behaviour

- **Read-only.** The db is opened with a URI of the form `file:<path>?mode=ro`. Dump reads all rows inside one `BEGIN` (deferred) … `COMMIT` read transaction, so it sees one consistent snapshot even while another process writes.
- **After a crashed writer.** If a writer died mid-transaction, it leaves a hot journal, and a `mode=ro` connection can't read at all (SQLITE_READONLY_ROLLBACK). Dump then reopens the db normally, so SQLite's own crash recovery rolls the database back to its last commit; that's the state dump prints. Dump itself never issues a write. This is required by invariant 7: a dump taken between a crashed undo and its re-run must work. Added after the final review.
- **Version check inside the snapshot.** `user_version` is read inside the same read transaction as the rows. If it changed since the migration check, dump fails with exit 1.
- **Output.** stdout gets the JSONL lines only. Nothing goes to stderr on success. Exit 0.
- **Exit 1** with a plain stderr message, and no output, when:
  - the db is missing;
  - the file isn't SQLite;
  - migrations are pending (`run: uv run migrate --db <db>`);
  - the db is newer than the code.

  These are the same checks `apply` makes.

## Code (layering as in the foundation)

| File | Role |
|---|---|
| `db/connection.py` | `connect_readonly(path) -> sqlite3.Connection` |
| `db/dump.py` | `live_graph(con) -> tuple[list[dict], list[dict]]`. It returns node dicts (with `effective_tags`) and edge dicts, already sorted by SQL, inside one read transaction. |
| `api/dump.py` | `dump_lines(db_path) -> Iterator[str]`: open read-only, check migrations, serialise. It raises `DumpError` when dump can't run. |
| `cli/dump.py` | `main(argv=None) -> int`. `pyproject` gets `dump = "graph_undo.cli.dump:main"`. |

Python sorts the lines again after SQL sorts them. That guarantees the byte order whatever collation SQLite uses for non-ASCII names.

## Tests

1. A dump right after `load` equals the seed's node lines with `effective_tags` added, plus its edge lines, all in dump order.
2. A dump after `apply` of seed 42 equals `tests/replay.py`'s state (names, fields, parents, references). Every node's `effective_tags` equals an independent ancestor walk.
3. Output is sorted, and two dumps of the same db are byte-identical.
4. The db file's bytes and mtime are unchanged after a dump, and no `-journal`/`-wal` file is left behind.
5. Every `from_id` and `to_id` in the dump is the `id` of a dumped node.
6. Deleted rows never appear: after `delete_node`, the subtree is absent.
7. The exit-1 cases each print a message and produce no stdout.
8. `uv run dump --db …` works through the entry point.
9. An empty graph produces empty output.

## Left to the builder

- The exact SQL.
- The error message wording.
