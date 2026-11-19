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

def inv6_idempotent(d3_text: str, d4_text: str) -> Result:
    if d3_text == d4_text:
        return Result("PASS", "D3 == D4")
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
    return Result("FAIL", "second undo changed the dump", tuple(v))


# --- 7, 8 -----------------------------------------------------------------

INV7 = Result("SKIP", "not covered by this checker")


def inv8_timing(scenario_s: float, total_s: float, limit: float = 60.0) -> Result:
    summary = f"scenario {scenario_s:.1f} s (checker total {total_s:.1f} s)"
    if scenario_s < limit:
        return Result("PASS", summary)
    return Result("FAIL", summary, (f"scenario took {scenario_s:.1f} s, limit {limit:.0f} s",))
