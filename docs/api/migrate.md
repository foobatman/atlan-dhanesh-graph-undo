# migrate

Applies any pending schema migrations (`src/graph_undo/db/migrations/NNNN_*.sql`) to an existing database, each one in its own transaction. `PRAGMA user_version` stores the number of the last migration applied. The latest is **3**:
- `0001`: the graph;
- `0002`: the op log and removals;
- `0003`: the undo log.

`apply`, `undo` and `dump` never migrate on their own. If a migration is pending, they refuse to run and print `database needs migrations [N…]; run: uv run migrate --db <db>`.

## CLI

```sh
uv run migrate --db graph.db
```

This prints `migrated graph.db: 1 -> 3` or `graph.db is up to date (3)`.

## Python

```python
# doctest-run
from graph_undo.api.migrate import LATEST, migrate_db, pending_for

assert LATEST == 3
assert pending_for(tmp_path / "docs.db") == []
assert migrate_db(tmp_path / "docs.db") == (3, 3)     # (version before, version after)
```

## Errors

`MigrationError(number, message)` is raised in two cases:
- a migration fails, which leaves the schema at `N-1`;
- the database is newer than the code.
