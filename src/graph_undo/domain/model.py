"""Graph domain values. Pure Python: no I/O, no database."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

NODE_TYPES = ("connection", "database", "schema", "table", "column")
EDGE_TYPES = ("contains", "references")
LEVEL = {t: i for i, t in enumerate(NODE_TYPES)}
AUTHORED_FIELDS = ("description", "owner", "tags")


def normalise_tags(tags: Iterable[str]) -> tuple[str, ...]:
    """Sorted, duplicate-free. Strings are kept exactly as given (no trimming, no case folding)."""
    return tuple(sorted(set(tags)))


@dataclass(frozen=True)
class Node:
    """A node row as the API sees it; `tags` is already a sorted tuple."""
    id: str
    qualified_name: str
    type: str
    description: str
    owner: str
    tags: tuple[str, ...]
    deleted_by: str | None = None
