# `apply` op: `delete_edge` — design

**Status:** draft
**Brief:** `problem.md` ("edges re-pointed to other nodes"; "Record enough that any delete-subtree can be undone later").
**Builds on (binding):**
- `2026-11-19-apply-foundation-design.md`: layout, `DeleteEdge`, `OpError` classes, repositories, `OpContext`/`OpResult`, `ops` + `removals`, per-op transaction, print-roll-back-skip.
- `2026-11-19-ops-fixture-design.md`, "Operation contract": `delete_edge` is human only, `from`/`to`/`type`. "The edge must exist."
- `2026-11-19-load-design.md`: flag-in-place. `edges.deleted_by`, edge row ids, partial unique indexes.

## Intent

`graph_undo/api/delete_edge.py: handle(ctx, op: DeleteEdge) -> OpResult` removes one live edge, `(from, to, type)`, named by qualified names. It is the first half of a re-point (`delete_edge` then `add_edge references`, two ops by the same human) and of a move (`delete_edge contains`, then straight away `add_edge contains` for the same child).

## Semantics

1. **Human only.** An ingest `delete_edge` is rejected in `parse_op` with `ActorNotAllowed`, before any key-set check, so the handler never sees one. Shape errors (missing or extra keys, unknown `type`, non-string fields) are `MalformedOp` from `parse_op`.
2. **Resolve endpoints by live name.** `NodeRepo.live_by_name(op.from_)` first, then `op.to`. The first one that isn't live raises `NodeNotFound`, so if both are missing, `from` is reported. A dead node with that name doesn't count.
3. **The live edge must exist.** `EdgeRepo.live(from.id, to.id, op.type)`. `None` raises `EdgeNotFound`. Direction and type are both part of the key:
   - deleting `A→B` never touches `B→A`;
   - `references A→B` never matches `contains A→B`.

   A dead row with the same endpoints and type doesn't count, even if this op would have removed it. The partial index `live_edge` guarantees at most one live match.
4. **Soft delete, one row, one transaction (the key requirement).** Inside the transaction `apply_op` opened:
   - `edges.mark_deleted([e.id], op.op_id)`: sets `deleted_by = op_id` on that single row;
   - `log.record(LoggedOp(...))`: `op='delete_edge'`, `effect='applied'`, `node_id=NULL`, `edge_id=e.id`, `before=NULL`, `payload=canonical(op.raw)`, actor kind and name;
   - `log.add_removals(op.op_id, [], [e.id])`: exactly one row, `('<op_id>', 'edge', str(e.id))`.

   `record` must run before `add_removals`, because `removals.op_id REFERENCES ops(op_id)` and foreign keys are on. The order of `mark_deleted` doesn't matter. The handler never hard-deletes and never touches `nodes`. Returns `OpResult(op_id, "applied", edge_id=e.id, removed_edges=1)`. There is no `noop` outcome: an edge that isn't live is an error, not a no-op (contract: "the edge must exist").
5. **Deleting a `contains` edge** leaves the child live and parentless (`live_parent` returns `None`). Through the `effective_tags` view, the child and its whole live subtree immediately lose the tags of the old parent and the old parent's ancestors, while keeping their own tags and the tags of ancestors inside the subtree. Nothing is stored, because the view derives it.
6. **No re-attach enforcement.** Apply doesn't check that an `add_edge contains` follows. A parentless non-connection node is a legal state between ops, and also after a run. Reasons:
   - every op is applied and logged on its own (foundation: one op, one transaction);
   - with print-and-skip, a rejected `add_edge` can't retroactively undo an already-committed `delete_edge`;
   - looking ahead in the file would make the single-op CLI and file apply behave differently, which the foundation forbids.

   The fixture guarantees the pairing. Apply doesn't depend on it. If the pair's `add_edge` is rejected, the child stays parentless and that rejection is printed.
7. **Re-delete after re-add.** After `delete_edge` (op-a), `add_edge` of the same triple (new row id), then `delete_edge` (op-b), op-b marks only the new live row. The old row keeps `deleted_by = op-a` and its `removals` row. `mark_deleted` only updates live rows, and the handler passes only the id `EdgeRepo.live` returned. The result is two dead rows and two `removals` rows, one per op.
8. **Self-loop request (`from == to`) → `EdgeNotFound`, not `SelfLoop`.** The schema's `CHECK (from_id <> to_id)` means no such edge can exist, so "the edge isn't live" is the true answer. `SelfLoop` describes creating an edge, which is `add_edge`'s rule. The handler has no special case: if the node is live, the lookup returns `None` and raises `EdgeNotFound`. If it isn't live, it raises `NodeNotFound`.
9. **Cascade-removed edges.** An edge removed by a `delete_node` has at least one dead endpoint, so a later `delete_edge` naming it fails at step 2 with `NodeNotFound`. If the name has since been recreated, the lookup uses the new id, finds no live edge and raises `EdgeNotFound`. Either way, the cascade's `deleted_by` and `removals` rows are never changed.

## Validation order

`parse_op` (actor, then shape) → `from` live → `to` live → live edge `(from.id, to.id, type)` → writes. Each check raises before any write, and the transaction is rolled back anyway, so a rejected op leaves the `nodes`, `edges`, `ops` and `removals` tables byte-identical.

## Errors

`str(e) == f"{op_id} {code}: {message}"`. Names are printed with `repr`-style single quotes.

| class | code | when | message template |
|---|---|---|---|
| `ActorNotAllowed` | `actor_not_allowed` | ingest `delete_edge` (in `parse_op`) | `ingest may not delete_edge` |
| `MalformedOp` | `malformed` | shape (in `parse_op`) | foundation's text |
| `NodeNotFound` | `node_not_found` | `from` or `to` not live | `from 'conn/db/x' is not a live node` / `to '…' is not a live node` |
| `EdgeNotFound` | `edge_not_found` | no live edge with that triple (including `from == to`) | `no live references edge 'conn/a' -> 'conn/b'` (the type word is `op.type`) |

Example: `op-0042 edge_not_found: no live contains edge 'conn/db/sc/t' -> 'conn/db/sc/t/k'`.

## Repository methods used

`NodeRepo.live_by_name`, `EdgeRepo.live`, `EdgeRepo.mark_deleted`, `OpLogRepo.record`, `OpLogRepo.add_removals`. The tests also read `EdgeRepo.live_parent`, `OpLogRepo.get` and `OpLogRepo.removals`. No new methods are needed.

## Foundation changes requested

1. **Shared message templates.** The foundation fixes codes but not message text. Please put the `NodeNotFound` template (`"<field> '<qn>' is not a live node"`) in `domain/errors.py`, for example as `NodeNotFound.for_field(op_id, field, qn)`, so all five ops print the same text. The same goes for the `ActorNotAllowed` text (`"ingest may not <op>"`).
2. **`LoggedOp` definition.** Please spell out its fields: `op_id, actor_kind, actor_name, op, effect, node_id, edge_id, before, payload`, with `seq` read-only. Also fix where `canonical(raw)` lives (suggested: `domain/ops.py`).
3. **Write order.** State in the handler contract that `log.record` comes before `log.add_removals`, because of the `removals → ops` foreign key.
4. **Parse precedence.** For ingest with a forbidden op, `ActorNotAllowed` is raised before the key-set check, whatever keys are present.

## What a later undo gets from this record

- **The `ops` row** (`op='delete_edge'`, `edge_id`, actor) says a human removed exactly this edge row on purpose. The other way an edge dies is a `delete_node` cascade. There `deleted_by` points at an `ops` row with `op='delete_node'` and `edge_id` NULL, and the edge is one of many `removals` rows. So `ops.op` for `edges.deleted_by` tells a human edge delete apart from a cascade.
- **`removals` + `deleted_by`** keep the history even after re-add and re-delete (Semantics 7): each delete owns exactly its own rows.
- **Re-point and move pairs** are not linked in the log. A later spec that needs them can find a human `delete_edge` followed by the next op being an `add_edge` with the same actor and the same `from` (re-point) or `to` (move). That's a heuristic for undo to decide; apply records only facts.
- **Interaction with undoing a subtree delete:** a human `delete_edge` after the big delete is a "human change after the delete" (invariant 1). Undo must not resurrect an edge that a human removed afterwards. The row's `deleted_by` points at the human op, not the subtree delete, so undo can tell.

## Out of scope

Undo and redo of `delete_edge`; linking re-point pairs; enforcing re-attachment; cycle or orphan checks (those are `add_edge`'s); `dump`; `CRASH_AFTER`.
