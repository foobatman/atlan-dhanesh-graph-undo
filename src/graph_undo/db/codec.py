"""Column encodings shared by the repositories."""

from __future__ import annotations

import json
from collections.abc import Iterable


def encode_tags(tags: Iterable[str]) -> str:
    """Tags as stored: compact JSON array, sorted and duplicate-free."""
    return json.dumps(sorted(set(tags)), separators=(",", ":"), ensure_ascii=False)
