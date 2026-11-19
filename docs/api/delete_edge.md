# delete_edge

Deletes one live edge. Human only. Deleting a `contains` edge leaves the child without a parent until an `add_edge contains` gives it one; that pair is how a node is moved.

## CLI

One line in an ops file, then `uv run apply ops.jsonl [--db graph.db]`:

```json
{"op_id":"op-0007","actor":{"kind":"human","name":"ann"},"op":"delete_edge","from":"conn/db/sc/t","to":"conn/db/sc/u","type":"references"}
```

## Python

```python
# doctest-run
from graph_undo.api.registry import apply_op
from graph_undo.domain.ops import parse_op

result = apply_op(store, parse_op({"op_id": "op-0007", "actor": {"kind": "human", "name": "ann"},
                                   "op": "delete_edge", "from": "conn/db/sc/t", "to": "conn/db/sc/u",
                                   "type": "references"}))
assert store.log.removals("op-0007") == ([], [result.edge_id])
```

## Errors

| code | message |
|---|---|
| `node_not_found` | `from '{qn}' is not a live node` / `to '{qn}' is not a live node` |
| `edge_not_found` | `no live {type} edge '{from}' -> '{to}'` |
| `actor_not_allowed` | `ingest may not delete_edge` |

## Records

- **`ops` row:** `edge_id` is the removed edge.
- **Removals:** one `removed_edges` row. The edge gets `deleted_by = op_id`.
- **Undo:** possible with [undo](undo.md).
