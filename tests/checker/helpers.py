"""Builders for hand-written dumps and reports (checker tests only)."""

import json


def line(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"


def node(id, qn, type="table", tags=(), eff=None, description="", owner=""):
    tags = sorted(set(tags))
    return {"id": id, "qualified_name": qn, "type": type, "description": description,
            "owner": owner, "tags": tags, "effective_tags": sorted(set(eff if eff is not None else tags))}


def dump_text(nodes, edges) -> str:
    ns = sorted(nodes, key=lambda n: n["qualified_name"])
    es = sorted(edges)
    return "".join(line(n) for n in ns) + "".join(
        line({"from_id": f, "to_id": t, "type": ty}) for f, t, ty in es)


def report_text(items) -> str:
    return "".join(line(i) for i in items)
