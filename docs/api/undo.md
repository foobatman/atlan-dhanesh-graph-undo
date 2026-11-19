# undo

Reverses one applied `delete_node` or `delete_edge`, working on the graph as it is now. Each node and edge that the delete removed ends up in one of three states:
- `restored`: its original row is live again;
- `merged`: a `contains` edge that was recreated under a different parent;
- `skipped`: left dead, with a reason.

**Each op can be undone once.** A second attempt changes nothing and fails with `already_undone`. The whole undo runs in one transaction.

## CLI

```sh
uv run undo op-0001                       # against ./graph.db
uv run undo op-0001 --db PATH > report.jsonl
```

- **stdout:** the JSONL report.
- **stderr:** `undid op-0001 on graph.db: nodes restored=546 skipped=68; edges restored=584 merged=87 skipped=103`.
- **Exit code:** 0 on success. 1 on any error, which goes to stderr as `{op_id} {code}: {message}`.

## Python

```python
# doctest-run
import json
from graph_undo.api.registry import apply_op
from graph_undo.api.undo import undo_op
from graph_undo.domain.errors import AlreadyUndone
from graph_undo.domain.ops import parse_op

apply_op(store, parse_op({"op_id": "op-0008", "actor": {"kind": "human", "name": "ann"},
                          "op": "delete_node", "node": "conn/db/sc"}))
result = undo_op(store, "op-0008")             # UndoResult(op_id, lines, counts)
assert len(result.lines) == 4 + 5              # one line per removed node and edge
assert result.summary()["node"] == {"restored": 4, "skipped": 0}
assert json.loads(result.lines[0])["outcome"] == "restored"
try:
    undo_op(store, "op-0008")
except AlreadyUndone as e:
    assert str(e).startswith("op-0008 already_undone: already undone (undo #1, after op-0008)")
```

`undo_db(path, op_id)` opens the database, undoes the op and closes it again. It raises `ApplyError` if the database is missing or not migrated.

## Rules (rule B2)

Nodes are decided **parents first**. The first rule that matches wins:

| # | condition | outcome | `reason_code` |
|---|---|---|---|
| 1 | a live node holds the name | skipped | `name_held` |
| 2a | it is the root and had no parent | restored | |
| 2b | its original parent is live, or was restored by this undo | restored under it | |
| 2c | the live node holding the parent's name has the parent's type | restored under that node | |
| 2d | that node has a different type | skipped | `parent_type_mismatch` |
| 2e | otherwise | skipped | `no_live_parent` |

A restored node keeps its id, description, owner and tags. **No live node is ever edited.**

Edges are decided after the nodes:

| edge | outcome | `reason_code` |
|---|---|---|
| `contains` into a node restored under its original parent | restored | |
| `contains` into a node restored under a different parent (2c) | merged: a new edge is created, see `new_edge_id` | `reparented` |
| `contains` into a skipped node | skipped | `endpoint_not_restored` |
| any other edge whose endpoints are both live | restored | |
| an endpoint was removed by this delete and skipped | skipped | `endpoint_not_restored` |
| an endpoint was deleted later by another op | skipped | `endpoint_deleted` |
| an identical live edge exists (`delete_edge` undo) | skipped | `duplicate_edge` |
| the child has a new parent (`delete_edge contains` undo) | skipped | `child_has_parent` |

References are never pointed at whichever node now holds an endpoint's name.

### Example

```
Deleted:                At undo time:                   Undo:
core   (schema)         core′  (ingest recreated)       core          skipped name_held
├─ orders               ├─ orders′ (ingest)             orders        skipped name_held
│  ├─ id                │  └─ id′  (ingest)             id            skipped name_held
│  └─ total             │                               total         restored under orders′  (edge merged)
└─ users                                                users         restored under core′    (edge merged)
   └─ email                                             email         restored under users    (edge restored)
```

## Report

The report has one JSON object per line, in canonical form (sorted keys, compact). There is one line for **every** node and edge the op removed, and no other lines. Node lines come first, ordered by `(qualified_name, id)`, then edge lines ordered by `id`.

| key | on |
|---|---|
| `kind` (`node` / `edge`), `id`, `op_id`, `outcome` | every line |
| `qualified_name` | nodes |
| `parent_id` | restored nodes: the node it now hangs under (`null` for a root) |
| `from_id`, `to_id`, `type` | edges (the original edge) |
| `new_edge_id` | merged edges |
| `reason_code`, `reason` | merged and skipped items |

```
{"id":"…","kind":"node","op_id":"op-0001","outcome":"skipped","qualified_name":"postgres/ops/core","reason":"name held by 570a… created by op-0092 (ingest)","reason_code":"name_held"}
{"id":"…","kind":"node","op_id":"op-0001","outcome":"restored","parent_id":"570a…","qualified_name":"postgres/ops/core/users"}
{"from_id":"…","id":1203,"kind":"edge","new_edge_id":2950,"op_id":"op-0001","outcome":"merged","reason":"recreated as 570a… → 9ab…","reason_code":"reparented","to_id":"…","type":"contains"}
```

## Errors

| code | message |
|---|---|
| `malformed` | `op_id '{op_id}' does not match op-NNNN` |
| `op_not_found` | `no applied op with id '{op_id}'` |
| `not_undoable` | `{op} cannot be undone; only delete_node and delete_edge can` |
| `already_undone` | `already undone (undo #{n}, after {last op}); restored {r} nodes, {re} edges, merged {m}, skipped {s}. Undo runs once per op.` |

## Records

- **`undos`:** one row per undo. `op_id` is unique, which is what enforces "once per op". It also stores `after_seq`, the last applied op when the undo ran, and `report`, the exact JSONL printed.
- **`undo_items`:** one row per report line, with `outcome`, `reason_code` and `new_edge_id`.
- **Not in `ops`:** undo is not logged in `ops`, so a later `apply` works exactly as before.

Design: `docs/superpowers/specs/2026-11-19-undo-design.md`. Why these rules: [`DECISIONS.md`](../../DECISIONS.md).
