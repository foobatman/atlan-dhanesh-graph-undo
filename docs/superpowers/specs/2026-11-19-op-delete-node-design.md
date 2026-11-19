# `apply`: `delete_node` (cascade delete-subtree)

**Status:** draft
**Brief:** `problem.md`, "Cascade" and invariants 2 and 4.
**Builds on (binding):** `2026-11-19-apply-foundation-design.md` (layout, `OpError`s, repositories, `OpContext`/`OpResult`, `ops` + `removals`, transactions); `2026-11-19-ops-fixture-design.md` ("Operation contract", op-0001, E5, E6); `2026-11-19-load-design.md` (flag-in-place).

## Intent

`delete_node` removes a node, every descendant along live `contains` edges and every live edge touching any of them. Nothing is physically deleted: rows are flagged with `deleted_by = op_id`. The op also records **exactly** what it flagged in `removals`. The later undo spec reverses that record and nothing else, so the record must be exact: no row missing, no row extra, no row flagged by an earlier op.

## Semantics

- **Actor:** human only. Ingest `delete_node` is rejected in `parse_op` with `ActorNotAllowed`; the handler never sees it.
- **Resolve:** `op.node` is a qualified name resolved against live nodes (`NodeRepo.live_by_name`). That node is the root.
- **Removed nodes (N):** the root plus every node reachable from it along **live** `contains` edges whose child is live. Dead `contains` edges are never walked.
- **Removed edges (E):** every **live** edge, of either type, with `from_id ∈ N` or `to_id ∈ N`. This includes the root's own parent `contains` edge, the internal `contains` edges, and references inbound to, outbound from, and internal to the subtree.
- **Bookkeeping (one transaction, owned by `apply_op`):**
  1. `edges.mark_deleted(E, op_id)`: every row in E gets `deleted_by = op_id`.
  2. `nodes.mark_deleted(N, op_id)`: every row in N gets `deleted_by = op_id`.
  3. `log.record(...)`: the op-log row, with `op = 'delete_node'`, `effect = 'applied'`, `node_id = root id`, `edge_id = NULL`, `before = NULL`, `payload = canonical(op.raw)`.
  4. `log.add_removals(op_id, N, E)`: one `removed_nodes` row per node in N and one `removed_edges` row per edge in E.

  Edges go before nodes, so no statement leaves a live edge pointing at a dead node. The log row goes before `removals` because `removals.op_id` references `ops(op_id)` and foreign keys are on.
- **Rows already dead are untouched.** A dead row is never in N or E (both are computed from live rows only), and `mark_deleted` only updates `deleted_by IS NULL` rows. So a reference removed earlier by `delete_edge`, the old `contains` edge of a child moved out, and the dead rows of an earlier incarnation of the same name all keep their original `deleted_by`. They get no `removals` row from this op.
- **Exactness invariant** (holds for every `delete_node` at commit, tested directly):
  - every `removals` row of this op names a row whose `deleted_by = op_id`;
  - every row whose `deleted_by = op_id` has a `removals` row of this op;
  - `|removed_nodes(op_id)| = OpResult.removed_nodes = |N|`, and `|removed_edges(op_id)| = removed_edges = |E|`.
- **Graph invariant kept:** after the op, every live edge has two live endpoints, and live `contains` is still a forest.
- **Names freed:** every name in N is free for reuse at once (the `live_name` index is partial). A later `add_node` of the same name mints a new id. A later `delete_node` of that name resolves to the new id and flags only new rows.
- **`effective_tags`:** survivors are recomputed by the view, never stored. A survivor's ancestors are all survivors, so its value is unchanged; removed nodes drop out of the view.
- **Result:** `OpResult(op_id, "applied", node_id=root_id, edge_id=None, removed_nodes=|N|, removed_edges=|E|)`. `delete_node` has no `noop` case.

### Cases addressed

| Case | Outcome |
|---|---|
| Leaf (column) delete | N = {leaf}; E = its parent `contains` edge plus any live references touching it. |
| Root connection delete | N = the whole tree under it; E = every live edge touching it (references to other trees included). |
| Node moved **in** before the delete | Its new live `contains` edge is inside the subtree, so it and its descendants are in N. Its old dead `contains` edge keeps its old `deleted_by`. |
| Node moved **out** before the delete | Reached only via a dead edge, so it survives. The dead edge is not re-flagged. |
| Reference already removed by `delete_edge` | Dead, so not in E; keeps its `deleted_by`. |
| Boundary table deleted after the big delete (E5) | Its references into D are already dead (`op-0001`) and stay so; only its live rows are flagged. |
| Recreated node deleted (E6) | Name resolves to the new id; only new rows are flagged. The original rows keep `deleted_by = op-0001` and stay in `removals(op-0001)` only. |
| Same name deleted twice | Second op raises `NodeNotFound`. |

### Performance

op-0001 on seed 42 removes 614 nodes and 774 edges. The handler issues a constant number of set-based statements, independent of subtree size:
- `live_subtree_ids`: one recursive CTE over `live_edges` (type `contains`) joined to `live_nodes`, `UNION` (not `UNION ALL`).
- `live_touching`: one `SELECT` with `from_id IN (…) OR to_id IN (…)`.
- `mark_deleted` (×2), `add_removals`: one statement each per kind.

Id lists are passed as **one** JSON parameter and expanded with `json_each(?)`, so there is no per-row query and no bound-variable limit. Target: the handler call for op-0001 takes under 1 s (expected: milliseconds).

## Validation order

1. `json.loads`, else `MalformedOp` (in `apply_file`).
2. `parse_op`: exact key set `{op_id, actor, op, node}`, `node` a string, valid `op_id`, else `MalformedOp`. Ingest actor → `ActorNotAllowed`.
3. `apply_op`: `op_id` already logged → skipped, or `OpIdConflict` if the payload differs.
4. Handler: `nodes.live_by_name(op.node)` is `None` → `NodeNotFound`.

There is no other graph check: any live node may be deleted, at any level.

## Errors

`str(e) == f"{op_id} {code}: {message}"`. A rejected op writes nothing: no flags, no `removals`, no op-log row.

| class | code | when | message template |
|---|---|---|---|
| `MalformedOp` | `malformed` | shape errors (foundation `parse_op`) | foundation's text |
| `ActorNotAllowed` | `actor_not_allowed` | ingest `delete_node` (foundation `parse_op`) | foundation's text |
| `NodeNotFound` | `node_not_found` | `node` not live (never existed, already deleted, or removed by an ancestor's delete) | `node '{qn}' is not a live node` |
| `OpIdConflict` | `op_id_conflict` | foundation `apply_op` | foundation's text |

Example: `op-0002 node_not_found: node 'conn/db/sc' is not a live node`.

## Repository methods used

All from the foundation:
- `NodeRepo.live_by_name`, `NodeRepo.live_subtree_ids`, `NodeRepo.mark_deleted`;
- `EdgeRepo.live_touching`, `EdgeRepo.mark_deleted`;
- `OpLogRepo.record`, `OpLogRepo.add_removals` (and `removals`, `get` in tests).

### Foundation changes requested

1. **`mark_deleted` returns the number of rows it updated** (`-> int`, both repos). The handler asserts it equals `len(ids)`; a mismatch is a bug (`AssertionError`, rolled back), not a user error. It is the cheapest guard that flagged set == listed set.
2. **Implementation constraint** on `live_touching`, `mark_deleted` and `add_removals`: id lists go in as one `json_each(?)` parameter; one statement per call (per kind for `add_removals`). No signature change.
3. **Shared message template** for `NodeNotFound`: `"{field} '{qn}' is not a live node"` with `field` ∈ {`node`, `parent`, `from`, `to`}, matching the foundation plan's `test_error_str` example. Optionally a helper `api/resolve.py: require_live(ctx, field, qn) -> Node` so all five ops produce identical text.

## What undo gets from this record (not implemented here)

- `ops` row: `op = 'delete_node'`, `node_id` = the root id, apply order (`seq`), actor.
- `removals(op_id)`: exactly N and E, by id. Each row is still in place with authored fields frozen: dead rows are never edited (every op resolves live names; `mark_deleted` skips dead rows).
- The root's original parent: the edge in E with `type = 'contains'` and `to_id = root id`. Its `from_id` is P.
- Boundary references: edges in E with exactly one endpoint in N.
- Order for top-down restore: the `contains` edges in E.
- Caveat for the undo spec: once undo un-flags rows, a later delete may re-flag them with a new op_id. `removals` of the old op still lists them. The exactness invariant above is stated at apply time; undo defines what holds afterwards.

## Out of scope

- Undo, redo, the report, conflict rules, `CRASH_AFTER`.
- Ingest deletes, deletes by id, partial (non-cascading) deletes.
- Re-parenting orphans: the cascade removes every descendant, by definition.
