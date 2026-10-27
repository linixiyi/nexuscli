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


@pytest.fixture(autouse=True)
def _isolate_user_home(tmp_path_factory, monkeypatch: pytest.MonkeyPatch):
    """Point every test's user home at a throwaway directory.

    Anything the code resolves through `Path.home()` — the audit log, usage
    DB, session transcripts, long-term memory, skill registry, background task
    registry — used to land in the developer's real `~/.nexuscli` whenever a
    test forgot to redirect it. That silently mixed test fixtures into real
    user state: after one suite run the real audit log carried hundreds of
    `pytest-of-*` entries, and `/audit` in a live REPL showed them.

    Both HOME and USERPROFILE are set because win32 `Path.home()` prefers
    USERPROFILE; a HOME-only redirect leaks there. Tests that redirect HOME
    themselves still win: their patch lands after this fixture.
    """
    home = tmp_path_factory.mktemp("user-home")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    return home


@pytest.fixture(autouse=True)
def _reset_otlp_wiring_state():
    """Reset the OTLP one-shot wiring globals around every test.

    `nexuscli.telemetry.otlp` keeps process-lifetime state (attempted/warned
    flags plus the load_config-resolved telemetry face it consults before its
    cwd fallback). Without this reset, one test calling `load_config` with an
    `otlp.endpoint` would leak that endpoint into any later test that enables
    telemetry spans — wiring a dead endpoint into an unrelated assertion.
    The reset is a plain dataclass swap plus a global assignment; zero cost.
    """
    from nexuscli.telemetry.otlp import _reset_state_for_tests

    _reset_state_for_tests()
    yield
    _reset_state_for_tests()
