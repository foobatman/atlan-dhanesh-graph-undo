# edit_node

Replaces some of a live node's authored fields. Human only.

| actor | fields | effect |
|---|---|---|
| human | `node`, `set` | `set` is a non-empty object whose keys come from `description`, `owner` and `tags`. Each given field is replaced. `tags` is stored sorted and duplicate-free. |

## CLI

One line in an ops file, then `uv run apply ops.jsonl [--db graph.db]`:

```json
{"op_id":"op-0004","actor":{"kind":"human","name":"ann"},"op":"edit_node","node":"conn/db/sc","set":{"owner":"bob","tags":["gold","a"]}}
```

## Python

```python
# doctest-run
from graph_undo.api.registry import apply_op
from graph_undo.domain.ops import parse_op

apply_op(store, parse_op({"op_id": "op-0004", "actor": {"kind": "human", "name": "ann"}, "op": "edit_node",
                          "node": "conn/db/sc", "set": {"owner": "bob", "tags": ["gold", "a"]}}))
node = store.nodes.live_by_name("conn/db/sc")
assert (node.owner, node.tags) == ("bob", ("a", "gold"))
```

## Errors

| code | message |
|---|---|
| `node_not_found` | `node '{qn}' is not a live node` |
| `actor_not_allowed` | `ingest may not edit_node` |
| `malformed` | `set must be a non-empty object`, `set has unknown key '...'`, `set.tags must be a list of strings` |

## Records

**`ops` row:** `node_id` is the edited node. `before` holds the previous values of the fields that were set, as JSON. Descendants' `effective_tags` change at once, because `effective_tags` is a view.
