# API reference

Every operation can be run two ways: from the shell (`uv run <command>`), or from Python through the `graph_undo.api` layer. Both give the same results and print the same error text. Each page below shows both.

| Operation | What it does | Page |
|---|---|---|
| load | create the database from `seed.jsonl` | [load](load.md) |
| migrate | bring an existing database up to the latest schema | [migrate](migrate.md) |
| apply | apply a file of ops, or one op | [apply](apply.md) |
| `add_node` | create a node under a parent (human), or upsert by name (ingest) | [add_node](add_node.md) |
| `edit_node` | replace a node's description, owner or tags | [edit_node](edit_node.md) |
| `delete_node` | delete a node, its `contains` subtree and every edge touching them | [delete_node](delete_node.md) |
| `add_edge` | add a `contains` or `references` edge | [add_edge](add_edge.md) |
| `delete_edge` | delete one edge | [delete_edge](delete_edge.md) |
| undo | reverse one `delete_node` or `delete_edge`, once | [undo](undo.md) |
| dump | print the live graph as sorted JSONL (read-only) | [dump](dump.md) |

The design behind undo's conflict rules is in [`DECISIONS.md`](../../DECISIONS.md).

## Layers

```
cli  ->  api  ->  db  ->  domain
```

- **`domain/`**: pure Python values. Covers `Node`, `Edge`, the typed ops, `parse_op`, the `OpError` classes and the undo planner.
- **`db/`**: the only package with SQL. It holds `Store`, with repositories `.nodes`, `.edges`, `.log` and `.undo_log`, plus the migrations.
- **`api/`**: the graph rules. Call these from Python.
- **`cli/`**: thin wrappers that print results and set exit codes.

## Opening a database from Python

```python
from graph_undo.db.store import Store

store = Store.open("graph.db")     # foreign keys on; every transaction is explicit
try:
    ...                            # apply_op(store, ...), undo_op(store, ...)
finally:
    store.close()
```

`apply_op` and `undo_op` each run in their own transaction. If they fail they roll back and leave the database unchanged, so the caller never commits.

## Errors

Every rejected op or undo raises a subclass of `graph_undo.domain.errors.OpError`. The text is the same everywhere:

```
str(e) == f"{op_id} {code}: {message}"      # e.g. "op-0002 node_not_found: node 'conn/db/sc' is not a live node"
```

`e.op_id`, `e.code` and `e.message` are available separately. When no `op_id` could be read (a line that isn't JSON, for example), the text is `"{code}: {message}"`.

| code | raised when | raised by |
|---|---|---|
| `malformed` | bad JSON, wrong, missing or extra keys, wrong types, unknown `op` or `type`, an `op_id` that doesn't match `op-NNNN` | `parse_op`, `undo_op` |
| `actor_not_allowed` | ingest sends `edit_node`, `delete_node` or `delete_edge`, or authored fields on `add_node` | `parse_op` |
| `node_not_found` | a `node`/`parent`/`from`/`to` name is not live | all five ops |
| `name_taken` | human `add_node` of a live name | `add_node` |
| `type_order` | a `contains` edge (or an `add_node` parent) is not exactly one level down | `add_node`, `add_edge` |
| `self_loop` | `from == to` | `add_edge` |
| `duplicate_edge` | the live edge `(from, to, type)` already exists | `add_edge` |
| `already_has_parent` | `add_edge contains` onto a child that has a live parent | `add_edge` |
| `edge_not_found` | `delete_edge` of an edge that isn't live | `delete_edge` |
| `op_id_conflict` | the `op_id` is already logged with a different payload | `apply_op` |
| `op_not_found` | `undo` of an `op_id` that is not in the op log | `undo_op` |
| `not_undoable` | `undo` of anything but an applied `delete_node` / `delete_edge` | `undo_op` |
| `already_undone` | `undo` of an op that was already undone | `undo_op` |

## Exit codes

| command | 0 | 1 |
|---|---|---|
| `uv run load` | database built | bad seed (`<seed>:<line>: <message>`), unreadable file |
| `uv run migrate` | migrated, or already up to date | migration failed, or the database is newer than the code |
| `uv run apply` | file processed, **even if some ops were rejected** (each one printed to stderr) | ops file or database missing, migrations pending |
| `uv run undo` | undone, report on stdout | any `OpError` above, database missing, migrations pending |
| `uv run dump` | live graph printed on stdout | database missing or not SQLite, migrations pending, database newer than the code (`DumpError`) |
