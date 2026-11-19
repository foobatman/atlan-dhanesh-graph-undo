# add_edge

Adds a `contains` or `references` edge between two live nodes. Human or ingest.

| type | rules |
|---|---|
| `references` | both endpoints live, `from != to`, and no identical live edge |
| `contains` | as for `references`, plus: the child has no live parent, and the child is exactly one level below the parent (`connection > database > schema > table > column`) |

To **move** a node, use `delete_edge contains` and then `add_edge contains` for the same child. To **re-point** a reference, use `delete_edge` then `add_edge`.

## CLI

One line in an ops file, then `uv run apply ops.jsonl [--db graph.db]`:

```json
{"op_id":"op-0006","actor":{"kind":"human","name":"ann"},"op":"add_edge","from":"conn/db/sc/u","to":"conn/db/sc/t","type":"references"}
```

## Python

```python
# doctest-run
from graph_undo.api.registry import apply_op
from graph_undo.domain.ops import parse_op

result = apply_op(store, parse_op({"op_id": "op-0006", "actor": {"kind": "human", "name": "ann"},
                                   "op": "add_edge", "from": "conn/db/sc/u", "to": "conn/db/sc/t",
                                   "type": "references"}))
assert store.edges.live("u", "t", "references").id == result.edge_id
```

## Errors

| code | message |
|---|---|
| `node_not_found` | `from '{qn}' is not a live node` / `to '{qn}' is not a live node` |
| `self_loop` | `{type} edge from '{qn}' to itself` |
| `type_order` | `contains '{p}' ({ptype}) -> '{c}' ({ctype}): a {ptype} can only contain a {next type}` (or `cannot contain anything`) |
| `duplicate_edge` | `{type} edge '{from}' -> '{to}' already exists (edge {id})` |
| `already_has_parent` | `'{child}' already has parent '{parent}' (edge {id}); delete that contains edge first` |

## Records

**`ops` row:** `edge_id` is the new edge, which gets a fresh row id. A dead edge between the same pair keeps its own row.
