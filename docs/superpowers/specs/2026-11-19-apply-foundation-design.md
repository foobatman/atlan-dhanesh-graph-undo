# `apply`: foundation design (layers, op log, errors, dispatch)

**Status:** draft (per-op specs written; shared conventions folded in)
**Brief:** `problem.md`, "apply ops.jsonl. Apply operations in order. Each has an op_id and an actor. Record enough that any delete-subtree can be undone later."
**Builds on:**
- `2026-11-19-load-design.md`: the flag-in-place schema, `deleted_by`, live views.
- `2026-11-19-ops-fixture-design.md`, "Operation contract": the five ops, their fields and semantics.

**Per-operation specs** (one each, written against this document):
- `2026-11-19-op-add-node-design.md`
- `2026-11-19-op-edit-node-design.md`
- `2026-11-19-op-delete-node-design.md`
- `2026-11-19-op-add-edge-design.md`
- `2026-11-19-op-delete-edge-design.md`

## Intent

`uv run apply fixtures/ops.jsonl [--db graph.db]` reads ops one by one and calls the handler for each op kind. Every handler lives in an API layer that knows the graph rules. The API layer talks to the database only through a database layer, and that layer is the only place SQL exists.

Every applied op is recorded in an op log. Every delete also records what it removed, in a removal log. Together they give the later undo spec everything it needs.

## Goals

- **Layered code:** a modular package with a strict one-way dependency: `cli → api → db`, with `domain` shared by `api` and `db`.
- **Dispatch:** one handler per op kind, chosen through a registry. `apply` never branches on op kind itself.
- **Typed errors:** every invalid op raises an `OpError` subclass with a stable `code`, the `op_id` and a message. The API layer produces the error text once, so every interface prints the same message.
- **Delete bookkeeping:** `delete_node` and `delete_edge` set `deleted_by`, write `removals` rows and write their op-log row, all in the op's own transaction.
- **Determinism:** the same seed and ops give the same database every time, ids included.

## Non-goals

- Undo, redo, and the undo conflict rules in DECISIONS.md. These are later specs.
- `CRASH_AFTER` and crash resumability. These belong to the undo spec. Per-op transactions here are atomic, but nothing is promised about crash timing.
- `dump` and the checker.

## Anti-goals

- SQL outside `graph_undo/db/`.
- An API handler that reads or writes base tables without going through a repository.
- An op that half-applies. Every op either commits completely or not at all.
- An invalid op that is skipped silently. Every rejection is printed.
- An error message that differs depending on how the op was submitted (file or a single op).

## Approaches considered (invalid-op policy)

| | Policy | Trade-off |
|---|---|---|
| A | Stop at the first invalid op, keeping earlier ones | One bad line blocks the whole rest of the file |
| B | Whole file in one transaction | One typo loses about 300 ops of work |
| **C (chosen)** | Print the error, skip the invalid op, and continue. Each op runs in its own transaction. | Nothing is lost and every rejection is visible. Dependent ops may also fail: if a re-point's `delete_edge` is rejected, its `add_edge` usually fails with `already_has_parent` or `duplicate_edge`, and that is printed too. |

**C is the user's decision:** "print the error and skip that operation. The same error that users get when applying individual operations through the CLI." A rejected op writes nothing: no graph rows and no op-log row. Its transaction is rolled back.

## Package layout

```
src/graph_undo/
  domain/                 pure Python: no sqlite3 import, no I/O
    model.py              Actor, Node, Edge dataclasses; NODE_TYPES, EDGE_TYPES, LEVEL; normalise_tags()
    ops.py                typed op dataclasses + parse_op(dict) -> Op
    errors.py             OpError hierarchy (below)
  db/                     the only package that contains SQL
    schema.sql            (moved from src/graph_undo/schema.sql, extended with ops + removals)
    connection.py         connect(path); init_schema(con); transaction(con) context manager
    nodes.py              NodeRepo
    edges.py              EdgeRepo
    oplog.py              OpLogRepo (ops + removals tables)
    store.py              Store: one connection plus .nodes, .edges, .log; ids.mint_node_id()
  api/                    graph rules; one module per op
    context.py            OpContext (store + op) passed to handlers
    add_node.py           handle(ctx, op: AddNode) -> OpResult
    edit_node.py          handle(ctx, op: EditNode) -> OpResult
    delete_node.py        handle(ctx, op: DeleteNode) -> OpResult
    add_edge.py           handle(ctx, op: AddEdge) -> OpResult
    delete_edge.py        handle(ctx, op: DeleteEdge) -> OpResult
    registry.py           HANDLERS: dict[str, Handler]; apply_op(store, op) -> OpResult
    apply.py              apply_file(ops_path, db_path) -> ApplySummary
    load.py               load_seed(seed_path, db_path)  (moved from store.py; behaviour unchanged)
  cli/
    load.py               load_main   (moved from cli.py)
    apply.py              apply_main
  fixture/                unchanged
```

**Dependency rules.**
- `domain` imports nothing from the project.
- `db` imports `domain`.
- `api` imports `domain` and `db`.
- `cli` imports `api` and `domain.errors`.

A test (`tests/test_layers.py`) enforces these rules by walking each module's AST for imports. It also checks that the string `sqlite3` and SQL keywords in string literals appear only under `db/`.

**Moves.**
- `graph_undo/store.py` splits into `db/connection.py` (connect, schema), `db/store.py` (bulk seed insert, `Store.bulk_load(nodes, edges)`) and `api/load.py` (parsing, validation, the atomic tmp-file swap).
- `graph_undo/cli.py` becomes `cli/load.py`.
- `pyproject` scripts: `load = "graph_undo.cli.load:main"` and `apply = "graph_undo.cli.apply:main"`.
- Existing load tests keep passing, with only their import lines changed.

## Domain types (`domain/`)

```python
@dataclass(frozen=True)
class Actor:
    kind: Literal["human", "ingest"]
    name: str

@dataclass(frozen=True)
class Node:     # a row as the API sees it; tags already a sorted list
    id: str; qualified_name: str; type: str; description: str; owner: str; tags: tuple[str, ...]
    deleted_by: str | None

@dataclass(frozen=True)
class Edge:
    id: int; from_id: str; to_id: str; type: str; deleted_by: str | None
```

The op dataclasses all share `op_id: str`, `actor: Actor` and `raw: dict`. `raw` is the original JSON line, which goes to the op log.

| class | `op` | extra fields |
|---|---|---|
| `AddNode` | `add_node` | `qualified_name, type, parent, description: str \| None, owner: str \| None, tags: tuple \| None` (None for ingest) |
| `EditNode` | `edit_node` | `node, set: dict` (keys ⊆ {description, owner, tags}, tags normalised) |
| `DeleteNode` | `delete_node` | `node` |
| `AddEdge` | `add_edge` | `from_, to, type` |
| `DeleteEdge` | `delete_edge` | `from_, to, type` |

`parse_op(d: dict) -> Op` checks the **shape** only, and raises `MalformedOp` if it's wrong:
- the exact key set per (op, actor kind), as in the ops-fixture contract;
- field types;
- the enum values;
- an `op_id` matching `^op-\d{4,}$`;
- the actor kind.

Rules that depend on the graph (a name is live, a parent exists) belong to the handlers.

## Errors (`domain/errors.py`)

```python
class OpError(Exception):
    code: ClassVar[str]
    def __init__(self, op_id: str | None, message: str): ...
    # str(e) == f"{op_id} {code}: {message}"
```

| class | `code` | raised when |
|---|---|---|
| `MalformedOp` | `malformed` | Bad JSON, wrong or missing keys, wrong types, unknown `op` or `type`, bad `op_id` |
| `ActorNotAllowed` | `actor_not_allowed` | Ingest sends `edit_node`, `delete_node` or `delete_edge`, or sends authored fields on `add_node`. **Checked in `parse_op`.** |
| `NodeNotFound` | `node_not_found` | A referenced name (`node`, `parent`, `from`, `to`) is not live |
| `NameTaken` | `name_taken` | Human `add_node` of a live name |
| `TypeOrderViolation` | `type_order` | A `contains` edge (or `add_node` parent) is not exactly one level down |
| `SelfLoop` | `self_loop` | `from == to` |
| `DuplicateEdge` | `duplicate_edge` | A live edge with the same `(from, to, type)` already exists |
| `AlreadyHasParent` | `already_has_parent` | `add_edge contains` onto a child with a live parent |
| `EdgeNotFound` | `edge_not_found` | `delete_edge` of an edge that isn't live |
| `OpIdConflict` | `op_id_conflict` | `op_id` is already in the log with a different payload |

Error handling across the layers:
- Repositories never raise `OpError`. They return `None` or empty results, and the handler decides what is an error.
- An unexpected `sqlite3.IntegrityError` inside a handler is a bug, not a user error. It propagates and the transaction rolls back.
- `api/registry.apply_op` does **not** catch it.

## Shared conventions (agreed across the per-op specs)

1. **Message templates** live in `domain/errors.py`, so every op and every interface prints the same text:
   - `NodeNotFound.for_field(op_id, field, qn)` gives `"{field} '{qn}' is not a live node"`, with `field` ∈ {`node`, `parent`, `from`, `to`}. All five handlers resolve names through `api/resolve.py: require_live(ctx, field, qn) -> Node`.
   - `ActorNotAllowed.for_op(op_id, op)` gives `"ingest may not {op}"`. On ingest `add_node`, authored fields give `"ingest may not set {fields}"`.
   - Each per-op spec holds the exact templates for its other errors.
2. **`parse_op` check order:** first JSON object, then `op_id`, then `actor`, then a known `op`, then the actor is allowed for that op (`ActorNotAllowed`, even if the other keys are wrong), then the exact key set, then field types and enums.
   - `parse_op` normalises `tags` (sorted, unique) for both `AddNode` and `EditNode.set`.
   - `EditNode.set` is guaranteed non-empty, with known keys only.
3. **`canonical(d) -> str`** lives in `domain/ops.py`: `json.dumps(d, sort_keys=True, separators=(",",":"), ensure_ascii=False)`. It is used for `ops.payload` (the op exactly as received, tags not normalised), for `ops.before`, and for the `OpIdConflict` comparison.
4. **`LoggedOp`** (`db/oplog.py`) is a frozen dataclass whose fields mirror the `ops` columns, minus `seq`: `op_id, actor_kind, actor_name, op, effect, node_id, edge_id, before, payload`.
5. **Write order inside one op:** graph rows first, then `log.record(...)`, then `log.add_removals(...)`. `removals.op_id` has a foreign key to `ops.op_id`, so the log row must exist first.
6. **`mark_deleted` returns the number of rows it updated.** Delete handlers assert that the number equals the count of ids passed; a mismatch is a bug. Id lists go to SQL as one `json_each(?)` parameter, so each call is a single statement.
7. **`update_fields(node_id, fields)`** takes domain values (`tags` as a tuple, encoded by the repo). It raises `ValueError` on keys outside description/owner/tags; that is a programming error, not an `OpError`.
8. **Per-op tests that need another op's handler** use `pytest.skip` until that handler is in `HANDLERS`. All other setup goes directly through the repositories, so the per-op plans can land in any order.

## Database additions (`db/schema.sql`, `user_version = 2`)

```sql
CREATE TABLE ops (
  seq        INTEGER PRIMARY KEY,             -- apply order, 1..N
  op_id      TEXT NOT NULL UNIQUE,
  actor_kind TEXT NOT NULL CHECK (actor_kind IN ('human','ingest')),
  actor_name TEXT NOT NULL,
  op         TEXT NOT NULL CHECK (op IN ('add_node','edit_node','delete_node','add_edge','delete_edge')),
  effect     TEXT NOT NULL CHECK (effect IN ('applied','noop')),
  node_id    TEXT REFERENCES nodes(id),       -- resolved target / created node (node ops)
  edge_id    INTEGER REFERENCES edges(id),    -- resolved / created edge (edge ops; add_node's contains edge)
  before     TEXT,                            -- JSON: edit_node's previous values of the fields it set
  payload    TEXT NOT NULL                    -- the op line as received, compact sorted JSON
);

CREATE TABLE removals (
  op_id   TEXT NOT NULL REFERENCES ops(op_id),
  kind    TEXT NOT NULL CHECK (kind IN ('node','edge')),
  item_id TEXT NOT NULL,                       -- node id, or edge id as text
  PRIMARY KEY (op_id, kind, item_id)
);
```

- **Who writes `removals`:** only `delete_node` and `delete_edge` write rows. Every row they add has a matching `deleted_by = op_id` on the node or edge, set in the same transaction.
- **Ids are names resolved at apply time.** The log stores ids, never names, because a name can point to different nodes over time.
- **Migration:** `init_schema` creates the full v2 schema, and `load` creates v2 directly. `apply` refuses a database whose `user_version` isn't 2 with exit 1 and "run load first".

## Repositories (`db/`)

Below are the methods every handler may use. A per-op spec may ask for a new method; it gets added here, in the same style. Every method takes and returns domain types. None of them commits; transactions belong to `apply_op`.

```python
class NodeRepo:
    def live_by_name(self, qn: str) -> Node | None
    def by_id(self, node_id: str) -> Node | None             # live or dead
    def insert(self, node: Node) -> None
    def update_fields(self, node_id: str, fields: dict) -> None   # description/owner/tags only
    def live_subtree_ids(self, root_id: str) -> list[str]     # root first, along live contains
    def mark_deleted(self, node_ids: list[str], op_id: str) -> int     # rows updated

class EdgeRepo:
    def live(self, from_id: str, to_id: str, type: str) -> Edge | None
    def live_parent(self, child_id: str) -> Edge | None       # the live contains edge into child
    def live_touching(self, node_ids: list[str]) -> list[Edge]  # either end in node_ids, any type
    def insert(self, from_id: str, to_id: str, type: str) -> Edge
    def mark_deleted(self, edge_ids: list[int], op_id: str) -> int     # rows updated

class OpLogRepo:
    def get(self, op_id: str) -> LoggedOp | None
    def record(self, entry: LoggedOp) -> None                # seq assigned by SQLite
    def add_removals(self, op_id: str, node_ids: list[str], edge_ids: list[int]) -> None
    def removals(self, op_id: str) -> tuple[list[str], list[int]]
```

`mark_deleted` updates only rows that are currently live (`deleted_by IS NULL`) and returns how many it updated. The handler has already worked out which ids to pass.

## Id minting

`Store.mint_node_id(op_id) -> str` returns `str(uuid.uuid5(NAMESPACE, op_id))`, where `NAMESPACE = uuid.UUID("6f1c3a52-0d8e-4c1e-9b7a-2f4d5e6a7b8c")`.

- It's deterministic, so separate fresh runs produce identical dumps.
- One op mints at most one node id.
- If the id somehow collides with an existing id, the `IntegrityError` propagates as a bug.

## Handler contract (`api/`)

```python
Handler = Callable[[OpContext, Op], OpResult]

@dataclass(frozen=True)
class OpContext:
    store: Store
    op: Op

@dataclass(frozen=True)
class OpResult:
    op_id: str
    effect: Literal["applied", "noop", "skipped"]   # "skipped" only from apply_op, never logged
    node_id: str | None = None
    edge_id: int | None = None
    removed_nodes: int = 0
    removed_edges: int = 0
```

A handler does the following, in order:
1. Resolve names to live nodes, raising `NodeNotFound`.
2. Check the graph rules, raising the specific `OpError`.
3. Write through the repositories.
4. Write its own op-log row with `ctx.store.log.record(...)`, and any `removals`.
5. Return an `OpResult`.

The handler writes the op-log row, not the registry, because only the handler knows `node_id`, `edge_id`, `before` and `effect`.

## `apply_op` and `apply_file`

```python
def apply_op(store: Store, op: Op) -> OpResult:
    logged = store.log.get(op.op_id)
    if logged:                                   # resume / re-run
        if logged.payload != canonical(op.raw): raise OpIdConflict(...)
        return OpResult(op.op_id, "skipped")     # → counted as skipped; nothing written
    with transaction(store.con):                 # BEGIN IMMEDIATE … COMMIT, ROLLBACK on any exception
        return HANDLERS[op.kind](OpContext(store, op), op)
```

`OpResult.effect` therefore also allows `"skipped"`. That value is never stored, because skipped ops aren't re-logged.

`apply_file(ops_path, db_path) -> ApplySummary`:
- Streams the file line by line. Line numbers count `\n` only, the same lesson as in load. Blank lines are ignored.
- Each line goes `json.loads`, then `parse_op`, then `apply_op`.
- On an `OpError` (from `json.loads` wrapped as `MalformedOp`, from `parse_op`, or from a handler), it calls `on_reject(line, error)`. It does not commit anything for that op, and continues with the next line.
- `ApplySummary` counts `applied`, `noop`, `skipped` (already in the log) and `rejected`, and keeps the list of `(line, OpError)`.
- **Signature:** `apply_file(ops_path, db_path, on_reject: Callable[[int, OpError], None]) -> ApplySummary`.
- **Re-runs:** rejected ops are not logged, so a re-run tries them again. Given the same state, it prints the same errors.

`cli/apply.py`:
- **Each rejection:** printed to stderr as it happens, as `ops.jsonl:<line>: <op_id> <code>: <message>`. Everything after `<line>: ` is exactly `str(OpError)`, the same text any caller of the API layer gets for that op. A future single-op CLI would print `<op_id> <code>: <message>` with no location prefix.
- **Summary:** always printed at the end, as `applied ops.jsonl to graph.db: applied=303 noop=6 skipped=0 rejected=0`.
- **Exit code:** 0 whenever the file was processed, **even if some ops were rejected**. Rejected ops are only logged to stderr and counted in the summary. This is the user's decision: "don't exit apply with non-zero status unless the entire apply fails."
- **Exit 1 only when the apply as a whole cannot run:** the ops file is missing or unreadable, the db is missing, or the db has the wrong `user_version`. A plain message is printed, and no op is attempted.

## Testing approach (shared)

- **Fixtures:** `tests/apply/conftest.py` provides a `store` fixture that loads a small hand-written seed into `tmp_path`. It's the same `BASE`-style tree as `tests/test_load.py`: conn → db → schema → two tables → a column, plus one reference.
- **Helpers:** `run(store, **op)` builds the op dict and calls `apply_op`. `live_names(store)` and `log_row(store, op_id)` read state for assertions.
- **Per-op tests:** each op's tests live in `tests/apply/test_<op>.py` and cover:
  - the happy path;
  - each `OpError` it can raise;
  - the op-log row;
  - for deletes, the `removals` rows and `deleted_by`;
  - nothing being written when the op fails, with the db unchanged and no log row.
- **Integration (foundation plan):**
  - Load seed 42, apply `ops.jsonl` 42, expect exit 0.
  - **Bad op mid-file:** a file with a bad op in the middle (an edit of an unknown name, plus a malformed line) exits 0 with `rejected=2`. Every later op is applied. Each stderr line equals `f"{path}:{line}: {e}"`, where `e` is the `OpError` that `apply_op` raises for that op on its own.
  - Live state equals `tests/replay.py`'s replay. Compare names and fields, not ids.
  - A second `apply` reports `skipped=309` and changes nothing.
  - The whole run takes under 5 s.

## Plans

- The foundation plan (`docs/superpowers/plans/2026-11-19-apply-foundation.md`) builds:
  - the package move;
  - domain types, errors and `parse_op`;
  - the db repositories and schema v2;
  - the registry, `apply_file` and the CLI;
  - the layer test.
- It registers handlers as they land. The per-op plans (`2026-11-19-op-<name>.md`) each add one handler and its tests, and can run in any order after the foundation.
