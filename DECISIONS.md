# Decisions: undo of a subtree delete

`undo <op_id>` reverses one `delete_node` (or `delete_edge`) against the graph **as it is now**. It doesn't rewind other ops. Every removed node and edge is reported once, as `restored`, `merged` or `skipped`. Full rules: [docs/api/undo.md](docs/api/undo.md). Spec: `docs/superpowers/specs/2026-11-19-undo-design.md`.

## Conflict rules

**Independent edits** are changes after the delete to nodes and edges the delete didn't remove: edits to the parent, siblings and boundary tables, re-pointed references, moved tables. **Rule: untouched.**
- Undo writes only rows with `deleted_by = <this op>`, which no later op can have edited.
- Its only other write is a new `contains` edge *into* a restored node.
- `effective_tags` is a view, so restored nodes inherit their ancestors' *current* tags, including tags added or removed after the delete.

**Caused edits** are ingest runs that recreated deleted names under new ids, because the crawler saw the asset again. **Rule: the recreated node wins its name, and the original's children rejoin it.**
- The original is `skipped` (`name_held`). The recreated node keeps its id and every field.
- An original child with a free name is restored **under the recreated node** (rule 2c), keeping its own id and fields. The new `contains` edge is reported as `merged`.
- This rule applies again at each level of the tree.

**Conflicting edits** are human changes after the delete that collide with what undo would bring back. **Rule: the human's change stands, and undo explains what it gave up.**
- A human claimed a deleted name, or edited a recreated node: the original is skipped (`name_held`, "created by op-… (human)"), and the human's node is not touched.
- An endpoint was deleted later: the edge is skipped (`endpoint_deleted`, naming that op).
- A human pointed a reference at the recreated node: that edge stays. The original reference is skipped, because references are never redirected.
- A child was moved after a `delete_edge contains`: skipped (`child_has_parent`).
- The holder of a parent's name has a different type: the child is skipped (`parent_type_mismatch`), along with its descendants (`no_live_parent`).

**Once per op.** A second `undo` of the same op is refused with `already_undone` and writes nothing. That is how invariant 6 holds. The `undos.op_id` unique constraint enforces it.

## What the model proposed that I rejected

- **Field-level merge into the recreated node** (copy the original's description, owner and tags into blank fields, then move children and edges onto it). Rejected because it writes to rows the delete never removed. Invariant 1 then depends on getting "did a human set this field?" exactly right, and undo of an undo gets harder.
- **Strict "skip if the name is held", with children needing their original parent.** Rejected after checking it against the fixture: ingest recreates the root's own name, so the root is skipped, everything beneath it cascades to skipped, and `undo op-0001` restores nothing. Rule 2c, which reattaches children under the holder of their parent's name, fixes this without editing the holder.
- **Refusing the whole undo when any item conflicts.** Rejected: only the conflicting node is skipped, and the rest goes ahead.
- **Logging undo in the `ops` table.** Rejected: undo gets its own `undos` and `undo_items` tables. That way the `ops` CHECK constraints stay unchanged and no fields are added to nodes or edges.

## What I verified myself

*To be filled in by the author.*

- [ ] 
- [ ] 
- [ ] 
