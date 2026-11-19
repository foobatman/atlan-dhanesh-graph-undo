"""Deterministic ops.jsonl generator (see docs/superpowers/specs/2026-11-19-ops-fixture-design.md).

Ops address nodes by qualified_name; add_node mints ids at apply time, so no ids appear in ops.
A name-keyed simulator validates and applies every op as it is emitted.
"""

from __future__ import annotations

import random
from collections import defaultdict

from graph_undo.fixture.seed import OWNERS, TAGS

INGEST = "ingest-crawler"
LEVEL = {"connection": 0, "database": 1, "schema": 2, "table": 3, "column": 4}
RECREATE_TARGET = (55, 65)
TOTAL_AFTER_DELETE = (295, 315)
FILLER_CHUNK = (20, 30)


class _Graph:
    """Live graph keyed by qualified name. apply() enforces the ops contract."""

    def __init__(self, nodes: list[dict], edges: list[dict]):
        by_id = {n["id"]: n["qualified_name"] for n in nodes}
        self.nodes = {n["qualified_name"]: dict(n) for n in nodes}
        self.origin = {q: "seed" for q in self.nodes}
        self.parent: dict[str, str] = {}
        self.children: dict[str, list[str]] = defaultdict(list)
        self.refs: list[tuple[str, str]] = []
        self._refset: set[tuple[str, str]] = set()
        for e in edges:
            f, t = by_id[e["from_id"]], by_id[e["to_id"]]
            if e["type"] == "contains":
                self._link(f, t)
            else:
                self._add_ref(f, t)

    def _link(self, p, c):
        self.parent[c] = p
        self.children[p].append(c)

    def _unlink(self, c):
        self.children[self.parent.pop(c)].remove(c)

    def _add_ref(self, f, t):
        self.refs.append((f, t))
        self._refset.add((f, t))

    def _del_ref(self, f, t):
        self.refs.remove((f, t))
        self._refset.discard((f, t))

    def has_ref(self, f, t):
        return (f, t) in self._refset

    def subtree(self, qn):
        out, stack = [], [qn]
        while stack:
            q = stack.pop()
            out.append(q)
            stack.extend(reversed(self.children.get(q, [])))
        return out

    def apply(self, op: dict) -> None:
        kind, actor = op["op"], op["actor"]["kind"]

        def need(cond, msg):
            if not cond:
                raise ValueError(f"{op['op_id']} {kind}: {msg}")

        if kind == "add_node":
            qn = op["qualified_name"]
            if actor == "ingest" and qn in self.nodes:
                return
            need(qn not in self.nodes and op["parent"] in self.nodes, "name live or parent missing")
            need(LEVEL[op["type"]] == LEVEL[self.nodes[op["parent"]]["type"]] + 1, "type order")
            blank = actor == "ingest"
            self.nodes[qn] = {"qualified_name": qn, "type": op["type"],
                              "description": "" if blank else op["description"],
                              "owner": "" if blank else op["owner"],
                              "tags": [] if blank else op["tags"]}
            self.origin[qn] = "ops"
            self._link(op["parent"], qn)
        elif kind == "edit_node":
            need(actor == "human" and op["node"] in self.nodes, "bad edit")
            self.nodes[op["node"]].update(op["set"])
        elif kind == "delete_node":
            need(actor == "human" and op["node"] in self.nodes, "bad delete")
            gone = self.subtree(op["node"])
            gone_set = set(gone)
            if op["node"] in self.parent:
                self._unlink(op["node"])
            for q in gone:
                self.nodes.pop(q)
                self.origin.pop(q)
                self.parent.pop(q, None)
                self.children.pop(q, None)
            for r in [r for r in self.refs if r[0] in gone_set or r[1] in gone_set]:
                self._del_ref(*r)
        elif kind == "add_edge":
            f, t = op["from"], op["to"]
            need(f in self.nodes and t in self.nodes and f != t, "bad endpoints")
            if op["type"] == "contains":
                need(t not in self.parent, "child has a parent")
                need(LEVEL[self.nodes[t]["type"]] == LEVEL[self.nodes[f]["type"]] + 1, "type order")
                self._link(f, t)
            else:
                need(not self.has_ref(f, t), "duplicate reference")
                self._add_ref(f, t)
        elif kind == "delete_edge":
            f, t = op["from"], op["to"]
            need(actor == "human", "delete_edge by non-human")
            if op["type"] == "contains":
                need(self.parent.get(t) == f, "no such contains edge")
                self._unlink(t)
            else:
                need(self.has_ref(f, t), "no such reference")
                self._del_ref(f, t)
        else:
            raise ValueError(f"unknown op {kind}")


def generate_ops(seed: int, nodes: list[dict], edges: list[dict]) -> tuple[list[dict], dict[str, list[str]]]:
    """Return (ops, manifest). Manifest maps case name -> op_ids exercising it (not written to disk)."""
    rng = random.Random(f"ops:{seed}")
    g = _Graph(nodes, edges)
    ops: list[dict] = []
    manifest: dict[str, list[str]] = defaultdict(list)

    def emit(case: str, kind: str, op: str, actor: str | None = None, **fields) -> None:
        name = INGEST if kind == "ingest" else (actor or rng.choice(OWNERS))
        rec = {"op_id": f"op-{len(ops) + 1:04d}", "actor": {"kind": kind, "name": name}, "op": op, **fields}
        g.apply(rec)
        ops.append(rec)
        manifest[case].append(rec["op_id"])

    def live_tables(origin="seed", exclude=()):
        return [q for q, n in g.nodes.items()
                if n["type"] == "table" and g.origin[q] == origin and q not in exclude]

    # ---- plan from the seed --------------------------------------------------------------
    schemas = [q for q, n in g.nodes.items() if n["type"] == "schema"]
    root = max(schemas, key=lambda q: (len(g.subtree(q)), q))
    deleted = g.subtree(root)
    dset = set(deleted)
    parent_db = g.parent[root]
    conn = g.parent[parent_db]
    db_tags_at_delete = list(g.nodes[parent_db]["tags"])
    d_children = {q: list(g.children.get(q, [])) for q in deleted}
    removed_refs = [r for r in g.refs if r[0] in dset or r[1] in dset]
    inbound: dict[str, list[str]] = defaultdict(list)       # D table -> outside sources
    for f, t in removed_refs:
        if t in dset and f not in dset:
            inbound[t].append(f)
    boundary = sorted({x for r in removed_refs for x in r if x not in dset})

    d_tables = d_children[root][:]
    targets = sorted(inbound)
    rng.shuffle(targets)
    others = [t for t in d_tables if t not in inbound]
    rng.shuffle(others)
    recreate_order = targets + others

    t1 = recreate_order[0]
    x1 = sorted(inbound[t1])[0]
    t2 = next(t for t in recreate_order[1:] if any(x != x1 for x in inbound[t]))
    x2 = next(x for x in sorted(inbound[t2]) if x != x1)
    b = next(x for x in boundary if x not in (x1, x2)
             and any(r for r in removed_refs if x in r))
    reserved = {x1, x2, b}

    # ---- phase 1: big delete -------------------------------------------------------------
    emit("big_delete", "human", "delete_node", node=root)

    def pair(case: str, type_: str, old: tuple[str, str], new: tuple[str, str]) -> None:
        """Re-point or move: delete_edge then add_edge, two ops by the same human."""
        who = rng.choice(OWNERS)
        emit(case, "human", "delete_edge", actor=who, **{"from": old[0], "to": old[1], "type": type_})
        emit(case, "human", "add_edge", actor=who, **{"from": new[0], "to": new[1], "type": type_})

    def filler(n: int) -> None:
        protected = reserved | {parent_db}
        while n > 0:
            roll = rng.random()
            seed_nodes = [q for q in g.nodes if g.origin[q] == "seed" and q not in protected]
            tables = live_tables(exclude=protected)
            seed_refs = [r for r in g.refs if g.origin.get(r[0]) == "seed" == g.origin.get(r[1])
                         and not reserved & set(r)]
            if roll < 0.5:
                q = rng.choice(seed_nodes)
                field = rng.choice(["description", "owner", "tags"])
                val = {"description": f"edited: {q.rsplit('/', 1)[-1]} ({rng.randint(1, 999)})",
                       "owner": rng.choice(OWNERS),
                       "tags": sorted(rng.sample(TAGS, rng.randint(0, 2)))}[field]
                emit("filler", "human", "edit_node", node=q, set={field: val})
                n -= 1
            elif roll < 0.7:
                f, t = rng.choice(tables), rng.choice(tables)
                if f != t and not g.has_ref(f, t):
                    emit("filler", "human", "add_edge", **{"from": f, "to": t, "type": "references"})
                    n -= 1
            elif roll < 0.8 and seed_refs:
                f, t = rng.choice(seed_refs)
                emit("filler", "human", "delete_edge", **{"from": f, "to": t, "type": "references"})
                n -= 1
            elif seed_refs and n >= 2:
                f, t = rng.choice(seed_refs)
                z = rng.choice(tables)
                if z not in (f, t) and not g.has_ref(f, z):
                    pair("filler", "references", (f, t), (f, z))
                    n -= 2

    def gap() -> None:
        filler(rng.randint(*FILLER_CHUNK))

    # ---- phase 2: neighbour edits ----------------------------------------------------------
    new_db_tag = rng.choice([t for t in TAGS if t not in g.nodes[parent_db]["tags"]])
    emit("neighbour_edits", "human", "edit_node", node=parent_db,
         set={"tags": sorted(g.nodes[parent_db]["tags"] + [new_db_tag]),
              "description": f"database {parent_db} (reviewed)"})
    conn_tag = rng.choice([t for t in TAGS if t not in g.nodes[conn]["tags"]])
    emit("neighbour_edits", "human", "edit_node", node=conn,
         set={"tags": sorted(g.nodes[conn]["tags"] + [conn_tag])})
    for s in g.children[parent_db]:
        emit("neighbour_edits", "human", "edit_node", node=s, set={"owner": rng.choice(OWNERS)})
    for x in rng.sample([x for x in boundary if x not in reserved], 11):
        emit("neighbour_edits", "human", "edit_node", node=x,
             set={"description": f"table {x.rsplit('/', 1)[-1]} (touched after delete)",
                  "tags": sorted(set(g.nodes[x]["tags"]) | {rng.choice(TAGS)})})
    gap()

    # ---- phase 3: re-points and moves ------------------------------------------------------
    pairs = 0
    sources = [x for x in boundary if x not in reserved] + live_tables(exclude=reserved)
    for x in sources:
        if pairs == 10:
            break
        outs = [t for f, t in g.refs if f == x and t not in reserved]
        if not outs:
            continue
        y = outs[0]
        z = rng.choice(live_tables(exclude=reserved | {x, y}))
        if g.has_ref(x, z):
            continue
        pair("repoints", "references", (x, y), (x, z))
        pairs += 1
    s1, s2 = g.children[parent_db][:2]
    for t in [t for t in g.children[s1] if t not in reserved][:2]:
        pair("moves", "contains", (s1, t), (s2, t))
    gap()

    # ---- phase 4: ingest run ---------------------------------------------------------------
    target = rng.randint(*RECREATE_TARGET)
    emit("ingest_recreate", "ingest", "add_node", qualified_name=root, type="schema", parent=parent_db)
    recreated_tables, partial_tables = [], []
    for i, t in enumerate(recreate_order):
        if len(manifest["ingest_recreate"]) >= target and len(recreated_tables) >= 10:
            break
        emit("ingest_recreate", "ingest", "add_node", qualified_name=t, type="table", parent=root)
        recreated_tables.append(t)
        cols = d_children[t]
        k = rng.randint(1, len(cols) - 1) if i % 2 else len(cols)  # odd: partial, even: full
        if k < len(cols):
            partial_tables.append(t)
        for c in sorted(rng.sample(cols, k), key=cols.index):
            emit("ingest_recreate", "ingest", "add_node", qualified_name=c, type="column", parent=t)
    for q in rng.sample([q for q in g.nodes if g.origin[q] == "seed" and q in g.parent], 6):
        emit("ingest_retouch", "ingest", "add_node", qualified_name=q, type=g.nodes[q]["type"],
             parent=g.parent[q])
    e6 = next(t for t in reversed(recreated_tables) if t not in (t1, t2) and t not in partial_tables)
    e7 = next(t for t in partial_tables if t not in (t1, t2, e6))
    manifest["E7"] = [o["op_id"] for o in ops if o["op"] == "add_node"
                      and (o["qualified_name"] == e7 or o.get("parent") == e7)]
    ingest_done = 0
    srcs = live_tables(exclude=reserved)
    while ingest_done < 4:
        f, t = rng.choice(srcs), rng.choice([t for t in recreated_tables if t != e6])
        if not g.has_ref(f, t):
            emit("ingest_edges", "ingest", "add_edge", **{"from": f, "to": t, "type": "references"})
            ingest_done += 1
    gap()

    # ---- phase 5: human edits on recreated nodes -------------------------------------------
    emit("recreated_edits", "human", "edit_node", node=root,
         set={"tags": sorted(rng.sample(TAGS, 2)), "description": "schema recreated by ingest"})
    editable = [t for t in recreated_tables if t != e6]
    for t in rng.sample(editable, 4):
        emit("recreated_edits", "human", "edit_node", node=t,
             set={"owner": rng.choice(OWNERS), "description": f"table {t.rsplit('/', 1)[-1]} (re-documented)"})
    cols = [c for t in editable for c in g.children[t]]
    for c in rng.sample(cols, 4):
        emit("recreated_edits", "human", "edit_node", node=c, set={"tags": [rng.choice(TAGS)]})
    gap()

    # ---- phase 6: edge cases ---------------------------------------------------------------
    emit("E1", "human", "add_edge", **{"from": x1, "to": t1, "type": "references"})
    outs = [t for f, t in g.refs if f == x2 and t != t2]
    if not outs:  # setup: give X2 an edge to re-point
        y = next(t for t in live_tables(exclude=reserved) if t != x2 and not g.has_ref(x2, t))
        emit("E2", "human", "add_edge", **{"from": x2, "to": y, "type": "references"})
        outs = [y]
    pair("E2", "references", (x2, outs[0]), (x2, t2))
    claimed = next(t for t in d_tables if t not in g.nodes)
    emit("E3", "human", "add_node", qualified_name=claimed, type="table", parent=root,
         description=f"table {claimed.rsplit('/', 1)[-1]} (created by hand)",
         owner=rng.choice(OWNERS), tags=["certified"])
    emit("E4", "ingest", "add_node", qualified_name=claimed, type="table", parent=root)
    emit("E5", "human", "delete_node", node=b)
    emit("E6", "human", "delete_node", node=e6)
    gap()

    # ---- phase 7: late parent edit ---------------------------------------------------------
    current = g.nodes[parent_db]["tags"]
    drop = db_tags_at_delete[0] if db_tags_at_delete else new_db_tag
    emit("late_parent_edit", "human", "edit_node", node=parent_db,
         set={"tags": sorted(t for t in current if t != drop)})

    # ---- top-up filler ---------------------------------------------------------------------
    filler(max(0, rng.randint(*TOTAL_AFTER_DELETE) - (len(ops) - 1)))
    return ops, dict(manifest)
