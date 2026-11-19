-- Migration 0002: op log and removals (docs/superpowers/specs/2026-11-19-apply-foundation-design.md).
-- ops: one row per applied op (rejected ops are never logged). removed_*: what each delete op removed.

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

-- "removals": two typed tables, so each id carries a real foreign key.
CREATE TABLE removed_nodes (
  op_id   TEXT NOT NULL REFERENCES ops(op_id),
  node_id TEXT NOT NULL REFERENCES nodes(id),
  PRIMARY KEY (op_id, node_id)
);
CREATE TABLE removed_edges (
  op_id   TEXT NOT NULL REFERENCES ops(op_id),
  edge_id INTEGER NOT NULL REFERENCES edges(id),
  PRIMARY KEY (op_id, edge_id)
);
CREATE INDEX removed_nodes_by_node ON removed_nodes(node_id);   -- "which deletes ever removed this node?"
CREATE INDEX removed_edges_by_edge ON removed_edges(edge_id);
