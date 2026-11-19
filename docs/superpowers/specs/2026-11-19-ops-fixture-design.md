# Ops fixture generator — design

Date: 2026-11-19 · Status: approved (with add_node minting ids) · Source: `problem.md` ("Fixture", "Data model", "Cascade", "Actors", invariants)
Builds on: `docs/superpowers/specs/2026-11-19-seed-data-design.md` (seed graph, `generate_seed`)

## Intent

`problem.md` asks for `ops.jsonl`: one delete-subtree of ≥ 500 nodes, then ~300 operations of mixed kinds, generated deterministically from the same seed as `seed.jsonl`. `undo <op_id>` and the checker are judged against this file. So it has to produce every situation the invariants talk about, by construction rather than by luck, and every operation must be valid when applied in order.

This spec also fixes the **operation contract**: the five operation kinds, their fields and their semantics. `apply` (a later step) implements exactly these.

## Goals

- `uv run gen-fixture --seed N` writes `fixtures/ops.jsonl` next to `seed.jsonl`. Same seed → byte-identical files.
- `ops.jsonl` contains, at minimum, each case listed in problem.md, plus the edge cases the invariants imply (catalogue below).
- Every operation is valid against the graph state produced by `seed.jsonl` plus all earlier operations.

## Non-goals

- `apply`/`undo`/`dump`/checker. This spec only fixes the contract they consume.
- Computing expected undo outcomes. The checker derives those itself.
- Any undo/redo operation kinds inside `ops.jsonl`.

## Anti-goals

- Required cases that appear only for some seeds.
- Any operation that would be rejected at apply time (dangling ids, duplicate edges, human add of a live name, contains onto a node that already has a parent).
- Ingest operations that carry description, owner or tags.
- Randomness not derived from the seed.

## Constraints

- Python ≥ 3.11, stdlib only. Lives in the existing `graph_undo` package. Every command runs via `uv run`.
- Must stay well under 1 s (scenario budget is 60 s total).
- Follows the problem.md data model. No new node/edge fields.

## Approaches considered

| | Approach | Trade-off |
|---|---|---|
| **A (chosen)** | Scripted phases + seeded filler, generated against an in-memory simulation of the graph | Every case guaranteed and countable; data still varies per seed |
| B | Fully random op stream, retried until quotas are met | Rare cases (endpoint gone, parent not live) appear by chance; quotas need rejection loops |
| C | Hand-written scenario template with ids filled from the seed | Precise, but brittle: it hard-codes the seed graph's shape |

Picked A, by the partner's decision: it guarantees the invariant cases while staying seed-driven.

## Operation contract

### Common fields

Every line has `op_id`, `actor`, `op`, plus the op's own fields, and nothing else.

- `op_id`: `"op-0001"`, `"op-0002"`, … (4-digit zero-padded, consecutive, in file order).
- `actor`: `{"kind": "human" | "ingest", "name": <str>}`. Human names come from the seed's owner list. The ingest name is `"ingest-crawler"`.
- `op` ∈ {`add_node`, `edit_node`, `delete_node`, `add_edge`, `delete_edge`}. These are the only five kinds (partner's decision). **Re-pointing an edge is two separate operations**, `delete_edge` then `add_edge`, never a combined op (partner's decision).

### The five operations

| op | actor | fields | semantics |
|---|---|---|---|
**Nodes are addressed by `qualified_name`, never by id.** `add_node` mints a new id at apply time (partner's decision), so `ops.jsonl` can't know the ids of nodes created by ops. A live qualified name identifies exactly one node, so every node reference in an op is a qualified name, resolved against the live graph when the op is applied.

| op | actor | fields | semantics |
|---|---|---|---|
| `add_node` | human | `qualified_name`, `type`, `parent`, `description`, `owner`, `tags` | **Mints a new id.** Creates the node and the `contains` edge parent → new node. Invalid if `qualified_name` is live or `parent` isn't live. |
| `add_node` | ingest | `qualified_name`, `type`, `parent` | **Upsert by `qualified_name`.** If a live node has that name: no-op (nothing changes, including authored fields and parent). Otherwise: **mint a new id** and create the node with blank authored fields (`description: ""`, `owner: ""`, `tags: []`), plus the `contains` edge from `parent`. |
| `edit_node` | human only | `node`, `set` | `set` is a non-empty subset of {`description`, `owner`, `tags`}. Replaces those fields. `tags` is stored sorted and duplicate-free. |
| `delete_node` | human | `node` | Removes the node, every descendant along `contains`, and every edge (either type, either direction) touching any removed node. |
| `add_edge` | human or ingest | `from`, `to`, `type` | Both endpoints live, `from ≠ to`, edge not already present. For `contains`: `to` must currently have no parent, the result must not create a cycle, and the type order `connection > database > schema > table > column` must hold. |
| `delete_edge` | human | `from`, `to`, `type` | The edge must exist. Deleting a `contains` edge leaves the child temporarily parentless. The generator always follows it immediately with an `add_edge contains` for the same child (a move). |

`node`, `parent`, `from` and `to` are qualified names. How `apply` mints ids (e.g. `uuid4`, or deterministically from `op_id`) is for the apply spec. The fixture doesn't depend on it. Because names are resolved at apply time, an op naming a recreated R refers to the new node, which is what a human or crawler acting later would mean.

`qualified_name` is identity, not a path. It is set at creation and never changes, including on a move. New nodes created by the generator use `parent's qualified_name + "/" + name`.

## Scenario catalogue

Notation: **R** = the root of the big delete (the largest schema), **D** = the set of nodes it removes, **P** = R's parent database. **Boundary tables** = live tables outside D that had a `references` edge to or from D. **Recreated** = a node created by ingest whose `qualified_name` is in D.

Phases run in this order. Filler (phase 8) is interleaved between phases 2–7 at seeded positions, never before op-0001. Targets are minima the tests assert.

| # | Phase | Ops | What it produces | Invariant it exercises |
|---|---|---|---|---|
| 1 | **Big delete** | op-0001: human `delete_node R` | removes ≥ 550 nodes and every edge touching them | all, especially 4 (account for every node and edge) |
| 2 | **Neighbour edits** | ≥ 15 human `edit_node` | P gets a new tag (and a description edit). Both sibling schemas of R are edited. ≥ 8 boundary tables get owner/description/tags edits. P's connection gets a tag. | 1 (human edits untouched), 5 (restored nodes inherit P's new tag) |
| 3 | **Re-points** | ≥ 10 `delete_edge` + `add_edge` pairs (references); ≥ 2 contains moves | references X→Y re-pointed to X→Z, with X a boundary table where possible. Tables moved between P's surviving schemas (delete contains + add contains, adjacent). | 1, 5 (moved tables change inherited tags) |
| 4 | **Ingest run** | ≥ 50 ingest `add_node` recreating distinct names in D, parent-first; ≥ 5 ingest `add_node` re-touching live names (no-ops); ≥ 3 ingest `add_edge` references onto recreated tables | R's name recreated under P with a new id. ≥ 10 of D's tables recreated under it, plus some of their columns, until ≥ 50 distinct D names are recreated. **Pick order:** tables in D that were targets of a removed inbound reference from a boundary table come first (the seed forces 40 such references, so E1/E2 always have candidates). For some recreated tables only part of their columns are recreated, so originals of both kinds exist: a name recreated, and a name still free under a recreated parent name. | 2 (recreated names are not free), 3 (no duplicate names) |
| 5 | **Human edits to recreated nodes** | ≥ 8 human `edit_node` on recreated nodes, including recreated R's tags, ≥ 3 tables, ≥ 3 columns | authored fields on new-id nodes | 1, 5 (new tag on recreated R flows to its subtree) |
| 6 | **Edge cases** | see below | | |
| 7 | **Late parent edit** | 1 human `edit_node P` setting `tags` to P's current tags minus at least one. P's current tags always include the tag added in phase 2, so this works for every seed. If P had tags at delete time, remove one of those rather than the phase-2 tag. | an ancestor loses a tag after the delete, so restored nodes must not keep it | 5 |
| 8 | **Filler** | seeded mix until ops after op-0001 total **290–320** | human `edit_node` on random live nodes outside D. `add_edge`/`delete_edge` references between live tables. Re-point pairs. | volume, 1 |

### Phase 6 edge cases (each at least once)

| id | Case | Ops | Why it matters |
|---|---|---|---|
| E1 | Human reference into a recreated table | human `add_edge` X → T′, where **X is a boundary table that had a removed reference X→T** and T′ is T's recreation | Undo's old X→T meets the human's new X→T′. Both survive, or merge, or the report explains. |
| E2 | Re-point onto a recreated node | `delete_edge` X→Y, `add_edge` X→T′, again with X having had a removed X→T (a different X from E1) | Human intent about the boundary changed after the delete |
| E3 | Human claims a deleted name | human `add_node` with a name in D that ingest did **not** recreate, under recreated R, with authored fields | The name is taken by human work, not ingest. Inv 1 + 3. |
| E4 | Ingest re-touches the human-claimed name | ingest `add_node` with E3's name (no authored fields) | Must be a no-op. Ingest never overwrites. |
| E5 | Endpoint gone | human `delete_node` of a boundary table B (not X of E1/E2, not edited in phase 2), which had a removed reference into D | That reference can't be restored: endpoint gone. Inv 4. |
| E6 | Recreated node deleted again | human `delete_node` of a recreated table T′ that has recreated columns and no human edits | At undo time, T's name and its recreated columns' names are free again, while the name of T's parent (R) is held by recreated R′. |
| E7 | Mixed name states under one parent | from phase 4: a recreated table T′ where some of T's original columns have their names recreated and some don't | Some originals collide on name, their siblings don't, and they share a parent name that is held by a new id |

**What the fixture does not decide.** Invariant 2's "whose parent is live" can be read two ways: the original parent id, or whatever node holds the parent's name (or the parent restored earlier in the same undo, top-down). Which reading applies, and so which D nodes are *eligible* for restore, is an **undo rule** that goes in DECISIONS.md with the undo spec. The fixture only guarantees the raw states exist at the end of the ops, stated as facts the tests can check:

- (a) D nodes whose name is free and whose ancestor names are free up to, but not including, R. That's most of D.
- (b) D nodes whose name is held by a new id (recreated).
- (c) D nodes whose name is free but whose parent's name is held by a new id (E7, and E6's columns after deletion).
- (d) R's own name held by recreated R′, while R's original parent P is still live. |

## Generator design

- `generate_ops(seed: int, nodes: list[dict], edges: list[dict]) -> tuple[list[dict], dict]`. It returns the ops and a **manifest**: `{case_name: [op_id, …]}` for every phase and edge case, used by tests and handy for reading the file. The manifest is not written to disk, because problem.md's fixture is only the two files.
- Its own RNG: `random.Random(f"ops:{seed}")` (str seeding is deterministic), so tweaking ops generation never changes `seed.jsonl`.
- A small in-memory **simulator** (`_Graph`: live nodes keyed by qualified name with a minted internal id, parent map, children map, edge set keyed by names) applies each op as it's emitted, using the semantics above. Each op is generated from the current simulated state, and the simulator rejects (raises on) any invalid op. That makes the generator's own tests the first validator of the contract.
- R = largest schema by subtree size (same rule the seed tests use).
- `main()` writes both files: `seed.jsonl` then `ops.jsonl`, same serialisation as the seed (`sort_keys`, compact separators, `\n`, trailing newline). The stderr summary gains `ops=<n> recreated=<k>`.

## Verification (tests, over the same seed list as the seed tests)

1. Exact field sets per op kind and actor. Ingest ops never carry `description`/`owner`/`tags`. `edit_node` is human only.
2. `op_id`s are consecutive from `op-0001`. op-0001 is a human `delete_node` removing ≥ 500 nodes.
3. Ops after op-0001 number 290–320.
4. **Replay validity:** a test replays seed + all ops through an independent mini-applier written in the test file (not the generator's `_Graph`), and every op is valid.
5. At the end: ≥ 50 distinct D names are live under ids not in the seed (the test applier mints its own ids); ≥ 5 human `edit_node`s target such nodes.
6. Every manifest case has ≥ the minimum count above. E1–E7 each present. At end of replay, states (a)–(d) each have ≥ 1 node (computed by the independent test applier). E1/E2's X each had a seed reference to T (the original of T′).
7. Every `delete_edge contains` is immediately followed by an `add_edge contains` for the same child.
8. Re-point pairs: ≥ 10 adjacent `delete_edge` / `add_edge` references with the same `from` and a different `to`.
9. Determinism: identical bytes for the same seed, different for another seed. `seed.jsonl` bytes are unchanged by this work.

## Left to the builder

Exact pick order within phases, filler mix weights, which tags or descriptions get edited, and the internal helper structure. Any of these is fine as long as the minima and validity hold for every tested seed.
