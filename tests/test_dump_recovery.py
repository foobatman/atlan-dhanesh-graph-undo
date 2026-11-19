"""dump next to a crashed writer (invariant 7) and the snapshot's consistency."""

import json
import subprocess
import sys

from graph_undo.api.dump import dump_lines
from graph_undo.api.load import load_seed
from graph_undo.fixture.seed import generate_seed, serialize

CRASH = """
import os, sqlite3, sys
con = sqlite3.connect(sys.argv[1], isolation_level=None)
con.execute("PRAGMA journal_mode=DELETE")
con.execute("PRAGMA cache_size=1")           # tiny cache: dirty pages spill into the db file
con.execute("BEGIN IMMEDIATE")
con.execute("UPDATE edges SET deleted_by='op-crash' WHERE type='references'")
con.execute("UPDATE nodes SET description = description || ' (crashed edit)'")
os._exit(0)                                  # die mid-transaction: hot journal left behind
"""


def test_dump_after_writer_crash_shows_last_commit(tmp_path):
    seed = tmp_path / "s.jsonl"
    seed.write_text(serialize(*generate_seed(42)))
    db = tmp_path / "g.db"
    load_seed(seed, db)
    before = dump_lines(db)
    raw = db.read_bytes()
    subprocess.run([sys.executable, "-c", CRASH, str(db)], check=True)
    assert db.read_bytes() != raw, "test setup: crash should have spilled pages into the db file"
    assert (tmp_path / "g.db-journal").exists(), "test setup: expected a hot journal"
    after = dump_lines(db)                      # must not fail
    assert after == before                      # the uncommitted delete is not visible
    ids = {json.loads(x)["id"] for x in after if "from_id" not in x}
    assert all(json.loads(x)["from_id"] in ids and json.loads(x)["to_id"] in ids
               for x in after if "from_id" in x)
