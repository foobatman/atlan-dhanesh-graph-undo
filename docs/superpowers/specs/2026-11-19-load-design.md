# `load` command: design

**Status:** draft
**Brief:** `problem.md`, the `load seed.jsonl` command: "Create the database from the seed."

## Intent

There is one command, `uv run graph-undo load fixtures/seed.jsonl`. It builds a fresh SQLite file holding the seed graph. The schema it creates is the base that `apply`, `undo`, `dump` and the checker will build on. It is designed so that a delete can later be undone without copying rows anywhere.

## Goals

- Store nodes and edges exactly as `problem.md` defines them, plus one bookkeeping column (`deleted_by`) for soft deletion.
- The database enforces the graph's structural rules, so a bad seed fails loudly:
  - unique name among live nodes;
  - one live parent per node;
  - no duplicate live edge;
  - edges only between existing nodes.
- One place, the live views, that filters out deleted rows. Everything downstream reads through it.
- `effective_tags` is derived on read and never stored.
- Loading the same seed always produces the same database contents.

## Non-goals (left to later specs)

- The op log (`ops`) and the removal log (`removals`). These belong to the operations spec, and `load` doesn't create them.
- Crash safety and `CRASH_AFTER`. These belong to the undo spec.
- `dump`, `apply` and `undo` themselves.

## Anti-goals

- A partially loaded database left behind after a bad seed.
- A second copy of `effective_tags` that can go stale.

## Approaches considered

| | A: separate history | B: flag in place (**chosen**) |
|---|---|---|
| Live tables | Only live rows; plain unique indexes | Live and deleted rows; `deleted_by` column; partial unique indexes |
| Delete | Copy rows to history, then remove them | Set `deleted_by` |
| Undo restore | Copy back | Clear the flag; the row's authored fields are still there, frozen |
| Reads | No filter | Filter through `live_nodes` / `live_edges` views |
| Delete → restore → delete again | A new snapshot per delete | Needs a removal log (operations spec) because `deleted_by` holds only the latest |

**The user chose B.**
- It is easier to understand.
- The data-model rule is flexible enough for one bookkeeping column.
- Performance is the same at this size: about 5k rows and a 614-node delete take milliseconds either way.
- The filter-everywhere risk is handled by interface design. Reads go only through views; the views are the single place the filter is written.

## Schema

```sql
PRAGMA foreign_keys = ON;   -- set on every connection

CREATE TABLE nodes (
  id             TEXT PRIMARY KEY,
  qualified_name TEXT NOT NULL,
  type           TEXT NOT NULL CHECK (type IN ('connection','database','schema','table','column')),
  description    TEXT NOT NULL,
  owner          TEXT NOT NULL,
  tags           TEXT NOT NULL,          -- JSON array, sorted and unique, e.g. '["gold","pii"]'
  deleted_by     TEXT                    -- NULL = live; else the op_id of the delete that removed it
);

CREATE TABLE edges (
  id         INTEGER PRIMARY KEY,        -- row id: a dead edge and a re-added one can coexist
  from_id    TEXT NOT NULL REFERENCES nodes(id),
  to_id      TEXT NOT NULL REFERENCES nodes(id),
  type       TEXT NOT NULL CHECK (type IN ('contains','references')),
  deleted_by TEXT,
  CHECK (from_id <> to_id)
);

CREATE UNIQUE INDEX live_name   ON nodes(qualified_name)       WHERE deleted_by IS NULL;
CREATE UNIQUE INDEX live_edge   ON edges(from_id, to_id, type) WHERE deleted_by IS NULL;
CREATE UNIQUE INDEX live_parent ON edges(to_id) WHERE type = 'contains' AND deleted_by IS NULL;
CREATE INDEX edges_from ON edges(from_id);
CREATE INDEX edges_to   ON edges(to_id);

CREATE VIEW live_nodes AS
  SELECT id, qualified_name, type, description, owner, tags FROM nodes WHERE deleted_by IS NULL;
CREATE VIEW live_edges AS
  SELECT id, from_id, to_id, type FROM edges WHERE deleted_by IS NULL;
CREATE VIEW effective_tags AS ...  -- see below
```

**`effective_tags` view.** It has columns `(node_id, tags)`, one row per live node.
- `tags` is a JSON array, sorted and unique, of the node's own tags together with the tags of every ancestor reached through `live_edges` of type `contains`.
- The builder writes it as a recursive CTE using `json_each` and `json_group_array`.
- Dead nodes and dead edges never contribute.

The schema lives in `src/graph_undo/schema.sql` and is applied with `executescript`. `PRAGMA user_version = 1` marks the schema version.

**Rule recorded for later specs:** reads of graph state go through `live_nodes`, `live_edges` and `effective_tags`. Only the store module touches the base tables.

## Worked example

Take a seed with two tables in one schema, where `customers` references `orders`. After `load` (ids shortened):

```
nodes  n1 …/public   n2 …/orders [gold]   n3 …/orders/amount [pii]   n4 …/orders/id   n5 …/customers   (all deleted_by NULL)
edges  1 n1→n2 contains   2 n2→n3 contains   3 n2→n4 contains   4 n1→n5 contains   5 n5→n2 references
```

`effective_tags` for `n3` is `["gold","pii"]`.

Later, under the operations spec, a delete of `…/orders` by op-0007 sets `deleted_by = 'op-0007'` on `n2`, `n3`, `n4` and on edges 1, 2, 3 and 5. The live views then show only `n1`, `n5` and edge 4. Ingest can later insert a new live `…/orders` row: `live_name` allows it, because `n2` is dead.

## The command

```
uv run graph-undo load fixtures/seed.jsonl            # writes ./graph.db
uv run graph-undo load fixtures/seed.jsonl --db PATH
```

- **Entry point.** `[project.scripts] graph-undo = "graph_undo.cli:main"`, using argparse subcommands. Only `load` exists for now. `apply`, `undo` and `dump` are added by later specs.
- **Replacement.** `load` always creates the database from scratch. An existing file at `--db` is replaced.
- **All or nothing.**
  1. Build at `<db>.tmp`, deleting any stale tmp file first.
  2. Insert in one transaction.
  3. `os.replace` onto `<db>` only on success.
  4. On failure, remove the tmp file; any previous `<db>` is left untouched.
- **Success output.** Exit 0. Print a summary on stderr: `loaded graph.db: nodes=2503 edges=2801`.
- **Failure output.** Exit 1. Print `<seed path>:<line>: <message>` on stderr.

## Input handling

- **Line format.** Each non-empty line is a JSON object. A line containing `from_id` is an edge; any other line is a node. Blank lines are ignored.
- **Order.** Line order doesn't matter: all nodes are inserted first, then all edges.
- **Node keys.** Exactly `id, qualified_name, type, description, owner, tags`. Edge keys: exactly `from_id, to_id, type`. Unknown or missing keys are an error.
- **Field types.** `tags` must be a list of strings. It is stored sorted and de-duplicated, with compact JSON (`separators=(",",":")`). Every other node field must be a string.
- **Edge ids.** Assigned 1…N in file order of edge lines. Every loaded row has `deleted_by = NULL`.
- **Checks that report the offending line:**
  - bad JSON;
  - wrong keys or types;
  - unknown node or edge `type`;
  - duplicate `id`;
  - duplicate `qualified_name`;
  - an edge to an unknown id;
  - a self-loop;
  - a duplicate edge;
  - a second `contains` parent;
  - a `contains` edge that breaks type order (connection → database → schema → table → column, one level down). Type order also rules out cycles.

  The constraints catch most of these. Map the `sqlite3.IntegrityError` back to the line being inserted.

## Files

| File | Role |
|---|---|
| `src/graph_undo/schema.sql` | DDL above |
| `src/graph_undo/store.py` | `connect(path) -> sqlite3.Connection` (foreign keys on) and `load_seed(seed_path, db_path) -> (n_nodes, n_edges)`, raising `LoadError(line, message)` |
| `src/graph_undo/cli.py` | `main(argv=None)` with argparse subcommands |
| `.gitignore` | Add `*.db`, `*.db.tmp` |
| `README.md` | Status table, the `load` command, schema summary, layout |

The runtime is stdlib only (`sqlite3`, `json`, `argparse`).

## Tests (`tests/test_load.py`)

- Loading seed 42 gives counts equal to the seed's node and edge lines.
- **Round trip.** `live_nodes` and `live_edges` read back equal the seed nodes and edges, with tags parsed and edge ids ignored.
- Every row has `deleted_by IS NULL`.
- `effective_tags` equals an independent Python computation (own tags ∪ ancestors' tags) for every node.
- Each check under "Input handling" fails with exit 1, names the right line, and leaves no `<db>` and no `<db>.tmp`.
- A failed load over an existing `<db>` leaves it byte-identical.
- Loading twice replaces the database. Two loads of the same seed have identical table contents.
- The partial indexes accept a second row with a live name once the first row is marked dead (a direct SQL test of the B model).
- `uv run graph-undo load` works end to end through the entry point.

## Left to the builder

- The exact SQL of the `effective_tags` view.
- Error message wording.
- Whether validation happens in Python before insert or by mapping constraint errors. Either is fine, provided each failure reports its line.
