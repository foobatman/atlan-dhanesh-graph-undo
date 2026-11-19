# CLAUDE.md

Project brief: `problem.md`. Specs live in `docs/superpowers/specs/`, plans in `docs/superpowers/plans/`.

## Workflow rules

- **Update `README.md` after every logical change, before moving to the next task.** Keep the status table, commands, file formats and layout in sync with the code. A change isn't done until the README reflects it, and the README update goes in the same commit (or the commit right after it).
- Use **uv** for everything. Every command runs as `uv run …` (e.g. `uv run pytest`, `uv run gen-fixture`). Never call `python`/`pytest` directly or activate a venv. Add dependencies with `uv add` (dev ones with `uv add --dev`).
- Runtime code is stdlib-only unless a spec says otherwise.
- Follow the data model in `problem.md` exactly. Don't add fields to nodes or edges.
