# `apply`: `add_edge` operation

**Status:** draft
**Builds on:** `2026-11-19-apply-foundation-design.md` (binding: layout, `OpError` classes, repositories, `OpContext`/`OpResult`, op log, one transaction per op, reject = print, roll back, skip, continue); `2026-11-19-ops-fixture-design.md`, "Operation contract"; `2026-11-19-load-design.md` (partial unique indexes `live_edge`, `live_parent`; edge row ids).

## Intent

`graph_undo/api/add_edge.py: handle(ctx, op: AddEdge) -> OpResult` creates one live edge `from → to` of type `contains` or `references`. In the fixture it is the second half of a re-point (`delete_edge` + `add_edge references`) and of a move (`delete_edge contains` + `add_edge contains`). It is also used for ingest references onto recreated tables and for E1, a human reference to a recreated table.

## Semantics

- **Actors.** Human or ingest, same rules for both. The ingest key set is the same as the human one, and parsing never raises `ActorNotAllowed` for `add_edge`.
- **Resolution.** `from` and `to` are qualified names, resolved with `NodeRepo.live_by_name` when the op is applied. A name that is not live (never existed, or dead) raises `NodeNotFound`. **`from` is resolved and reported first.** If both are missing, only `from` is named. A name recreated after a delete resolves to the **new** id, because the op log stores ids and never names.
- **Self loop.** `from == to` raises `SelfLoop`. This is checked after resolution, so a dead name gives `NodeNotFound`, not `SelfLoop`.
- **Duplicates.** A live edge with the same `(from_id, to_id, type)` raises `DuplicateEdge`. The same pair with the *other* type is a different edge and is allowed (for example `references s → t` alongside `contains s → t`). The reverse direction is a different edge and is allowed for `references`.
- **Dead edges never block, and are never revived.** If a dead row with the same endpoints and type exists, the handler inserts a **new row with a new id**. The dead row stays exactly as it is, `deleted_by` included. The `live_edge` index is partial, so the two rows coexist.
  Why not revive the dead row:
  - The dead row's `deleted_by` and its `removals` row are the record of what that delete removed. Undo uses them for invariant 4: account for every removed edge exactly once.
  - Reviving the row would make it live again under the delete's `removals` entry. Undo would then report it as "restored" when it was actually a later human or ingest action. It also could not tell the two apart for invariant 1.
  - Reviving would also give two different ops one edge id, so the op log could no longer say which op created the live edge.
- **`contains`, type order.** `LEVEL[to.type]` must equal `LEVEL[from.type] + 1`. Anything else raises `TypeOrderViolation`: the same level, two or more levels down, upward, or `from` a column.
- **`contains`, one parent.** If `to` already has a live parent (`EdgeRepo.live_parent`), the op raises `AlreadyHasParent`, even when the new parent would be valid. A move must delete the old contains edge first.
- **Identical live `contains`.** If the exact `contains from → to` is already live, the error is **`DuplicateEdge`**, not `AlreadyHasParent`. The duplicate check runs first and is the more precise diagnosis: the op asks for an edge that already exists, not for a second parent.
- **Cycles are impossible.** Every `contains` edge goes exactly one level down (the type-order rule, also enforced at load). Along any `contains` path the level therefore rises strictly, so a path can never return to its start. The handler needs no cycle walk. `references` may form cycles; they are not a hierarchy.
- **Moves.** Nodes are never renamed. A moved node keeps its `qualified_name`, even though its path no longer matches its new parent's name (the fixture spec says "identity, not a path"). Its subtree moves with it, and `effective_tags` of the node and all its descendants follow the new ancestors through the `effective_tags` view. Nothing is stored.
- **Ingest duplicate is an error, not a no-op.** The contract lists "edge not already present" as a precondition for `add_edge` with either actor. Only `add_node` has upsert semantics, and it states them explicitly. So an ingest duplicate is a `DuplicateEdge` rejection, like a human one. The fixture never emits one: it checks `has_ref` first. If a crawler re-sending edges ever needs to be idempotent, changing this to `effect='noop'` is a one-line change and logs nothing new.
- **`references` between any node types.** The contract constrains only `contains`, the data model says nothing about `references`, and `tests/replay.py` checks no types for references. A column → database reference is therefore valid. The fixture uses table → table only.

## Validation order

1. Resolve `from`, else `NodeNotFound(from)`.
2. Resolve `to`, else `NodeNotFound(to)`.
3. `from.id == to.id`, else `SelfLoop`.
4. `contains` only: type order, else `TypeOrderViolation`.
5. A live `(from.id, to.id, type)`, else `DuplicateEdge`.
6. `contains` only: `live_parent(to.id)`, else `AlreadyHasParent`.

Steps 1–6 only read. The first failure raises, and `apply_op` rolls back the empty transaction. A rejected op writes no edge row and no op-log row.

## Writes

1. `edge = ctx.store.edges.insert(from.id, to.id, op.type)`. The new id is assigned by SQLite (current max id + 1), and `deleted_by` is NULL.
2. `ctx.store.log.record(LoggedOp(op_id, actor_kind, actor_name, op='add_edge', effect='applied', node_id=None, edge_id=edge.id, before=None, payload=canonical(op.raw)))`.
3. `return OpResult(op.op_id, "applied", edge_id=edge.id)`.

There are no `removals` rows and no node writes. `effective_tags` is a view, so nothing needs recomputing.

## Errors

`str(e)` is `f"{op_id} {code}: {message}"`. `{x!r}` means a Python repr, i.e. a single-quoted name.

| class | code | message template |
|---|---|---|
| `NodeNotFound` | `node_not_found` | `from {qn!r} is not a live node` / `to {qn!r} is not a live node` |
| `SelfLoop` | `self_loop` | `{type} edge from {qn!r} to itself` |
| `TypeOrderViolation` | `type_order` | `contains {f!r} ({f_type}) -> {t!r} ({t_type}): a {f_type} can only contain a {next_type}` or, when `f_type` is `column`: `... : a column cannot contain anything` |
| `DuplicateEdge` | `duplicate_edge` | `{type} edge {f!r} -> {t!r} already exists (edge {id})` |
| `AlreadyHasParent` | `already_has_parent` | `{t!r} already has parent {p!r} (edge {id}); delete that contains edge first` |

Example: `op-0042 already_has_parent: 'conn/db/sc/t' already has parent 'conn/db/sc' (edge 3); delete that contains edge first`.

Shape errors (unknown `type`, missing or extra keys, a non-string `from`) are `MalformedOp`, raised by `parse_op` in the foundation.

## Repository methods used

- `NodeRepo.live_by_name`
- `NodeRepo.by_id` (the parent's name, for the `AlreadyHasParent` message)
- `EdgeRepo.live`
- `EdgeRepo.live_parent`
- `EdgeRepo.insert`
- `OpLogRepo.record`
- `LEVEL` and `NODE_TYPES` from `domain/model.py`

## Foundation changes requested

No new repository methods or error classes. Two clarifications:

1. **Spell out `LoggedOp`.** The foundation uses `LoggedOp` but never defines it. It should be a frozen dataclass in `db/oplog.py` whose fields mirror the `ops` columns minus `seq`: `op_id, actor_kind, actor_name, op, effect, node_id, edge_id, before, payload`.
2. **Give `canonical(raw) -> str` one public home** (e.g. `domain/ops.py`), so handlers and `apply_op` produce the same payload bytes.

## What later undo will need (undo itself is out of scope)

- **`ops.edge_id`** is the exact row this op created. A later undo of the add, or a redo, targets that id and never `(from, to, type)`, which can match several rows over time.
- **`ops.seq` and `actor_kind`** tell undo of a delete-subtree which live edges were added after the delete, and by whom. Two cases matter:
  - A restored node whose parent slot is now taken by a human's `add_edge contains` (`live_parent` conflict).
  - A removed reference X→T alongside a human's new X→T′ (E1/E2).

  Both are invariant-1 cases, settled in DECISIONS.md.
- **Dead rows are left untouched.** Undo can always restore the original row by clearing its `deleted_by`, unless an identical live edge was re-added since. In that case the `live_edge` index forbids the restore, and undo must report the edge as merged.

## Out of scope

Undo and redo, edge attributes (the data model has none), `delete_edge` (its own spec), and a combined re-point op (the contract forbids one).
