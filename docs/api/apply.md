# apply

Applies operations in order. Each op runs in its own transaction and is recorded in the op log (`ops`). Deletes also record exactly what they removed, in `removed_nodes` and `removed_edges`, so they can be undone later. If an op is invalid, its error is printed, that op alone is skipped, and the rest carry on.

## CLI

```sh
uv run apply fixtures/ops.jsonl              # against ./graph.db
uv run apply fixtures/ops.jsonl --db PATH
```

- **Rejections:** each one goes to stderr as `ops.jsonl:<line>: <op_id> <code>: <message>`.
- **Summary:** always printed last, e.g. `applied ops.jsonl to graph.db: applied=302 noop=7 skipped=0 rejected=0`.
- **Re-running:** an op that is already logged with the same payload counts as `skipped` and is not applied again.

## Python: a whole file

```python
# doctest-run
from graph_undo.api.apply import ApplyError, apply_file

ops = tmp_path / "ops.jsonl"
ops.write_text('{"op_id":"op-0001","actor":{"kind":"human","name":"ann"},"op":"edit_node",'
               '"node":"conn/db/sc","set":{"owner":"bob"}}\n')
rejected = []
summary = apply_file(ops, tmp_path / "docs.db", on_reject=lambda line, e: rejected.append((line, str(e))))
assert (summary.applied, summary.rejected, rejected) == (1, 0, [])
```

`apply_file` raises `ApplyError` only when nothing can run: the file or database is missing, or migrations are pending.

## Python: a single op

Every op on the next pages is applied the same way:

```python
from graph_undo.api.registry import apply_op
from graph_undo.domain.ops import parse_op

result = apply_op(store, parse_op({...}))     # OpResult(op_id, effect, node_id, edge_id, removed_nodes, removed_edges)
```

- **`parse_op`** checks only the shape of the op, raising `malformed` or `actor_not_allowed`.
- **`apply_op`** checks the graph rules, writes the change and its op-log row in one transaction, and returns the `OpResult`.
- **`effect`** is one of:
  - `applied`;
  - `noop`, for an ingest `add_node` of a live name;
  - `skipped`, when the same op was already applied.

## Op format (every op)

```json
{"op_id": "op-0001", "actor": {"kind": "human" | "ingest", "name": "ann"}, "op": "<kind>", ...fields}
```

- **`op_id`:** must match `op-NNNN` (4 or more digits).
- **Node references:** nodes are always named by `qualified_name`, resolved against the live graph when the op is applied, and never by id.
- **Op kinds:** see [add_node](add_node.md), [edit_node](edit_node.md), [delete_node](delete_node.md), [add_edge](add_edge.md) and [delete_edge](delete_edge.md).
