"""Deterministic fixture generator: builds seed.jsonl from a seed value."""

from __future__ import annotations

import argparse
import json
import random
import sys
import uuid
from pathlib import Path

CONNECTIONS = ["snowflake", "postgres"]
DATABASES = ["sales", "finance", "marketing", "ops", "hr", "product"]
SCHEMAS = ["public", "staging", "analytics", "raw", "core", "mart"]
TABLE_WORDS = ["orders", "customers", "invoices", "payments", "products", "events",
               "sessions", "accounts", "refunds", "shipments", "campaigns", "leads"]
COLUMN_WORDS = ["id", "name", "amount", "created_at", "updated_at", "status", "email",
                "country", "currency", "quantity", "price", "region", "score", "type"]
OWNERS = ["alice", "bob", "carol", "dave", "erin", "frank", "grace", "heidi"]

DATABASES_PER_CONNECTION = 2
SCHEMAS_PER_DATABASE = 3
# Narrow ranges keep totals inside the spec's bands for any seed (~2,510 nodes, sd ~17).
TABLES_RANGE = (18, 18)
COLUMNS_RANGE = (7, 10)
BIG_COLUMNS_RANGE = (8, 10)  # floor: 61 x (1 + 8) + 1 = 550, so the big schema is >= 550 by construction
REFERENCES_TOTAL = 300
REFERENCES_INBOUND = 40   # other table -> big-schema table
REFERENCES_OUTBOUND = 40  # big-schema table -> other table
BIG_SCHEMA_TABLES = 61

TAGS = ["certified", "deprecated", "finance", "gold", "marketing", "pii", "silver"]
# Weights for drawing 0, 1 or 2 own tags, per node type.
TAG_WEIGHTS = {
    "connection": [60, 30, 10],
    "database": [55, 35, 10],
    "schema": [60, 30, 10],
    "table": [55, 35, 10],
    "column": [80, 17, 3],
}


def _make_id(rng: random.Random) -> str:
    return str(uuid.UUID(int=rng.getrandbits(128), version=4))


def _unique_names(rng: random.Random, words: list[str], count: int) -> list[str]:
    """`count` distinct sibling names: shuffled words, suffixed _NN once words run out."""
    pool = words[:]
    rng.shuffle(pool)
    return [pool[i] if i < len(pool) else f"{pool[i % len(pool)]}_{i // len(pool):02d}"
            for i in range(count)]


def _draw_tags(rng: random.Random, type_: str) -> list[str]:
    k = rng.choices([0, 1, 2], weights=TAG_WEIGHTS[type_])[0]
    return sorted(rng.sample(TAGS, k))


def _references(rng: random.Random, big: list[dict], other: list[dict]) -> list[dict]:
    """Table -> table references: forced crossings of the big schema, then uniform random."""
    tables = big + other
    seen: set[tuple[str, str]] = set()
    pairs: list[tuple[dict, dict]] = []

    def draw(src: list[dict], dst: list[dict], count: int) -> None:
        while count:
            a, b = rng.choice(src), rng.choice(dst)
            if a is not b and (a["id"], b["id"]) not in seen:
                seen.add((a["id"], b["id"]))
                pairs.append((a, b))
                count -= 1

    draw(other, big, REFERENCES_INBOUND)
    draw(big, other, REFERENCES_OUTBOUND)
    draw(tables, tables, REFERENCES_TOTAL - REFERENCES_INBOUND - REFERENCES_OUTBOUND)
    pairs.sort(key=lambda p: (p[0]["qualified_name"], p[1]["qualified_name"]))
    return [{"from_id": a["id"], "to_id": b["id"], "type": "references"} for a, b in pairs]


def generate_seed(seed: int) -> tuple[list[dict], list[dict]]:
    """Build the seed graph: nodes in DFS pre-order, then edges (contains in the same order)."""
    rng = random.Random(seed)
    nodes: list[dict] = []
    edges: list[dict] = []
    total_schemas = len(CONNECTIONS) * DATABASES_PER_CONNECTION * SCHEMAS_PER_DATABASE
    big_schema_index = rng.randrange(total_schemas)
    schema_index = 0
    big_tables: list[dict] = []
    other_tables: list[dict] = []

    def add(type_: str, name: str, parent: dict | None) -> dict:
        qn = name if parent is None else f"{parent['qualified_name']}/{name}"
        where = f"connection {name}" if parent is None else (
            f"{type_} {name} in {parent['type']} {parent['qualified_name'].rsplit('/', 1)[-1]}")
        node = {
            "id": _make_id(rng),
            "qualified_name": qn,
            "type": type_,
            "description": where,
            "owner": rng.choice(OWNERS),
            "tags": _draw_tags(rng, type_),
        }
        nodes.append(node)
        if parent is not None:
            edges.append({"from_id": parent["id"], "to_id": node["id"], "type": "contains"})
        return node

    for conn_name in CONNECTIONS:
        conn = add("connection", conn_name, None)
        for db_name in _unique_names(rng, DATABASES, DATABASES_PER_CONNECTION):
            db = add("database", db_name, conn)
            for schema_name in _unique_names(rng, SCHEMAS, SCHEMAS_PER_DATABASE):
                schema = add("schema", schema_name, db)
                is_big = schema_index == big_schema_index
                n_tables = BIG_SCHEMA_TABLES if is_big else rng.randint(*TABLES_RANGE)
                schema_index += 1
                for table_name in _unique_names(rng, TABLE_WORDS, n_tables):
                    table = add("table", table_name, schema)
                    (big_tables if is_big else other_tables).append(table)
                    n_cols = rng.randint(*(BIG_COLUMNS_RANGE if is_big else COLUMNS_RANGE))
                    for col_name in _unique_names(rng, COLUMN_WORDS, n_cols):
                        add("column", col_name, table)
    edges.extend(_references(rng, big_tables, other_tables))
    return nodes, edges


def serialize(nodes: list[dict], edges: list[dict]) -> str:
    """JSONL: one compact, key-sorted object per line, nodes then edges, trailing newline."""
    return "".join(json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n"
                   for r in [*nodes, *edges])


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

    schemas = [n for n in nodes if n["type"] == "schema"]
    big = max(schemas, key=lambda s: sum(1 for n in nodes
                                         if n["qualified_name"].startswith(s["qualified_name"] + "/")))
    big_size = 1 + sum(1 for n in nodes if n["qualified_name"].startswith(big["qualified_name"] + "/"))
    print(f"wrote {path}: nodes={len(nodes)} edges={len(edges)} "
          f"big_schema={big['qualified_name']} big_size={big_size}", file=sys.stderr)
    return 0
