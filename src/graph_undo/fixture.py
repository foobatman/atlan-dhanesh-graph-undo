"""Deterministic fixture generator: builds seed.jsonl from a seed value."""

from __future__ import annotations

import random
import uuid

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
TABLES_RANGE = (15, 21)
COLUMNS_RANGE = (5, 12)
BIG_SCHEMA_TABLES = 62  # ~62 x 9.5 nodes/table => subtree comfortably >= 550

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


def generate_seed(seed: int) -> tuple[list[dict], list[dict]]:
    """Build the seed graph: nodes in DFS pre-order, then edges (contains in the same order)."""
    rng = random.Random(seed)
    nodes: list[dict] = []
    edges: list[dict] = []
    total_schemas = len(CONNECTIONS) * DATABASES_PER_CONNECTION * SCHEMAS_PER_DATABASE
    big_schema_index = rng.randrange(total_schemas)
    schema_index = 0

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
                n_tables = (BIG_SCHEMA_TABLES if schema_index == big_schema_index
                            else rng.randint(*TABLES_RANGE))
                schema_index += 1
                for table_name in _unique_names(rng, TABLE_WORDS, n_tables):
                    table = add("table", table_name, schema)
                    for col_name in _unique_names(rng, COLUMN_WORDS, rng.randint(*COLUMNS_RANGE)):
                        add("column", col_name, table)
    return nodes, edges
