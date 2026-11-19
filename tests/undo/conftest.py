"""Undo tests reuse the apply fixtures (BASE tree, `store`, `run`, `snapshot`)."""

from apply.conftest import store  # noqa: F401  (pytest fixture)
