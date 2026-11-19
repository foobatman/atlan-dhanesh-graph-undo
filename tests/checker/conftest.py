import subprocess
from functools import cache

import pytest


@cache
def has_undo() -> bool:
    return subprocess.run(["uv", "run", "undo", "--help"], capture_output=True).returncode == 0


needs_undo = pytest.mark.skipif(not has_undo(), reason="undo entry point not available yet")


@pytest.fixture(scope="session")
def fixture42(tmp_path_factory):
    out = tmp_path_factory.mktemp("fx42")
    r = subprocess.run(["uv", "run", "gen-fixture", "--seed", "42", "--out", str(out)],
                       capture_output=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    return out / "seed.jsonl", out / "ops.jsonl"
