# `apply`: `edit_node` design

**Status:** draft
**Brief:** `problem.md`: "three authored fields: description, owner, tags (a list)"; invariant 1, "No change made by a human actor after the delete is lost or altered by the undo."
**Builds on (binding):**
- `2026-11-19-apply-foundation-design.md`: layout, `EditNode`, `OpError` classes, repositories, `OpContext`/`OpResult`, the `ops` table, transactions owned by `apply_op`.
- `2026-11-19-ops-fixture-design.md`, "Operation contract": `edit_node` row.
- `2026-11-19-load-design.md`: flag-in-place rows; `effective_tags` is a view.

## Intent

`edit_node` is the only way a human changes authored fields. It replaces some of `description`, `owner` and `tags` on one live node, and it records the old values of exactly those fields, so a later undo and the checker can tell what a human changed, on which node id, and when.

## Semantics

- **Actor:** human only. An ingest `edit_node` is rejected in `parse_op` with `ActorNotAllowed`. The handler never sees one.
- **Target:** `node` is a qualified name, resolved with `NodeRepo.live_by_name` when the op is applied. The op edits that row's id and no other.
- **`set`:** a non-empty subset of `{description, owner, tags}`. Each key given replaces that field whole. Keys not given are untouched, and so are `id`, `qualified_name`, `type`, `deleted_by` and every edge.
- **Tags:** `parse_op` normalises `set["tags"]` with `normalise_tags()`: sorted, duplicates removed. `[]` clears the node's own tags. Strings are taken as they are: no trimming, no case folding. Empty strings and any unicode round-trip exactly.
- **Dead nodes can't be edited.** Names resolve to live rows only, so a name held only by a dead row gives `NodeNotFound`. If a name was deleted and then recreated (for example by ingest), the edit goes to the new live row. The dead row keeps the values it had at delete time.
- **A set that changes nothing is still `applied`.** It writes a log row like any other edit, with `before` equal to the new values. Why:
  1. A human stating a value after the delete is a human change for invariant 1, even if the value matched. If undo later brings back an older value, the human's statement must still win. A missing log row would hide it.
  2. `noop` already has one meaning, "ingest upsert of a live name". Keeping it to that meaning keeps the apply summary (`noop=6` for seed 42) about ingest.
  3. Fewer branches: the handler doesn't compare old values with new ones.
- **`effective_tags`:** a tag edit changes the effective tags of every live descendant along live `contains` edges. This happens through the view, with no extra writes. Removing a tag from an ancestor removes it from a descendant's effective tags, unless the descendant has that tag itself or gets it from another ancestor.

## Validation order

1. `parse_op` (foundation), shape only, raising `MalformedOp` or `ActorNotAllowed`:
   - the key set is exactly `{op_id, actor, op, node, set}`;
   - an ingest actor raises `ActorNotAllowed`;
   - `node` is a string;
   - `set` is a non-empty object whose keys are a subset of the three fields;
   - `description` and `owner` are strings, and `tags` is a list of strings.

   Then the tags are normalised.
2. `apply_op` (foundation): an `op_id` already in the log is either skipped, or raises `OpIdConflict` if its payload differs.
3. Handler: `live_by_name(op.node)`. If that returns `None`, raise `NodeNotFound`.
4. Handler: no more graph rules. Every live node can be edited.

## Writes (all inside `apply_op`'s transaction)

1. `before = {k: getattr(node, k) for k in op.set}`, with `tags` as a list. It's serialised as compact JSON with sorted keys (`separators=(",",":")`, `ensure_ascii=False`), for example `{"owner":"o","tags":["gold"]}`.
2. `ctx.store.nodes.update_fields(node.id, dict(op.set))`. This happens even when the values are the same.
3. `ctx.store.log.record(LoggedOp(...))` with:

| column | value |
|---|---|
| `op_id`, `actor_kind`, `actor_name`, `op` | from the op; `op = 'edit_node'` |
| `effect` | `'applied'` (always) |
| `node_id` | the resolved live id |
| `edge_id` | `NULL` |
| `before` | the JSON above: previous values of exactly the keys in `set` |
| `payload` | `canonical(op.raw)`, the line as received (tags **not** normalised) |

4. Return `OpResult(op_id, "applied", node_id=node.id)`.

The op writes no `removals` rows and sets no `deleted_by`.

## Errors

`str(e) == f"{op_id} {code}: {message}"`. `<qn>` is shown with `repr()` (single quotes, escapes).

| class | code | where | message template |
|---|---|---|---|
| `ActorNotAllowed` | `actor_not_allowed` | `parse_op` | `edit_node is human only; actor kind is 'ingest'` |
| `MalformedOp` | `malformed` | `parse_op` | `set must be a non-empty object` / `set has unknown key 'colour'` / `set.owner must be a string` / `set.description must be a string` / `set.tags must be a list of strings` |
| `NodeNotFound` | `node_not_found` | handler | `node '<qn>' is not a live node` (for example `op-0042 node_not_found: node 'conn/db/x' is not a live node`) |
| `OpIdConflict` | `op_id_conflict` | `apply_op` | foundation's text |

The `MalformedOp` texts are the ones this spec proposes. The foundation owns `parse_op` and can change them, but each of these cases must keep a message of its own.

## Repository methods used

- `NodeRepo.live_by_name(qn) -> Node | None`
- `NodeRepo.update_fields(node_id, fields) -> None`
- `OpLogRepo.record(entry: LoggedOp) -> None`

No new methods.

## What later undo will use from the log (undo is out of scope)

- `ops` rows with `op = 'edit_node'` and `seq` greater than the delete's `seq`. Their `node_id` and the keys of `before` say which fields of which node id a human set after the delete. That's the set invariant 1 protects.
- Rows with `node_id` in the delete's `removals` and `seq` lower than the delete: these edits are already in the dead row's values, so undo restores the dead row exactly as it is.
- `before` lets the checker rebuild a node's authored fields at any `seq`, without depending on undo code.

## Out of scope

Undo and redo of edits, editing `qualified_name` or `type`, moving nodes (that's `delete_edge` plus `add_edge`), ingest edits, partial tag operations (add or remove one tag), and validating tag vocabulary.

## Foundation changes requested

1. **A shared `NodeNotFound` message.** Give it a constructor `NodeNotFound(op_id, field, qn)` with message `f"{field} {qn!r} is not a live node"`, so `node`, `parent`, `from` and `to` read the same way in all five ops.
2. **Export `canonical(obj) -> str`** (compact, sorted keys, `ensure_ascii=False`) from `domain`. The handler uses it for `before`, and the registry uses it for `payload`.
3. **State the `update_fields` contract:** it takes domain values (`tags` as a tuple or list), stores `tags` as compact JSON, and refuses keys outside `{description, owner, tags}` with `ValueError`, which is a bug and not an `OpError`.
4. **State that `parse_op` guarantees** that `EditNode.set` is non-empty, has only known keys, and holds normalised `tags` as a tuple, so the handler doesn't check these again.
