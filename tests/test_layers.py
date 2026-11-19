"""Layer rules (spec: apply-foundation, "Package layout"): cli -> api -> db -> domain; SQL only in db/."""

import ast
import re
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "graph_undo"
LAYERS = ("domain", "db", "api", "cli")
ALLOWED = {
    "domain": set(),
    "db": {"domain"},
    "api": {"domain", "db"},
    "cli": {"api", "domain"},
}
SQL = re.compile(r"\b(SELECT|INSERT|UPDATE|DELETE FROM|CREATE)\b")


def modules():
    return sorted(p for p in SRC.rglob("*.py"))


def layer_of(path: Path) -> str:
    rel = path.relative_to(SRC).parts
    return rel[0] if len(rel) > 1 else "<top>"


def imported(tree: ast.AST, path: Path) -> set[str]:
    """Absolute module names imported by one file (relative imports resolved)."""
    pkg = ["graph_undo", *path.relative_to(SRC).parts[:-1]]
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = pkg[: len(pkg) - node.level + 1]
                mod = ".".join(base + ([node.module] if node.module else []))
            else:
                mod = node.module
            out.add(mod)
            out.update(f"{mod}.{a.name}" for a in node.names)
    return out


def project_layer(mod: str) -> str | None:
    parts = mod.split(".")
    if parts[0] != "graph_undo" or len(parts) < 2:
        return None
    return parts[1]


def test_layers_exist():
    for layer in LAYERS:
        assert (SRC / layer / "__init__.py").exists(), layer


def test_no_top_level_modules_left():
    assert sorted(p.name for p in SRC.glob("*.py")) == ["__init__.py"]


@pytest.mark.parametrize("path", modules(), ids=lambda p: str(p.relative_to(SRC)))
def test_imports_follow_layers(path):
    layer = layer_of(path)
    tree = ast.parse(path.read_text(encoding="utf-8"))
    mods = imported(tree, path)
    targets = {project_layer(m) for m in mods} - {None}
    if layer == "fixture":
        assert targets <= {"fixture"}, f"fixture imports {targets}"
        return
    assert "fixture" not in targets, f"{layer} must not import fixture"
    if layer in ALLOWED:
        bad = targets - ALLOWED[layer] - {layer}
        assert not bad, f"{path.relative_to(SRC)} ({layer}) imports {sorted(bad)}"
    if layer != "db":
        assert "sqlite3" not in {m.split(".")[0] for m in mods}, f"{path.relative_to(SRC)} imports sqlite3"
    if layer == "domain":
        stdlib_io = {"sqlite3", "pathlib", "io", "os", "shutil", "importlib"}
        assert not ({m.split(".")[0] for m in mods} & stdlib_io), f"domain does I/O: {mods & stdlib_io}"


@pytest.mark.parametrize("path", [p for p in modules() if layer_of(p) != "db"],
                         ids=lambda p: str(p.relative_to(SRC)))
def test_no_sql_outside_db(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            assert not SQL.search(node.value), f"SQL-like literal in {path.relative_to(SRC)}: {node.value!r}"


def test_sql_files_only_in_db():
    for p in SRC.rglob("*.sql"):
        assert layer_of(p) == "db", p
