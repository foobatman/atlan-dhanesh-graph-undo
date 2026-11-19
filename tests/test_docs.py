"""docs/api/ stays in sync with the code: every error code documented, every operation has a runnable example."""

import json
import re
from pathlib import Path

import pytest

from apply.conftest import BASE, load_store
from graph_undo.api.registry import HANDLERS
from graph_undo.domain import errors

DOCS = Path(__file__).resolve().parents[1] / "docs" / "api"
OPERATIONS = sorted(HANDLERS) + ["apply", "load", "migrate", "undo"]
BLOCK = re.compile(r"```python\n(.*?)```", re.S)


def error_codes():
    return sorted(c.code for c in vars(errors).values()
                  if isinstance(c, type) and issubclass(c, errors.OpError) and c is not errors.OpError)


def test_every_error_code_documented():
    index = (DOCS / "README.md").read_text()
    missing = [code for code in error_codes() if f"`{code}`" not in index]
    assert missing == []


def test_index_links_every_operation():
    index = (DOCS / "README.md").read_text()
    assert [op for op in OPERATIONS if f"({op}.md)" not in index] == []


@pytest.mark.parametrize("op", OPERATIONS)
def test_every_op_has_doc(op):
    text = (DOCS / f"{op}.md").read_text()
    assert "uv run" in text
    assert "```python" in text


@pytest.mark.parametrize("op", OPERATIONS)
def test_python_examples_run(op, tmp_path):
    """The block starting with '# doctest-run' runs against a fresh BASE store (see tests/apply/conftest.py)."""
    blocks = [b for b in BLOCK.findall((DOCS / f"{op}.md").read_text()) if b.startswith("# doctest-run")]
    assert len(blocks) == 1, f"{op}.md needs exactly one ```python block starting with '# doctest-run'"
    seed_path = tmp_path / "seed.jsonl"
    seed_path.write_text("".join(json.dumps(r) + "\n" for r in BASE))
    store = load_store(tmp_path, name="docs.db")
    try:
        exec(compile(blocks[0], f"docs/api/{op}.md", "exec"),
             {"store": store, "tmp_path": tmp_path, "seed_path": seed_path})
    finally:
        store.close()


def test_decisions_covers_the_three_edit_kinds():
    text = (DOCS.parents[1] / "DECISIONS.md").read_text().lower()
    for word in ("independent", "conflicting", "caused", "rejected", "verified"):
        assert word in text
