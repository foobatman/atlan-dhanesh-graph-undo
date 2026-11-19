-- Graph store, flag-in-place model (docs/superpowers/specs/2026-11-19-load-design.md).
-- Deleted rows stay in place with deleted_by = <op_id>; reads go through the live_* views.

CREATE TABLE nodes (
  id             TEXT PRIMARY KEY,
  qualified_name TEXT NOT NULL,
  type           TEXT NOT NULL CHECK (type IN ('connection','database','schema','table','column')),
  description    TEXT NOT NULL,
  owner          TEXT NOT NULL,
  tags           TEXT NOT NULL,          -- JSON array, sorted and unique
  deleted_by     TEXT                    -- NULL = live; else op_id of the delete that removed it
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

-- Own tags plus the tags of every ancestor along live contains edges; one row per live node.
CREATE VIEW effective_tags AS
  WITH RECURSIVE anc(node_id, anc_id) AS (
    SELECT id, id FROM live_nodes
    UNION
    SELECT anc.node_id, e.from_id
      FROM anc JOIN live_edges e ON e.to_id = anc.anc_id AND e.type = 'contains'
  ),
  tagged AS (
    SELECT node_id, json_group_array(tag) AS tags
      FROM (SELECT DISTINCT a.node_id, j.value AS tag
              FROM anc a JOIN live_nodes p ON p.id = a.anc_id, json_each(p.tags) j
             ORDER BY a.node_id, tag)
     GROUP BY node_id
  )
  SELECT n.id AS node_id, COALESCE(t.tags, '[]') AS tags
    FROM live_nodes n LEFT JOIN tagged t ON t.node_id = n.id;

PRAGMA user_version = 1;
