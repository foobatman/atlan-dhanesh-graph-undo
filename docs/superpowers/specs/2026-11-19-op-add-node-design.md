# `apply` op: `add_node`, design

**Status:** draft
**Builds on:** `2026-11-19-apply-foundation-design.md` (binding: layers, `OpError`s, repositories, op log, id minting, transactions), `2026-11-19-ops-fixture-design.md` "Operation contract", `2026-11-19-load-design.md` (flag-in-place schema).
**Code:** `src/graph_undo/api/add_node.py`: `handle(ctx: OpContext, op: AddNode) -> OpResult`.

## Intent

Create one new node under a live parent, linked by a new `contains` edge. A human `add_node` claims a free name with authored fields. An ingest `add_node` is an upsert by name. It creates the node only when no live node has the name, and otherwise changes nothing. Every successful call (applied or noop) leaves one op-log row, so a later undo can tell which live nodes were born after a delete, and who created them.

## Semantics

**Both actors**
- `qualified_name`, `parent` and `type` arrive already shape-checked by `parse_op`. Names are resolved against the **live** graph at apply time (`NodeRepo.live_by_name`).
- **Dead rows never block.** A dead row (`deleted_by IS NOT NULL`) with the same `qualified_name` does not count as "live", and `live_name` is a partial index, so a new live row can coexist with it. The dead row is not read, changed or revived. Its id, authored fields, `deleted_by` and its dead edges stay exactly as they were.
- **Parent is resolved by name to the live node.** If the parent's original row is dead and its name was recreated, the new node hangs under the recreated id. This is what the fixture's ingest run relies on: tables go under recreated R.
- **Id:** `ctx.store.mint_node_id(op.op_id)`, i.e. `uuid5(NAMESPACE, op_id)`. It is deterministic across fresh runs and always differs from any seed id or dead row's id. A collision would be an `IntegrityError`, which is a bug.
- **Type order:** `LEVEL[op.type] == LEVEL[parent.type] + 1` (connection 0 < database 1 < schema 2 < table 3 < column 4). So a same-level parent, a skipped level, a column as parent and an upward link are all rejected.
- **Writes on create:** the node row, then the edge `parent.id → new id`, type `contains`. Because the new node has no children and no other parent, cycle and `AlreadyHasParent` checks can't fire and are not run.

**Human** (`description`, `owner`, `tags` required by `parse_op`)
- If the name is live, the op fails with `NameTaken`. It never updates the existing node.
- Authored fields are stored as given, with `tags` normalised (sorted, duplicate-free) through `domain.model.normalise_tags`.

**Ingest** (no authored fields; `parse_op` raises `ActorNotAllowed` if any are present)
- **If the name is live, the op is a noop.** Nothing changes, even if `type` or `parent` differ from the live node's, and even if `parent` isn't live or the type order would be wrong. The upsert matches on name only. Ingest never moves, retypes or overwrites a node (fixture case E4).
- Otherwise, the node is created with blank authored fields: `description = ""`, `owner = ""`, `tags = ()`.

**Root connections.** `add_node` **cannot** create a `connection`. The contract requires `parent` for both actors, and `parse_op` rejects a missing or null parent with `MalformedOp`. No live node is at level −1, so any parent fails the type-order check with `TypeOrderViolation`. Connections come only from the seed, and the fixture never adds one. Allowing parentless adds would need a contract change (a nullable `parent`) for no scenario that needs it.

## Validation order

The checks run in this order, and the first failure wins:

1. **Name live?**
   - Ingest: noop (stop, log the noop row).
   - Human: `NameTaken`.
2. **Parent live?** If not, `NodeNotFound`.
3. **Type order.** If it fails, `TypeOrderViolation`.

Why the name check comes first:
- It matches `tests/replay.py` and the fixture generator.
- It makes ingest's noop independent of parent and type, as the contract requires ("nothing changes").
- For humans it reports the most fundamental conflict. If both the name and the parent are bad, the user sees `name_taken`.

Shape errors (`MalformedOp`, `ActorNotAllowed`) come earlier, from `parse_op`. An already-logged `op_id` is handled by `apply_op` (skip or `OpIdConflict`) before the handler runs.

## Writes

The handler writes, in the op's transaction (owned by `apply_op`):

| case | `nodes` | `edges` | `ops` row |
|---|---|---|---|
| applied (human/ingest) | 1 insert: `id = mint`, fields as above, `deleted_by NULL` | 1 insert: `parent.id → id`, `contains` | `effect='applied'`, `node_id = new id`, `edge_id = new contains edge id`, `before NULL` |
| ingest noop | nothing | nothing | `effect='noop'`, `node_id = existing live node's id`, `edge_id NULL`, `before NULL` |
| rejected | nothing | nothing | **nothing**: rolled back by `apply_op` |

- Every `ops` row also has `op_id`, `actor_kind`, `actor_name`, `op='add_node'`, and `payload = canonical(op.raw)`. `seq` is assigned by SQLite.
- No `removals` rows are written.
- **Noop `edge_id` is NULL** on purpose. `edge_id` means "the edge this op created". Pointing it at the existing parent edge would wrongly suggest ownership.
- **Return value:**
  - applied: `OpResult(op_id, "applied", node_id=new, edge_id=edge.id)`
  - noop: `OpResult(op_id, "noop", node_id=existing.id)`

## Errors

`str(e) == f"{op_id} {code}: {message}"`. Messages use the bare qualified name with no quotes. Names are paths without spaces.

| class (code) | when | message template |
|---|---|---|
| `NameTaken` (`name_taken`) | human, name live | `{qualified_name} is already live as node {existing.id}` |
| `NodeNotFound` (`node_not_found`) | parent not live (never existed, or only dead rows) | `parent '{qn}' is not a live node` |
| `TypeOrderViolation` (`type_order`) | `LEVEL[type] != LEVEL[parent.type] + 1` | `{type} {qualified_name} cannot go under {parent.type} {parent}: contains must go exactly one level down` |

Example: `op-0007 type_order: table conn/db/sc/x cannot go under table conn/db/sc/t: contains must go exactly one level down`.

From `parse_op` (not raised by this handler): `MalformedOp` and `ActorNotAllowed`. No new error classes.

## Repository methods used

All of these are foundation methods:
- `store.nodes.live_by_name(qn)`
- `store.nodes.insert(Node)`
- `store.edges.insert(from_id, to_id, "contains") -> Edge`
- `store.log.record(LoggedOp)`
- `store.mint_node_id(op_id)`

The handler issues no SQL.

## Foundation changes requested

These are clarifications. No new methods or tables are needed.

1. **`LoggedOp` fields.** Spell out that `LoggedOp` has `op_id, actor_kind, actor_name, op, effect, node_id, edge_id, before, payload` (the `ops` columns minus `seq`).
2. **`canonical(raw) -> str`.** Make it importable from `domain/ops.py`, so handlers build `payload` with the same function `apply_op` uses to compare.
3. **`AddNode.tags`.** State that `parse_op` normalises `AddNode.tags` (sorted, unique), as it does for `EditNode.set["tags"]`. The handler also calls `normalise_tags`, which is idempotent, so either way the stored value is correct.

## What the log gives later undo

Undo itself is out of scope. The log gives it the following:

- **Nodes born after a delete:** `ops` rows with `op='add_node' AND effect='applied' AND seq > <delete seq>`, joined to `nodes` on `node_id`.
  - If such a node's `qualified_name` equals a removed node's name, the name is now held by a new id. That is the "name not free" case behind invariants 2 and 3.
  - `actor_kind` says whether it was ingest recreation or a human claim. A human claim must never be lost (invariant 1, fixture E3).
- **The node's original parent edge:** `edge_id`. Undo can tell whether the node still hangs from that edge or was moved later.
- **Noop rows** record that ingest observed a live name and changed nothing (E4). Undo can ignore them, but they make "ingest never overwrote" checkable from the log.
- **`before` is always NULL.** There was no prior state: the node did not exist.

## Out of scope

- Undo and redo, `CRASH_AFTER`, and the CLI. `apply_file` prints rejections as the foundation describes.
- Moving or retyping an existing node.
- Adding root connections.
- Cycle checks, which can't apply to a brand-new leaf.
