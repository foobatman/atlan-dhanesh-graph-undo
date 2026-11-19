# `undo`: design (conflict rules, undo log, report, docs)

**Status:** draft, awaiting approval
**Brief:** `problem.md`: "undo <op_id>", invariants 1–6.
**Builds on (binding):** `2026-11-19-apply-foundation-design.md`: layers, `OpError`, repositories, the `ops` and `removals` tables, migrations, and the readiness checklist. Also `2026-11-19-op-delete-node-design.md` ("What undo gets from this record") and `2026-11-19-ops-fixture-design.md` (cases E1–E7 and states a–d).

## Intent

`uv run undo <op_id> [--db graph.db]` reverses one delete op that `apply` recorded. Undo works on the graph as it is now and never rewinds it. Each node and edge the delete removed is either **restored** (its original row is made live again), **merged** (a `contains` edge that is recreated under a different parent), or **skipped**, with a reason. Undo prints the outcome of every item as a JSONL report, one line per removed node or edge. An op can be undone **once**. The undo and its report are kept, and a second attempt is refused with a clear message.

## Goals

- Invariants 1–5 hold after any undo. Invariant 6 holds because a second undo is refused and writes nothing.
- Every outcome is explained: the reason names the node, edge or op that caused it.
- One call contract for Python and the CLI, documented per operation in `docs/api/`.
- `DECISIONS.md` (the deliverable required by problem.md) states the conflict rules.

## Non-goals

- **`CRASH_AFTER` and resumability (invariant 7):** the user's decision is to handle these in a separate spec. Here an undo is one transaction, so it either commits completely or not at all.
- **Redo and undo of an undo.** The README's "Known limitation" section stays, updated to mention the new undo log.
- **`dump` and the checker:** each gets its own spec. Tests here read the `live_*` views directly.
- **Undo of `add_node`, `edit_node` and `add_edge`.** Only delete ops can be undone.

## Anti-goals

- Undo writing any column of a row that wasn't in the removed set. The one write near existing rows is inserting new `contains` edges (see the parent rule).
- Copying fields into or out of a live node, i.e. field merging (approach A, rejected).
- An item that is missing from the report, or listed twice.
- A second undo of the same op changing anything.

## Approaches considered (the name-conflict rule)

| | Rule | Trade-off |
|---|---|---|
| A | Merge X into the live node Y that holds its name: copy X's fields wherever Y's are still blank, then move X's children and edges onto Y | Restores the most, but writes to Y, a row the delete never removed, which puts invariant 1 at risk. **Rejected by the user.** |
| B | Skip X, and Y wins. Children need X's original parent to be live. | Simplest, but with seed 42 R's name is recreated, so R is skipped and everything under R cascades to skipped. op-0001 would restore nothing. |
| **B2 (chosen)** | Skip X when its name is held, as in B. Children whose names are free reattach under whichever node now holds their parent's name. | Nodes keep their own ids and fields, and nothing is written to Y. Most of D comes back. The cost is new `contains` edges from nodes the delete didn't remove. |
| C | Merge if ingest created Y, skip if a human did | Needs two rules. Not chosen. |

Other choices the user made: skip only the conflicting node and continue with the rest (no whole-undo refusal); one undo per op, tracked, with a second attempt refused; crash handling deferred.

## Semantics: undo of `delete_node` X

Let N = `removed_nodes(X)`, E = `removed_edges(X)`, and R = the op's root (`ops.node_id`). Every row in N and E still has `deleted_by = X` and its fields as they were at delete time (foundation readiness checklist). The plan is computed in memory from bulk reads, then written.

### Phase 1: nodes, top-down

**Order:** walk the `contains` edges in E as a tree, starting at R. Parents come before children, and siblings go in `qualified_name` order.

For each node n, the first rule that matches decides its outcome:

1. **Name rule.** If a live node h holds `n.qualified_name`, n is **skipped**, `name_held`: "name held by `<h.id>`, created by `<op_id>` (`<actor_kind>`)". The creator comes from the `ops` row with `op = 'add_node'` and `node_id = h.id`. If h is a seed node (no such row), the text says "seed". This case can't happen after one delete, but it's handled. n is **held by** h.
2. **Parent rule** (n's name is free). Let p be n's original parent, i.e. `from_id` of the `contains` edge in E whose `to_id` is n. Check these in order:
   - **a. Root with no parent:** n is R and E has no `contains` edge into R. n is **restored** as a root.
   - **b. Original parent is live:** p is live now (outside N and never deleted, or restored earlier in this undo). n is **restored** under p, and its original edge is restored in phase 2.
   - **c. Holder of the parent's name:** p was **held by** h (rule 1), or p is dead outside N and a live node h holds p's name. If `h.type == p.type`, n is **restored** under h, and phase 2 creates a new `contains` edge h → n.
   - **d. Holder has a different type:** a holder exists but its type differs. n is **skipped**, `parent_type_mismatch`: "parent `<p.qn>` is held by `<h.id>` of type `<h.type>`, not `<p.type>`".
   - **e. No parent:** n is **skipped**, `no_live_parent`: "parent `<p.qn>` was not restored (`<p's reason code>`)", or "… was deleted by `<op>`" if p is outside N.

A skipped node's children are still checked one by one. Rule 2c means the children of a `name_held` node move to its holder, so the cascade that skips whole subtrees is 2d/2e.

Nodes are never **merged**. A restored node keeps its own id, description, owner and tags.

### Phase 2: edges

Each edge e in E is decided once phase 1 is complete:

| edge | condition | outcome |
|---|---|---|
| `contains` p → c | c restored under p (2b) | **restored** |
| `contains` p → c | c restored under holder h (2c) | **merged**, `reparented`: "recreated as `<h.id>` → `<c.id>`". The report carries `new_edge_id`. |
| `contains` p → c | c skipped | **skipped**, `endpoint_not_restored`: "child `<c.qn>` was skipped (`<code>`)" |
| `references` a → b | both endpoints live after phase 1, and no live `(a, b, references)` edge | **restored** |
| `references` a → b | an endpoint in N was skipped | **skipped**, `endpoint_not_restored`: "endpoint `<qn>` was skipped (`<code>`)" |
| `references` a → b | an endpoint outside N is dead | **skipped**, `endpoint_deleted`: "endpoint `<qn>` was deleted by `<op_id>`" |
| `references` a → b | a live duplicate exists | **skipped**, `duplicate_edge`. This can't happen after a `delete_node`, but it's handled for `delete_edge`. |

References are never redirected to the node that holds a name. E1's human edge X → T′ stays as it is, and the original X → T is skipped.

### Phase 3: writes (one transaction, owned by the caller)

1. `nodes.revive(restored_node_ids, X)`: `deleted_by = NULL` where `deleted_by = X`. Asserts the row count.
2. `edges.revive(restored_edge_ids, X)`, the same way.
3. `edges.insert(h, c, 'contains')` for each merged edge. The new ids go into the report.
4. `undo_log.record(X, report)` and `undo_log.add_items(...)`.

Nodes are written before edges, so no statement leaves a live edge with a dead endpoint. The partial unique indexes (`live_name`, `live_edge`, `live_parent`) are the last line of defence. An `IntegrityError` there is a bug, and it rolls everything back.

### Undo of `delete_edge` X

E holds one edge e, and N is empty.
- If both endpoints are live and no live `(from, to, type)` edge exists: **restored**.
- For `contains`, if the child already has a live parent (the generator always re-parents after a `delete_edge contains`): **skipped**, `child_has_parent`: "`<c.qn>` now has parent `<qn>` (moved by `<op_id>`)".
- Otherwise `endpoint_deleted` or `duplicate_edge`, as in the table above.

### Why the invariants hold

1. Undo un-flags only rows with `deleted_by = X`, which no op could have edited, and inserts new `contains` edges only into restored children. No live row's fields change, and no live edge is removed. Every human change made after X survives unchanged.
2. Under the chosen reading, "parent is live" means the original parent is live, was restored in this undo, or the parent's name is held by a live node of the same type. Every node with a free name and such a parent is restored, by rule 2b/2c. Its fields are untouched because the row is the original.
3. The name rule is checked against the live state plus the nodes restored earlier in this undo. A name occurs only once in N (it was unique among live nodes at delete time), and the `live_name` index enforces it.
4. The report is built from N and E, one entry each. A test compares the id sets.
5. `effective_tags` is a view over the live graph, so it's correct by construction once the tree is right.
6. A second undo is refused before any write.

## Errors (`domain/errors.py`, new `OpError` subclasses)

`str(e) == f"{op_id} {code}: {message}"`, the same shape as apply.

| class | code | when | message |
|---|---|---|---|
| `OpNotFound` | `op_not_found` | `op_id` is not in `ops` | `no applied op with id '{op_id}'` |
| `NotUndoable` | `not_undoable` | op is not `delete_node`/`delete_edge`, or it was a `noop` | `{op} cannot be undone; only delete_node and delete_edge can` |
| `AlreadyUndone` | `already_undone` | `undos` has a row for `op_id` | `already undone (undo #{undo_seq}, after {last_op_id}); restored {r} nodes, {re} edges, merged {m}, skipped {s}. Undo runs once per op.` |

Malformed `op_id` input (must match `^op-\d{4,}$`) raises `MalformedOp`.

## Undo log (migration `0003_undo.sql`)

```sql
CREATE TABLE undos (
  undo_seq    INTEGER PRIMARY KEY,
  op_id       TEXT NOT NULL UNIQUE REFERENCES ops(op_id),   -- UNIQUE enforces "once per op"
  after_seq   INTEGER NOT NULL,     -- max(ops.seq) when the undo ran: "undone after op-0309"
  report      TEXT NOT NULL         -- the exact JSONL printed
);
CREATE TABLE undo_items (
  op_id       TEXT NOT NULL REFERENCES undos(op_id),
  kind        TEXT NOT NULL CHECK (kind IN ('node','edge')),
  item_id     TEXT NOT NULL,        -- node id, or edge id as text
  outcome     TEXT NOT NULL CHECK (outcome IN ('restored','merged','skipped')),
  reason_code TEXT,
  new_edge_id INTEGER REFERENCES edges(id),   -- merged contains edges only
  PRIMARY KEY (op_id, kind, item_id)
);
```

- Undo gets its own tables instead of rows in `ops`: no rebuild of `ops`'s `CHECK` constraints, and no new fields on nodes or edges (CLAUDE.md).
- The `ops.seq` ordering that apply relies on stays as it is. A later `apply` still works after an undo.
- The undo log stores no wall-clock time, so separate runs stay byte-identical.
- `undo_items.new_edge_id` is the record a future redo needs.

## Report (stdout, JSONL: one line per removed item)

**User's decision:** the report is JSONL. Each line is a JSON object for exactly one node or edge the original op removed. That matches problem.md's "for every node and edge the original operation removed", and it makes invariant 4 a line count.

```jsonl
{"kind":"node","id":"…","op_id":"op-0001","outcome":"skipped","qualified_name":"pg/ops/core","reason":"name held by 3f2… created by op-0142 (ingest)","reason_code":"name_held"}
{"kind":"node","id":"…","op_id":"op-0001","outcome":"restored","parent_id":"3f2…","qualified_name":"pg/ops/core/users"}
{"kind":"edge","id":1203,"from_id":"…","new_edge_id":2950,"op_id":"op-0001","outcome":"merged","reason":"recreated as 3f2… → 9ab…","reason_code":"reparented","to_id":"…","type":"contains"}
```

- **Order:** all node lines come first, sorted by `(qualified_name, id)`. Edge lines follow, sorted by `id`.
- **Serialisation:** each line uses `canonical()` (`sort_keys`, compact separators), followed by `\n`.
- **No summary line:** the counts go to stderr in the summary line (CLI contract), so every stdout line is one item.
- **`reason_code` and `reason`** appear on `merged` and `skipped` items only.
- **`parent_id`** appears on restored nodes: the node they now hang under, or `null` for a root.
- **`new_edge_id`** appears on merged edges only.
- **What `undos.report` stores:** the exact bytes printed.

## Code layout (follows the foundation's layers)

| file | what |
|---|---|
| `db/migrations/0003_undo.sql` | the two tables |
| `db/undolog.py` | `UndoLogRepo`: `get(op_id) -> UndoRecord \| None`, `record(op_id, after_seq, report_json)`, `add_items(op_id, items)` |
| `db/nodes.py` | add `by_ids(ids) -> list[Node]`, `live_by_names(names) -> dict[str, Node]`, `revive(ids, op_id) -> int` |
| `db/edges.py` | add `by_ids(ids) -> list[Edge]`, `revive(ids, op_id) -> int`, `live_exists(triples) -> set` |
| `db/oplog.py` | add `creators(node_ids) -> dict[node_id, (op_id, actor_kind)]`, `last_seq() -> (seq, op_id)`. Also `deleter(op_id)`, if needed for reasons. |
| `db/store.py` | `Store.undo_log` |
| `domain/undo.py` | pure planner: `plan_undo(inputs) -> UndoPlan` (dataclasses `NodeOutcome`, `EdgeOutcome`, `UndoPlan`), no I/O. Gives the rules their own unit tests. |
| `domain/errors.py` | `OpNotFound`, `NotUndoable`, `AlreadyUndone` |
| `api/undo.py` | `undo_op(store, op_id) -> UndoReport`: checks, bulk reads, `plan_undo`, writes, all in one `transaction` |
| `cli/undo.py` | `undo_main`. `pyproject`: `undo = "graph_undo.cli.undo:main"` |

**Bulk reads, set-based:** id lists go in as `json_each(?)`, as in apply, so a constant number of statements runs per undo. Target: undo of op-0001 on seed 42 in under 1 s.

## CLI contract

- `uv run undo <op_id> [--db graph.db]`
- **Success:** prints the JSONL report to stdout and exits 0. Prints `undid op-0001 on graph.db: nodes restored=… skipped=…; edges restored=… merged=… skipped=…` to stderr.
- **`OpError`** (`op_not_found`, `not_undoable`, `already_undone`, `malformed`): prints `str(e)` to stderr and exits 1. Nothing is written.
- **Missing db, pending migrations or a newer db:** exits 1 with the same messages as apply.

## Documentation deliverables (user's request)

- **`DECISIONS.md`** (repo root, one page, required by problem.md):
  - the conflict rule for **independent**, **conflicting** and **caused** edits, mapped to the rules above;
  - **what the model proposed that was rejected**: approach A (field merge), and B as stated (it restores nothing on this fixture);
  - **what was verified**, as a list for the user to fill in. Claude doesn't claim this section.
- **`docs/api/`**: the API contract, one file per operation, so a reader can call any single op:
  - `README.md`: index, the layers, how to open a `Store`, the error format, the exit codes.
  - `load.md`, `migrate.md`, `apply.md` (file-level `apply_file`, and `apply_op` for a single op).
  - `add_node.md`, `edit_node.md`, `delete_node.md`, `add_edge.md`, `delete_edge.md`: the JSON op shape per actor, a Python call (`apply_op(store, parse_op({...}))`), the effect, the errors with their exact messages, and what the op log records.
  - `undo.md`: CLI, `undo_op`, the rules in short, the report schema, the errors.
- **`README.md`:** status table, an `undo` command section, the layout, a link to `docs/api/` and `DECISIONS.md`, and the known limitation updated.

## Testing

- **Planner unit tests** (`tests/undo/test_planner.py`, pure, no db): each rule 1–2e and each edge row of the table. Also the three-level cascade from the conversation: `core` held, `orders` held, `total` restored under `orders′`, `id` held. Plus a type mismatch skipping a whole subtree.
- **API tests** (`tests/undo/test_undo.py`, small hand-written seed, ops through `apply_op`):
  - restore with no conflicts;
  - the cases E1, E3/E4, E5, E6, E7;
  - root with no parent;
  - `delete_edge` undo, including `child_has_parent`;
  - the three errors;
  - a second undo is refused and leaves the db byte-identical;
  - a failure partway through writes nothing.
- **Invariant tests on seed 42** (`tests/undo/test_undo_e2e.py`): load, apply, undo op-0001, then check:
  - **1:** every human-edited row and every human-added live edge is unchanged. The before and after snapshots of live rows differ only by additions.
  - **2:** every node in N with a free name and an eligible parent is restored with its seed fields.
  - **3:** names are unique (the index, plus an explicit query).
  - **4:** the report's id sets equal N and E, with no duplicates.
  - **5:** `effective_tags` is checked against a Python recomputation.
  - **6:** a second undo is refused, and the live state is unchanged.
  - The whole scenario runs in under 5 s.

  These tests cover undo itself. The independent checker required by problem.md is a separate spec.

## Left to the builder

- internal helper structure;
- exact wording of the stderr summary line;
- how the planner is fed: one dataclass of inputs, or several arguments.

## Calls made by Claude (flag any to change)

1. A holder of the parent's name must have the **same type** as the original parent (2c/2d).
2. Undo of a `noop` op, or of a non-delete op, is refused with `not_undoable`.
3. `delete_edge` undo is supported and follows the same edge rules.
4. `AlreadyUndone` exits 1, and its message repeats the original summary counts.
5. Undo has its own tables and does not log into `ops`.
6. Sort order of the report lines, and no summary line on stdout.
7. `DECISIONS.md` "what I verified" is left for the user to fill in.
