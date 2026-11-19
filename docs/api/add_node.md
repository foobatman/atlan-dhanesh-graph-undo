# add_node

Creates a node and the `contains` edge from its parent. The new id is deterministic: `uuid5(NAMESPACE, op_id)`.

| actor | fields | effect |
|---|---|---|
| human | `qualified_name`, `type`, `parent`, `description`, `owner`, `tags` | Creates the node with those fields (tags sorted, duplicates removed). The name must not be live. |
| ingest | `qualified_name`, `type`, `parent` | **Upserts by name.** If the name is live, this is a `noop` and nothing changes. Otherwise it creates the node with `description: ""`, `owner: ""`, `tags: []`. |

## CLI

Put the op on one line of an ops file and run `uv run apply ops.jsonl [--db graph.db]`:

```json
{"op_id":"op-0002","actor":{"kind":"human","name":"ann"},"op":"add_node","qualified_name":"conn/db/sc/v","type":"table","parent":"conn/db/sc","description":"orders v2","owner":"ann","tags":["pii"]}
```

## Python

```python
# doctest-run
from graph_undo.api.registry import apply_op
from graph_undo.domain.ops import parse_op

result = apply_op(store, parse_op({
    "op_id": "op-0002", "actor": {"kind": "human", "name": "ann"}, "op": "add_node",
    "qualified_name": "conn/db/sc/v", "type": "table", "parent": "conn/db/sc",
    "description": "orders v2", "owner": "ann", "tags": ["pii"]}))
assert result.effect == "applied" and result.node_id == store.mint_node_id("op-0002")

again = apply_op(store, parse_op({
    "op_id": "op-0003", "actor": {"kind": "ingest", "name": "crawler"}, "op": "add_node",
    "qualified_name": "conn/db/sc/v", "type": "table", "parent": "conn/db/sc"}))
assert again.effect == "noop"                       # ingest never overwrites a live node
```

## Errors

| code | message |
|---|---|
| `name_taken` | `{qualified_name} is already live as node {id}` (human only) |
| `node_not_found` | `parent '{qn}' is not a live node` |
| `type_order` | `{type} {qualified_name} cannot go under {parent type} {parent qn}: contains must go exactly one level down` |
| `actor_not_allowed` | `ingest may not set {fields}` (ingest sending `description`, `owner` or `tags`) |
| `malformed` | wrong or missing keys, bad `type` |

## Records

- **`ops` row:** `effect` is `applied` or `noop`. `node_id` is the new node, or the live node for a `noop`. `edge_id` is the new `contains` edge.
- A node created by an op can later hold the name of a deleted node. [undo](undo.md) reports this holder as `created by <op_id> (<actor kind>)`.
