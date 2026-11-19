"""The checks. Pure functions over parsed dumps and reports: no I/O.

The rules come from problem.md's invariants and the readings in
docs/superpowers/specs/2026-11-19-checker-design.md, never from undo's own rules.
"""

from __future__ import annotations

from dataclasses import dataclass

from .model import AUTHORED, IDENTITY, Dump, Removed, fields

COMPARE = IDENTITY + AUTHORED


@dataclass(frozen=True)
class Result:
    status: str  # PASS | FAIL | SKIP
    summary: str
    violations: tuple[str, ...] = ()


def _result(violations: list[str], ok_summary: str) -> Result:
    if violations:
        return Result("FAIL", f"{len(violations)} violations", tuple(violations))
    return Result("PASS", ok_summary)


def _qn(d: Dump, i: str) -> str:
    n = d.nodes.get(i)
    return f"{i} ({n['qualified_name']})" if n else i


# --- guard ---------------------------------------------------------------

def _subtree(d: Dump, root: str) -> set[str]:
    kids: dict[str, list[str]] = {}
    for c, p in d.parent.items():
        kids.setdefault(p, []).append(c)
    out, stack = set(), [root]
    while stack:
        i = stack.pop()
        if i not in out:
            out.add(i)
            stack.extend(kids.get(i, []))
    return out


def guard(d0: Dump, d1: Dump, target_qn: str) -> list[str]:
    """D1 must be D0 minus exactly the target's subtree and every edge touching it."""
    holders = d0.names().get(target_qn, [])
    if len(holders) != 1:
        return [f"target {target_qn!r} is not exactly one live node before the delete (found {len(holders)})"]
    sub = _subtree(d0, holders[0])
    v = []
    for i in sorted(set(d1.nodes) - set(d0.nodes)):
        v.append(f"node {_qn(d1, i)} added by the delete")
    for i in sorted(set(d1.nodes) & set(d0.nodes)):
        if fields(d0.nodes[i], COMPARE) != fields(d1.nodes[i], COMPARE):
            v.append(f"node {_qn(d0, i)} changed by the delete")
    gone = set(d0.nodes) - set(d1.nodes)
    for i in sorted(sub - gone):
        v.append(f"node {_qn(d0, i)} is in the deleted subtree but still live")
    for i in sorted(gone - sub):
        v.append(f"node {_qn(d0, i)} removed but not in the deleted subtree")
    for e in sorted(d1.edges - d0.edges):
        v.append(f"edge {e} added by the delete")
    touching = {e for e in d0.edges if e[0] in sub or e[1] in sub}
    for e in sorted((d0.edges - d1.edges) - touching):
        v.append(f"edge {e} removed but touches no deleted node")
    for e in sorted(touching & d1.edges):
        v.append(f"edge {e} touches a deleted node but is still live")
    return v


# --- 3: unique names -------------------------------------------------------

def inv3_unique(d3: Dump, d4: Dump) -> Result:
    v = []
    for label, d in (("D3", d3), ("D4", d4)):
        for qn, ids in sorted(d.names().items()):
            if len(ids) > 1:
                v.append(f"{label}: name {qn!r} held by {', '.join(ids)}")
    return _result(v, f"{len(d3.nodes)} names unique")


# --- 5: effective_tags + structure ----------------------------------------

def _structure_and_tags(label: str, d: Dump) -> list[str]:
    v = []
    for f, t, ty in sorted(d.edges):
        for end in (f, t):
            if end not in d.nodes:
                v.append(f"{label}: edge ({f}, {t}, {ty}) has no node {end}")
    for i in sorted(d.multi_parent):
        v.append(f"{label}: node {_qn(d, i)} has more than one contains parent")
    expected: dict[str, frozenset[str]] = {}
    for i in sorted(d.nodes):
        chain, seen, cur = [], set(), i
        cyclic = False
        while cur is not None and cur in d.nodes:
            if cur in seen:
                cyclic = True
                break
            seen.add(cur)
            chain.append(cur)
            cur = d.parent.get(cur)
        if cyclic:
            v.append(f"{label}: node {_qn(d, i)} is on or under a contains cycle")
            continue
        tags = set()
        for a in chain:
            tags.update(d.nodes[a].get("tags") or [])
        expected[i] = frozenset(tags)
    for i, want in expected.items():
        got = d.nodes[i].get("effective_tags")
        if got != sorted(want):
            v.append(f"{label}: node {_qn(d, i)} effective_tags {got}, expected {sorted(want)}")
    return v


def inv5_effective_tags(d3: Dump, d4: Dump) -> Result:
    v = _structure_and_tags("D3", d3) + _structure_and_tags("D4", d4)
    return _result(v, f"{len(d3.nodes)} nodes")


# --- 6: idempotent --------------------------------------------------------

def inv6_idempotent(d3_text: str, d4_text: str, undo2_stderr: str = "") -> Result:
    first = next((l.strip() for l in undo2_stderr.splitlines() if l.strip()), "")
    note = f" (second undo: {first})" if first else ""
    if d3_text == d4_text:
        return Result("PASS", f"D3 == D4{note}")
    a, b = d3_text.splitlines(), d4_text.splitlines()
    v = []
    for n in range(max(len(a), len(b))):
        la = a[n] if n < len(a) else "<none>"
        lb = b[n] if n < len(b) else "<none>"
        if la != lb:
            v.append(f"line {n + 1}: D3 {la} / D4 {lb}")
            if len(v) == 3:
                break
    if not v:
        v.append("D3 and D4 differ only in line endings")
    return Result("FAIL", f"second undo changed the dump{note}", tuple(v))


# --- 7, 8 -----------------------------------------------------------------

INV7 = Result("SKIP", "not covered by this checker")


def inv8_timing(scenario_s: float, total_s: float, limit: float = 60.0) -> Result:
    summary = f"scenario {scenario_s:.1f} s (checker total {total_s:.1f} s)"
    if scenario_s < limit:
        return Result("PASS", summary)
    return Result("FAIL", summary, (f"scenario took {scenario_s:.1f} s, limit {limit:.0f} s",))


# --- 1: frame (no human change lost or altered) --------------------------

NAME_KEYS = ("node", "parent", "from", "to", "qualified_name")


def human_ops_on(ops_lines: list[dict], qn: str) -> list[str]:
    return [o.get("op_id", "?") for o in ops_lines
            if (o.get("actor") or {}).get("kind") == "human" and any(o.get(k) == qn for k in NAME_KEYS)]


def inv1_frame(d2: Dump, d3: Dump, rem: Removed, ops_lines: list[dict]) -> Result:
    def hint(qn: str) -> str:
        ids = human_ops_on(ops_lines, qn)
        return f" (human ops on {qn}: {', '.join(ids)})" if ids else ""

    v = []
    for i in sorted(d2.nodes):
        qn = d2.nodes[i]["qualified_name"]
        if i not in d3.nodes:
            v.append(f"node {_qn(d2, i)} was live before undo and is gone after{hint(qn)}")
            continue
        for k in COMPARE:
            if fields(d2.nodes[i], (k,)) != fields(d3.nodes[i], (k,)):
                v.append(f"node {_qn(d2, i)} {k} changed: {d2.nodes[i].get(k)!r} -> {d3.nodes[i].get(k)!r}{hint(qn)}")
    for e in sorted(d2.edges - d3.edges):
        v.append(f"edge {e} was live before undo and is gone after")
    added_nodes = set(d3.nodes) - set(d2.nodes)
    for i in sorted(added_nodes):
        if i not in rem.nodes:
            v.append(f"node {_qn(d3, i)} added by undo but the delete never removed it")
        elif fields(rem.nodes[i], COMPARE) != fields(d3.nodes[i], COMPARE):
            diff = [k for k in COMPARE if fields(rem.nodes[i], (k,)) != fields(d3.nodes[i], (k,))]
            v.append(f"node {_qn(d3, i)} restored but {', '.join(diff)} differs from before the delete")
    added_edges = d3.edges - d2.edges
    for e in sorted(added_edges - rem.edges):
        if not (e[2] == "contains" and e[1] in added_nodes):
            v.append(f"edge {e} added by undo but the delete never removed it")
    return _result(v, f"frame: {len(d2.nodes)} nodes, {len(d2.edges)} edges unchanged; "
                      f"{len(added_nodes)} nodes, {len(added_edges)} edges added")


# --- 2: eligible removed nodes restored intact (by name) -----------------

KEYS = {"node": ("id",), "edge": ("from_id", "to_id", "type")}


def _key_error(o: dict) -> str | None:
    """Why a report line can't be matched to a removed item, or None."""
    keys = KEYS.get(o.get("kind"))
    if keys is None:
        return f"kind {o.get('kind')!r} is not node or edge"
    bad = [k for k in keys if not isinstance(o.get(k), str)]
    return f"{', '.join(bad)} missing or not a string" if bad else None


def _report_index(report) -> tuple[dict[str, dict], dict[tuple, dict]]:
    nodes, edges = {}, {}
    for line in report:
        o = line.obj
        if not o:
            continue
        if _key_error(o) is not None:
            continue
        if o.get("kind") == "node":
            nodes.setdefault(o["id"], o)
        elif o.get("kind") == "edge":
            edges.setdefault((o["from_id"], o["to_id"], o["type"]), o)
    return nodes, edges


def inv2_restored(d0: Dump, d2: Dump, d3: Dump, rem: Removed, report) -> Result:
    live2 = d2.names()
    live3 = d3.names()
    rep_nodes, _ = _report_index(report)
    v, eligible = [], 0
    for i in sorted(rem.nodes, key=lambda i: (rem.nodes[i]["qualified_name"], i)):
        n = rem.nodes[i]
        if n["qualified_name"] in live2:
            continue
        p = rem.parent0.get(i)
        holder = None
        if p is not None:
            pn = d0.nodes[p]
            holders = [h for h in live3.get(pn["qualified_name"], []) if d3.nodes[h]["type"] == pn["type"]]
            if not holders:
                continue
            holder = holders[0]
        eligible += 1
        label = _qn(d0, i)
        if i not in d3.nodes:
            v.append(f"node {label} is eligible but not live after undo")
            continue
        if fields(n, COMPARE) != fields(d3.nodes[i], COMPARE):
            diff = [k for k in COMPARE if fields(n, (k,)) != fields(d3.nodes[i], (k,))]
            v.append(f"node {label} restored with changed {', '.join(diff)}")
        got = d3.parent.get(i)
        if got != holder:
            v.append(f"node {label} has parent {got}, expected {holder}")
        outcome = (rep_nodes.get(i) or {}).get("outcome")
        if outcome != "restored":
            v.append(f"node {label} report says {outcome!r}, expected 'restored'")
    if eligible == 0:
        return Result("FAIL", "vacuous: no eligible removed node")
    return _result(v, f"{eligible} eligible nodes restored intact")


# --- 4: report accounts for every removed item exactly once --------------

OUTCOMES = ("restored", "merged", "skipped")


def inv4_report(report, rem: Removed, d3: Dump, d2: Dump | None = None) -> Result:
    live_before = d2.edges if d2 is not None else frozenset()
    v = []
    if not report:
        v.append("report is empty")
    node_count: dict[str, int] = {}
    edge_count: dict[tuple, int] = {}
    for line in report:
        if line.error:
            v.append(f"report line {line.lineno}: {line.error}")
            continue
        o = line.obj
        kind, outcome = o.get("kind"), o.get("outcome")
        err = _key_error(o)
        if err:
            v.append(f"report line {line.lineno}: {err}")
            continue
        if outcome not in OUTCOMES:
            v.append(f"report line {line.lineno}: outcome {outcome!r} is not one of {', '.join(OUTCOMES)}")
        elif outcome != "restored" and not o.get("reason"):
            v.append(f"report line {line.lineno}: {outcome} without a reason")
        if kind == "node":
            i = o.get("id")
            node_count[i] = node_count.get(i, 0) + 1
            if outcome == "restored" and i not in d3.nodes:
                v.append(f"node {i} reported restored but not live after undo")
            if outcome == "skipped" and i in d3.nodes:
                v.append(f"node {i} reported skipped but live after undo")
            if outcome == "merged":
                v.append(f"node {i} reported merged; nodes are restored or skipped, never merged")
        else:
            e = (o.get("from_id"), o.get("to_id"), o.get("type"))
            edge_count[e] = edge_count.get(e, 0) + 1
            if outcome == "restored" and e not in d3.edges:
                v.append(f"edge {e} reported restored but not live after undo")
            if outcome == "skipped" and e in d3.edges and e not in live_before:
                v.append(f"edge {e} reported skipped but live after undo")
            if outcome == "merged":
                if e[2] != "contains":
                    v.append(f"edge {e} reported merged, but only contains edges can be merged")
                elif e in d3.edges:
                    v.append(f"edge {e} reported merged but its original triple is live after undo")
                elif e[1] not in d3.nodes or e[1] not in d3.parent:
                    v.append(f"edge {e} reported merged but child {e[1]} has no contains parent after undo")
    for i, k in sorted(node_count.items(), key=lambda kv: str(kv[0])):
        if k > 1:
            v.append(f"node {i} reported {k} times")
        if i not in rem.nodes:
            v.append(f"node {i} reported but the delete never removed it")
    for e, k in sorted(edge_count.items(), key=lambda kv: str(kv[0])):
        if k > 1:
            v.append(f"edge {e} reported {k} times")
        if e not in rem.edges:
            v.append(f"edge {e} reported but the delete never removed it")
    for i in sorted(set(rem.nodes) - set(node_count)):
        v.append(f"node {i} ({rem.nodes[i]['qualified_name']}) removed but not reported")
    for e in sorted(rem.edges - set(edge_count)):
        v.append(f"edge {e} removed but not reported")
    return _result(v, f"{len(rem.nodes)} nodes, {len(rem.edges)} edges, each reported once")
