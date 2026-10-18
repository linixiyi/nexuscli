"""Lane B machine verification: the `/index` and `/search` slash commands.

The testing matrix had both rows at 机器验证 0. These assertions pin what a
user sees: the `Indexed N code lines.` confirmation after `/index`, the
`path:line: snippet` hits from `/search`, the `(no matches)` empty state,
and that dependency directories are not indexed.
"""

from __future__ import annotations

import asyncio
import io
from pathlib import Path

from rich.console import Console

from nexuscli.config import NexusCliConfig
from nexuscli.entrypoints.repl import _handle_slash

APP_PY = '''"""Sample project indexed by the lane-B tests."""


def calculate_total(price, quantity):
    return price * quantity


def calculate_tax(amount):
    return amount * 0.2
'''

UTILS_PY = '''TIMEOUT_SECONDS = 30


def retry_failed_request(url):
    return f"retrying {url}"
'''


def _make_project(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text(APP_PY, encoding="utf-8")
    (tmp_path / "utils.py").write_text(UTILS_PY, encoding="utf-8")
    deps = tmp_path / "node_modules" / "left-pad"
    deps.mkdir(parents=True)
    (deps / "index.js").write_text("module.exports = n => 'x'.repeat(n);\n", encoding="utf-8")


def _run(command: str, tmp_path: Path) -> str:
    console = Console(file=io.StringIO(), width=120)
    asyncio.run(
        _handle_slash(
            command, console, str(tmp_path), NexusCliConfig(), None, None, None, None, None
        )
    )
    return console.file.getvalue()


def test_index_reports_the_number_of_indexed_lines(tmp_path: Path) -> None:
    _make_project(tmp_path)

    out = _run("/index", tmp_path)

    # app.py has 5 non-empty lines, utils.py has 3; the node_modules file is
    # excluded, so the reported count must be exactly 8.
    assert "Indexed 8 code lines." in out


def test_index_into_a_subdirectory_scopes_the_rebuild(tmp_path: Path) -> None:
    _make_project(tmp_path)

    out = _run("/index utils.py", tmp_path)

    assert "Indexed 3 code lines." in out


def test_search_returns_path_line_snippet_hits(tmp_path: Path) -> None:
    _make_project(tmp_path)
    _run("/index", tmp_path)

    out = _run("/search calculate_total", tmp_path)

    assert "app.py:4: def calculate_total(price, quantity):" in out
    # Term matching is per-line AND: the body line does not contain the term
    # and must not appear as a hit.
    assert "return price * quantity" not in out


def test_search_miss_reports_no_matches(tmp_path: Path) -> None:
    _make_project(tmp_path)
    _run("/index", tmp_path)

    out = _run("/search definitely-not-in-the-project", tmp_path)

    assert "(no matches)" in out


def test_search_without_a_query_does_not_crash(tmp_path: Path) -> None:
    _make_project(tmp_path)
    _run("/index", tmp_path)

    out = _run("/search", tmp_path)

    assert "(no matches)" in out


def test_search_never_returns_dependency_directory_hits(tmp_path: Path) -> None:
    _make_project(tmp_path)
    _run("/index", tmp_path)

    out = _run("/search module.exports", tmp_path)

    assert "node_modules" not in out
    assert "(no matches)" in out
