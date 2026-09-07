"""Shared pytest fixtures."""

from __future__ import annotations

import pytest

# CI renderers force ANSI output (typer/rich read these at runtime), which
# changes rendering-based assertions depending on where tests run. Keep them
# out of every test so output rendering is identical locally and on CI.
_RENDER_ENV_VARS = ("CI", "GITHUB_ACTIONS", "FORCE_COLOR", "PY_COLORS")


@pytest.fixture(autouse=True)
def _isolate_render_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in _RENDER_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
