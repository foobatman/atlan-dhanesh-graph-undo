# Seed data generator — design

Date: 2026-11-19 · Status: draft for approval · Source: `problem.md` ("Fixture")

## Intent

`problem.md` requires a script in the repo that generates the fixture deterministically from a seed value. This spec covers the first half: `seed.jsonl`, the starting graph (~5,000 nodes and edges). `ops.jsonl` is a later step, but the seed graph is shaped now so that step has what it needs: a delete target of ≥ 500 nodes, neighbours to edit, and references crossing the delete boundary.

## Goals

- `uv run gen-fixture --seed <int> [--out <dir>]` writes `<dir>/seed.jsonl` (default dir `fixtures/`, default seed `42`).
- Same seed → byte-identical file. Different seeds → different graphs.
- Records follow the `problem.md` data model exactly, with no extra fields.
- The graph exercises every rule undo will later depend on: contains cascade, cross-subtree references, and tag inheritance.

## Non-goals

- `ops.jsonl` (next step; it will live in the same script).
- Computing `effective_tags`: it is derived, and the tool computes it.
- Loading into SQLite (that's the `load` command).

## Anti-goals (failure even if it "works")

- Any randomness not derived from the seed: `uuid4()`, `time`, iteration over `set`/`dict` of unordered origin, hash-dependent ordering.
- Fields beyond the data model, or a `contains` graph that isn't a tree (invariant 2 speaks of a node's single parent).

## Constraints

- Python 3.11+, standard library only at runtime. pytest for tests (dev only).
- The project is managed with **uv**. Every command (generate, test, and later load/apply/undo/dump/check) runs as `uv run …`; nobody activates a venv or calls `python` directly.
- Must run well under 1 s; the full scenario budget is 60 s.

## Approaches considered

| | Approach | Trade-off |
|---|---|---|
| **A (chosen)** | One stdlib script, top-down walk with seeded random fan-outs, one schema forced past 500 nodes | No deps, byte-stable; tuning lives in constants |
| B | Config-file driven (YAML/JSON sizes, vocabularies) | Flexible, but there is one scenario; config is overhead |
| C | Faker for realistic names and descriptions | Adds a dependency, and output can drift across Faker versions, which breaks determinism |

Picked A: the brief's hard requirement is determinism, and A is the simplest thing that guarantees it.

## Design

### Record format (`seed.jsonl`)

One JSON object per line. All node lines come first, then all edge lines. A line is an edge if and only if it has `from_id`.

```json
{"description":"…","id":"…","owner":"alice","qualified_name":"snowflake/sales/public/orders_03","tags":["gold"],"type":"table"}
{"from_id":"…","to_id":"…","type":"contains"}
```

- Node fields, exactly: `id`, `qualified_name`, `type`, `description`, `owner`, `tags`.
- Edge fields, exactly: `from_id`, `to_id`, `type` ∈ {`contains`, `references`}.
- Serialised with `json.dumps(obj, sort_keys=True, separators=(",", ":"))`, UTF-8, `\n` line endings, trailing newline.

### Graph shape

Node `type` values (the brief leaves them open; our call): `connection → database → schema → table → column`.

| Level | Count |
|---|---|
| connections | 2 |
| databases per connection | 2 |
| schemas per database | 3 (12 total) |
| tables per schema | 18 (fixed, keeps totals in band for every seed) |
| columns per table | 7–10 (seeded); 8–10 in the big schema |
| **big schema** | one schema forced to 61 tables, so its subtree is **≥ 550 nodes by construction** (61 × 9 + 1) |

Target totals (asserted): ~2,400–2,600 nodes, contains edges = nodes − 2, ~300 references edges, **total lines 4,500–5,500**. Builder may tune fan-out constants to hit these.

- `contains`: parent → child, strict tree, exactly one parent per non-connection node, no cycles.
- `references`: ~300 table → table edges, no self-loops, no duplicate `(from_id, to_id)`. At least 60 cross the big schema boundary, in both directions (inbound and outbound), so the delete cuts edges touching outside nodes.

### Field values

- `id`: `str(uuid.UUID(int=rng.getrandbits(128), version=4))`, from the single `random.Random(seed)`.
- `qualified_name`: `/`-joined path of the node's ancestry names, e.g. `snowflake/sales/public/orders_03/amount`. Sibling names are unique, so qualified names are globally unique by construction. Names come from small fixed word lists plus a numeric suffix where needed.
- `owner`: from a fixed list of ~8 names (`alice`, `bob`, …). Ingest never sets it, so every node gets one.
- `description`: a template, e.g. `"table orders_03 in schema public"`. Non-empty, so "authored fields intact" is checkable.
- `tags`: vocabulary `pii, gold, silver, finance, marketing, certified, deprecated`. Each node gets 0–2 tags, probability weighted so ~30% of databases/schemas carry a tag (inheritance matters) and some descendants carry a tag already inherited (union must dedupe). Lists are sorted and duplicate-free.

### Output order

Nodes in depth-first pre-order of the tree (children in generation order). Then contains edges in the same order. Then references edges sorted by `(from qualified_name, to qualified_name)`.

### Project layout (uv)

```
pyproject.toml          # [project] requires-python >=3.11, no runtime deps
                        # [project.scripts] gen-fixture = "graph_undo.fixture:main"
                        # [dependency-groups] dev = ["pytest"]
                        # [build-system] uv_build (or hatchling), so the script entry point installs
uv.lock                 # committed
src/graph_undo/__init__.py
src/graph_undo/fixture.py
tests/test_fixture.py
```

Commands:

```
uv sync                                  # set up env (optional; uv run does it implicitly)
uv run gen-fixture --seed 42             # writes fixtures/seed.jsonl
uv run pytest                            # runs the tests
```

Later commands (load/apply/undo/dump) get their own `[project.scripts]` entry in the same package; the checker gets a separate package/module that doesn't import undo code.

### Code shape

`graph_undo/fixture.py` exposes `generate_seed(seed: int) -> tuple[list[dict], list[dict]]` (pure, in-memory) and a `main()` that writes the file. The future ops generator calls `generate_seed` and finds the delete target as the largest schema. It prints a one-line summary to stderr: node/edge counts, the big schema's qualified name and subtree size.

## Verification (tests)

1. Determinism: two runs with seed 42 produce identical bytes; seed 43 differs.
2. Exact field sets on every node and edge; edge `type` ∈ {contains, references}.
3. `id` and `qualified_name` unique across nodes; every edge endpoint exists.
4. `contains` is a tree: one parent per non-root, roots are exactly the connections, no cycles. Each child's qualified name = parent's + `/` + name.
5. Largest schema subtree ≥ 500 nodes; ≥ 60 references edges cross its boundary, in both directions.
6. Total line count in 4,500–5,500.

## Left to the builder

Exact word lists, fan-out constants (within the targets), and the internal helper structure.
