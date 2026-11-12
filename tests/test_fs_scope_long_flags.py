"""Long ``--flag=value`` write targets and the inner prompt's real tool name.

task-0006 (spec 2026-11-13_fix-project-trust-boundary). Two gaps in the
write-scope chain:

- ``_scan_paths`` skipped every ``-``-leading token wholesale, so the
  ``--target-directory=/etc`` spelling of a target-taking flag lost ``/etc``
  entirely and an out-of-workspace write classified as ``workspace``. The
  ``=`` spelling must split, queueing the value only for flags whose value
  IS a write target; ordinary flags (``--verbose``) and non-path value
  flags (``--mode=0755``, ``--reference=...``) gain no phantom targets.
- The bash handler's inner write-scope prompt evaluated permission rules
  under a hardcoded ``bash`` subject, so ``allow: ["execute_command"]``
  auto-approved at the executor yet the guard asked again — a stacked
  second prompt for the same call. The guard now evaluates (and prompts
  with) the caller's real tool name.

The two flag tables stay distinct on purpose: folding ``--target-directory``
into the skip-value table would teach the separate-value form
(``mv --target-directory /etc x``) to swallow ``/etc`` — an existing working
detection form failing OPEN. ``-t /etc`` fall-through must not regress.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest

from nexuscli.config import PermissionsConfig, load_config
from nexuscli.policy import FSScopeGuard
from nexuscli.tools import ToolRegistry, get_builtin_tools
from nexuscli.tools.base import ToolContext


# ---------------------------------------------------------------------------
# policy/fs_scope.py: --name=value long flags
# ---------------------------------------------------------------------------


@pytest.fixture
def scope_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Workspace and temp dir as siblings under tmp_path.

    gettempdir is pointed at the sibling so ``..`` escapes from the
    workspace are not absorbed by the system-tempdir allowance.
    """
    root = tmp_path / "ws"
    temp = tmp_path / "tmp"
    root.mkdir()
    temp.mkdir()
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(temp))
    return root


def _targets(command: str, root: Path) -> list[str]:
    return FSScopeGuard().extract_write_targets(command, root)


def _verdict(command: str, root: Path) -> str:
    guard = FSScopeGuard()
    return guard.classify(guard.extract_write_targets(command, root), root)


def test_mv_target_directory_equals_form_is_outside(scope_root: Path):
    # ① the value of a target-taking long flag is a write target
    assert _targets("mv --target-directory=/etc x", scope_root) == ["/etc", "x"]
    assert _verdict("mv --target-directory=/etc x", scope_root) == "outside"


def test_cp_target_directory_equals_form_is_outside(scope_root: Path):
    # ② same for cp, with a file-valued target directory
    assert _targets("cp --target-directory=/etc/hosts x", scope_root) == ["/etc/hosts", "x"]
    assert _verdict("cp --target-directory=/etc/hosts x", scope_root) == "outside"


def test_short_and_separate_long_target_forms_do_not_regress(scope_root: Path):
    # ③ ``-t /etc`` (and the separate-value long form) keep landing in
    # targets: recognizing the long spelling must not teach the separate
    # form to swallow its value — that would fail open.
    assert _targets("mv -t /etc x", scope_root) == ["/etc", "x"]
    assert _verdict("mv -t /etc x", scope_root) == "outside"
    assert _targets("mv --target-directory /etc x", scope_root) == ["/etc", "x"]
    assert _verdict("mv --target-directory /etc x", scope_root) == "outside"
    assert _targets("cp -t /etc/hosts x", scope_root) == ["/etc/hosts", "x"]
    assert _verdict("cp -t /etc/hosts x", scope_root) == "outside"


def test_plain_and_non_path_flags_gain_no_phantom_targets(scope_root: Path):
    # ④ ordinary flags are not misjudged: unknown long flags (with or
    # without a value) and the ``=`` spelling of non-path value flags add
    # nothing to the target list, and workspace verdicts stay workspace.
    assert _targets("mv --verbose a b", scope_root) == ["a", "b"]
    assert _verdict("mv --verbose a b", scope_root) == "workspace"
    assert _targets("mv --format=json a b", scope_root) == ["a", "b"]
    assert _targets("mkdir --mode=0755 d1", scope_root) == ["d1"]
    assert _verdict("mkdir --mode=0755 d1", scope_root) == "workspace"
    # --reference reads its value; it must not read as a write there.
    assert _targets("touch --reference=/etc/hosts f", scope_root) == ["f"]
    assert _verdict("touch --reference=/etc/hosts f", scope_root) == "workspace"


# ---------------------------------------------------------------------------
# tools/builtins.py: inner write-scope prompt uses the caller's real tool name
# ---------------------------------------------------------------------------


@pytest.fixture
def sandbox_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect config home away from the real ~/.nexuscli (repo convention).

    Path.home() reads USERPROFILE on Windows, so both are patched.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


def _make_context(tmp_path: Path, callback=None, allow=None) -> ToolContext:
    config = load_config(project_root=tmp_path, env={})
    config.policy.hitl_mode = "auto"
    if allow is not None:
        config.permissions = PermissionsConfig(allow=allow)
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    return ToolContext(cwd=str(ws), config=config, approval_callback=callback)


def _tool(name: str):
    registry = ToolRegistry()
    registry.register_all(get_builtin_tools())
    tool = registry.get(name)
    assert tool is not None
    return tool


def _run(command: str, context: ToolContext, name: str = "bash", **payload) -> ToolContext:
    return asyncio.run(_tool(name).execute({"command": command, **payload}, context))


def test_execute_command_allow_rule_skips_write_scope_prompt(
    tmp_path: Path, sandbox_home: Path, monkeypatch: pytest.MonkeyPatch
):
    # ⑤ allow: ["execute_command"] is the executor chain's standing
    # auto-approve for this tool; the inner write-scope prompt must
    # evaluate the same subject and not ask a second time.
    temp = tmp_path / "tmp"
    temp.mkdir()
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(temp))
    prompts: list[dict] = []

    def callback(request):
        prompts.append(request)
        return "approve"

    context = _make_context(tmp_path, callback=callback, allow=["execute_command"])
    result = _run("echo hi > ../outside.txt", context, name="execute_command")

    assert not result.is_error, result.content
    assert (tmp_path / "outside.txt").read_text(encoding="utf-8").strip() == "hi"
    assert prompts == []


def test_execute_command_bash_allow_rule_does_not_cover_it(
    tmp_path: Path, sandbox_home: Path, monkeypatch: pytest.MonkeyPatch
):
    # Mirrored fail-closed direction: allow: ["bash"] names the bash
    # subject only, so an execute_command call still hits the inner
    # prompt — and that prompt now names the real tool, not hardcoded bash.
    temp = tmp_path / "tmp"
    temp.mkdir()
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(temp))
    prompts: list[dict] = []

    def callback(request):
        prompts.append(request)
        return "deny"

    context = _make_context(tmp_path, callback=callback, allow=["bash"])
    result = _run("echo hi > ../outside.txt", context, name="execute_command")

    assert result.is_error
    assert len(prompts) == 1
    assert prompts[0]["tool_name"] == "execute_command"


def test_bash_allow_rule_still_covers_bash_without_reprompt(
    tmp_path: Path, sandbox_home: Path, monkeypatch: pytest.MonkeyPatch
):
    # The original allow path through the real registry tool: allow: ["bash"]
    # with a bash call must keep skipping the inner write-scope prompt.
    temp = tmp_path / "tmp"
    temp.mkdir()
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(temp))

    def callback(_request):
        raise AssertionError("allow: [bash] must not re-prompt the write scope")

    context = _make_context(tmp_path, callback=callback, allow=["bash"])
    result = _run("echo hi > ../outside.txt", context, name="bash")

    assert not result.is_error, result.content
    assert (tmp_path / "outside.txt").read_text(encoding="utf-8").strip() == "hi"
