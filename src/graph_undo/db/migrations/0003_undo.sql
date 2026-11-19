-- Migration 0003: undo log (docs/superpowers/specs/2026-11-19-undo-design.md, "Undo log").
-- undos: one row per undone op; UNIQUE(op_id) enforces "an op is undone at most once".
-- undo_items: the outcome of every node and edge the original op removed.

CREATE TABLE undos (
  undo_seq  INTEGER PRIMARY KEY,
  op_id     TEXT NOT NULL UNIQUE REFERENCES ops(op_id),
  after_seq INTEGER NOT NULL,                 -- max(ops.seq) when the undo ran
  report    TEXT NOT NULL                     -- the exact JSONL report printed
);

CREATE TABLE undo_items (
  op_id       TEXT NOT NULL REFERENCES undos(op_id),
  kind        TEXT NOT NULL CHECK (kind IN ('node','edge')),
  item_id     TEXT NOT NULL,                  -- node id, or edge id as text
  outcome     TEXT NOT NULL CHECK (outcome IN ('restored','merged','skipped')),
  reason_code TEXT,
  new_edge_id INTEGER REFERENCES edges(id),   -- merged contains edges only
  PRIMARY KEY (op_id, kind, item_id)
);
