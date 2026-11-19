# delete_node

Deletes a node, every descendant along live `contains` edges, and every live edge (of either type, in either direction) that touches any of them. Human only.

Nothing is physically removed. Each removed row gets `deleted_by = op_id`, and the op records exactly those rows in `removed_nodes` and `removed_edges`. That record is what [undo](undo.md) works from. The names of removed nodes are free to reuse straight away.

## CLI

One line in an ops file, then `uv run apply ops.jsonl [--db graph.db]`:

```json
{"op_id":"op-0005","actor":{"kind":"human","name":"ann"},"op":"delete_node","node":"conn/db/sc"}
```

## Python

```python
# doctest-run
from graph_undo.api.registry import apply_op
from graph_undo.domain.ops import parse_op

result = apply_op(store, parse_op({"op_id": "op-0005", "actor": {"kind": "human", "name": "ann"},
                                   "op": "delete_node", "node": "conn/db/sc"}))
assert (result.removed_nodes, result.removed_edges) == (4, 5)    # sc, t, u, k; 4 contains + 1 reference
node_ids, edge_ids = store.log.removals("op-0005")
assert sorted(node_ids) == ["k", "s", "t", "u"]
```

## Errors

| code | message |
|---|---|
| `node_not_found` | `node '{qn}' is not a live node` (never existed, already deleted, or removed with an ancestor) |
| `actor_not_allowed` | `ingest may not delete_node` |

## Records

- **`ops` row:** `node_id` is the root of the deleted subtree.
- **Removals:** one `removed_nodes` row per node and one `removed_edges` row per edge, all with `deleted_by = op_id`.
- **Rows already dead** keep their earlier `deleted_by` and are not listed again.
