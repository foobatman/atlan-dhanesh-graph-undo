"""gen-fixture CLI: writes seed.jsonl and ops.jsonl, deterministic from a seed value."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from graph_undo.fixture.ops import generate_ops
from graph_undo.fixture.seed import generate_seed, serialize


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gen-fixture", description=__doc__)
    parser.add_argument("--seed", type=int, default=42, help="seed value (default 42)")
    parser.add_argument("--out", type=Path, default=Path("fixtures"),
                        help="output directory (default fixtures/)")
    args = parser.parse_args(argv)

    nodes, edges = generate_seed(args.seed)
    args.out.mkdir(parents=True, exist_ok=True)
    path = args.out / "seed.jsonl"
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(serialize(nodes, edges))

    ops, manifest = generate_ops(args.seed, nodes, edges)
    with open(args.out / "ops.jsonl", "w", encoding="utf-8", newline="\n") as f:
        f.write(serialize(ops, []))

    schemas = [n for n in nodes if n["type"] == "schema"]
    big = max(schemas, key=lambda s: sum(1 for n in nodes
                                         if n["qualified_name"].startswith(s["qualified_name"] + "/")))
    big_size = 1 + sum(1 for n in nodes if n["qualified_name"].startswith(big["qualified_name"] + "/"))
    print(f"wrote {path}: nodes={len(nodes)} edges={len(edges)} "
          f"big_schema={big['qualified_name']} big_size={big_size} "
          f"ops={len(ops)} recreated={len(manifest['ingest_recreate'])}", file=sys.stderr)
    return 0
