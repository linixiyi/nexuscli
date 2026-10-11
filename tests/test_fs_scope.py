"""Tests for the filesystem write-scope guard (policy/fs_scope.py).

Covers ``extract_write_targets`` (redirections, write commands, git forms,
environment assignments, process substitution, unparseable quoting) and
``classify`` (workspace pass, absolute / drive-letter outside, ``..``
escapes, tempdir allowance), plus the bash-handler wiring in
``tools/builtins.py``: an outside verdict routes through the same HITL
approval callback the executor uses, fails closed in never mode or without
a callback, and a workspace verdict never prompts.

``tempfile.gettempdir()`` is monkeypatched wherever ``..`` escapes are
classified: pytest's tmp_path lives inside the real temp tree, so an
escape from tmp_path would otherwise be swallowed by the tempdir
allowance.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

import pytest

from nexuscli.config import load_config
from nexuscli.policy import FSScopeGuard
from nexuscli.tools import ToolRegistry, get_builtin_tools
from nexuscli.tools.base import ToolContext, ToolResult


@pytest.fixture
def scope_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """A workspace and a temp dir that are siblings under tmp_path.

    gettempdir is pointed at the sibling so ``..`` escapes from the
    workspace are not absorbed by the system-tempdir allowance.
    """
    root = tmp_path / "ws"
    temp = tmp_path / "tmp"
    root.mkdir()
    temp.mkdir()
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(temp))
    return root, temp


def _targets(command: str, root: Path) -> list[str]:
    return FSScopeGuard().extract_write_targets(command, root)


def _verdict(command_or_targets: str | list[str], root: Path) -> str:
    guard = FSScopeGuard()
    targets = (
        command_or_targets
        if isinstance(command_or_targets, list)
        else guard.extract_write_targets(command_or_targets, root)
    )
    return guard.classify(targets, root)


# ---------------------------------------------------------------------------
# extract_write_targets: redirections
# ---------------------------------------------------------------------------


def test_simple_redirect_target_extracted(tmp_path: Path):
    assert _targets("echo hi > file.txt", tmp_path) == ["file.txt"]


def test_glued_redirect_without_spaces(tmp_path: Path):
    assert _targets("echo hi>file.txt", tmp_path) == ["file.txt"]


def test_append_and_stderr_redirects(tmp_path: Path):
    assert _targets("echo hi >> app.log 2> err.log", tmp_path) == [
        "app.log",
        "err.log",
    ]


def test_both_streams_redirects(tmp_path: Path):
    assert _targets("prog &> all.txt", tmp_path) == ["all.txt"]
    assert _targets("prog >& all.txt", tmp_path) == ["all.txt"]


def test_fd_duplication_is_not_a_target(tmp_path: Path):
    assert _targets("prog 2>&1 | tee out.txt", tmp_path) == ["out.txt"]


def test_input_redirection_is_not_a_target(tmp_path: Path):
    assert _targets("rm < in.txt", tmp_path) == []


def test_heredoc_tag_is_not_a_target(tmp_path: Path):
    assert _targets("cat <<EOF > out.txt", tmp_path) == ["out.txt"]


def test_process_substitution_is_conservative(tmp_path: Path):
    targets = _targets("echo hi > >(gzip > f.gz)", tmp_path)
    assert targets and any("\x00" in t for t in targets)
    assert _verdict("echo hi > >(gzip > f.gz)", tmp_path) == "outside"


# ---------------------------------------------------------------------------
# extract_write_targets: write commands
# ---------------------------------------------------------------------------


def test_write_command_args_extracted(tmp_path: Path):
    assert _targets("rm -rf build/ dist/", tmp_path) == ["build/", "dist/"]


def test_value_taking_flag_value_not_a_target(tmp_path: Path):
    assert _targets("mkdir -m 755 d1", tmp_path) == ["d1"]
    assert _targets("truncate -s 0 big.log", tmp_path) == ["big.log"]


def test_dd_of_operand_extracted(tmp_path: Path):
    assert _targets("dd if=/dev/zero of=/dev/sda", tmp_path) == ["/dev/sda"]


def test_tee_multiple_targets(tmp_path: Path):
    assert _targets("prog | tee a.txt b.txt", tmp_path) == ["a.txt", "b.txt"]


def test_cp_destination_and_source_extracted(tmp_path: Path):
    assert _targets("cp -r a b", tmp_path) == ["a", "b"]


def test_env_assignment_prefix_skipped(tmp_path: Path):
    assert _targets("FOO=1 rm -rf x", tmp_path) == ["x"]


def test_windows_exe_suffix_normalized(tmp_path: Path):
    assert _targets("rm.exe -r old", tmp_path) == ["old"]


def test_cmd_switch_flags_skipped(tmp_path: Path):
    assert _targets("copy /Y a.txt b.txt", tmp_path) == ["a.txt", "b.txt"]


def test_unknown_command_has_no_targets(tmp_path: Path):
    assert _targets('python -c "import os"', tmp_path) == []
    assert _verdict('python -c "import os"', tmp_path) == "workspace"


def test_git_checkout_path_form(tmp_path: Path):
    assert _targets("git checkout -- a.txt b.txt", tmp_path) == ["a.txt", "b.txt"]


def test_git_checkout_branch_form_has_no_targets(tmp_path: Path):
    assert _targets("git checkout main", tmp_path) == []


def test_git_clean_pathspec_extracted(tmp_path: Path):
    assert _targets("git clean -fdx build", tmp_path) == ["build"]


# ---------------------------------------------------------------------------
# classify: workspace passes
# ---------------------------------------------------------------------------


def test_relative_target_passes(scope_root: tuple[Path, Path]):
    root, _temp = scope_root
    assert FSScopeGuard().classify(["out.txt", "sub/dir/x.bin"], root) == "workspace"


def test_empty_target_list_passes(scope_root: tuple[Path, Path]):
    root, _temp = scope_root
    assert FSScopeGuard().classify([], root) == "workspace"
    assert FSScopeGuard().classify(None, root) == "workspace"


def test_workspace_root_itself_passes(scope_root: tuple[Path, Path]):
    root, _temp = scope_root
    assert FSScopeGuard().classify(["."], root) == "workspace"


def test_absolute_inside_workspace_passes(scope_root: tuple[Path, Path]):
    root, _temp = scope_root
    assert FSScopeGuard().classify([str(root / "file.txt")], root) == "workspace"


def test_system_tempdir_passes(scope_root: tuple[Path, Path]):
    root, temp = scope_root
    # Targets under the system temp directory pass even though the
    # workspace root is elsewhere.
    nested = str(temp / "sub" / "x.txt")
    assert FSScopeGuard().classify([str(temp / "scratch.txt"), nested], root) == ("workspace")


def test_workspace_command_passes_without_prompting(scope_root: tuple[Path, Path]):
    root, _temp = scope_root
    assert _verdict("echo hello > out.txt", root) == "workspace"
    assert _verdict("mkdir -p a/b", root) == "workspace"


# ---------------------------------------------------------------------------
# classify: outside
# ---------------------------------------------------------------------------


def test_dotdot_escape_is_outside(scope_root: tuple[Path, Path]):
    root, _temp = scope_root
    assert FSScopeGuard().classify(["../escape.txt"], root) == "outside"


def test_command_dotdot_escape_is_outside(scope_root: tuple[Path, Path]):
    root, _temp = scope_root
    assert _verdict("echo hi > ../escape.txt", root) == "outside"
    assert _verdict("mv a.txt ../../elsewhere", root) == "outside"


def test_absolute_path_outside_is_outside(scope_root: tuple[Path, Path]):
    root, _temp = scope_root
    outside = (
        "C:\\nexuscli-scope-test\\outside.txt"
        if os.name == "nt"
        else "/usr/local/nexuscli-scope-test/outside.txt"
    )
    assert FSScopeGuard().classify([outside], root) == "outside"


def test_posix_rooted_path_is_outside(scope_root: tuple[Path, Path]):
    root, _temp = scope_root
    assert _verdict("echo hi > /etc/hosts", root) == "outside"


def test_windows_backslash_drive_path_is_outside(scope_root: tuple[Path, Path]):
    # Regression: POSIX-mode shlex used to eat the backslashes as escapes,
    # which degraded a drive-letter path into a workspace-relative word.
    if os.name != "nt":
        pytest.skip("Windows path spelling")
    root, _temp = scope_root
    assert _verdict("echo hi > C:\\nexuscli-scope\\x.txt", root) == "outside"


def test_tilde_path_is_conservative_outside(scope_root: tuple[Path, Path]):
    root, _temp = scope_root
    assert FSScopeGuard().classify(["~/secret.txt"], root) == "outside"
    assert _verdict("rm ~/secret.txt", root) == "outside"


def test_glob_target_is_conservative_outside(scope_root: tuple[Path, Path]):
    root, _temp = scope_root
    assert _verdict("rm *.txt", root) == "outside"


# ---------------------------------------------------------------------------
# Conservatism: variables, substitution, unparseable quoting
# ---------------------------------------------------------------------------


def test_variable_in_write_position_is_conservative(scope_root: tuple[Path, Path]):
    root, _temp = scope_root
    assert _verdict("echo hi > $OUT/x.txt", root) == "outside"
    assert _verdict("rm $TARGET", root) == "outside"


def test_variable_in_read_position_does_not_escalate(scope_root: tuple[Path, Path]):
    # Documented middle ground: a variable the analyzer cannot resolve only
    # escalates when it sits in a write position; the redirect target here
    # is a plain workspace-relative path.
    root, _temp = scope_root
    assert _verdict("echo $HOME > out.txt", root) == "workspace"


def test_command_substitution_anywhere_is_conservative(scope_root: tuple[Path, Path]):
    root, _temp = scope_root
    assert _verdict("echo hi > $(mktemp)", root) == "outside"
    # Even in a read position: a substitution may write anywhere.
    assert _verdict("echo $(date) > out.txt", root) == "outside"


def test_unparseable_quoting_is_conservative(scope_root: tuple[Path, Path]):
    root, _temp = scope_root
    assert _verdict('echo "unterminated', root) == "outside"


# ---------------------------------------------------------------------------
# Pipes and quotes: basic tolerance
# ---------------------------------------------------------------------------


def test_quoted_metachar_is_not_a_redirect(scope_root: tuple[Path, Path]):
    root, _temp = scope_root
    assert _targets('echo "a > b" > out.txt', root) == ["out.txt"]
    assert _verdict('echo "a > b" > out.txt', root) == "workspace"


def test_quoted_target_with_space(scope_root: tuple[Path, Path]):
    root, _temp = scope_root
    assert _targets('rm "my file.txt"', root) == ["my file.txt"]
    assert _verdict('rm "my file.txt"', root) == "workspace"


def test_pipe_segments_are_independent(scope_root: tuple[Path, Path]):
    root, _temp = scope_root
    assert _targets("grep -r TODO . | tee hits.txt", root) == ["hits.txt"]
    assert _verdict("grep -r TODO . | tee hits.txt", root) == "workspace"


def test_semicolon_and_background_segments(scope_root: tuple[Path, Path]):
    root, _temp = scope_root
    assert _targets("mkdir a; rm b.txt", root) == ["a", "b.txt"]
    assert _targets("mkdir a & echo done", root) == ["a"]


# ---------------------------------------------------------------------------
# Multi-line commands: a newline is a command terminator like ";"
# ---------------------------------------------------------------------------


def test_newline_separates_segments(scope_root: tuple[Path, Path]):
    # shlex used to swallow the newline as plain whitespace, so the write
    # command on the second line was completely missed.
    root, _temp = scope_root
    assert _targets("echo start\nrm ~/secret.txt", root) == ["~/secret.txt"]
    assert _verdict("echo start\nrm ~/secret.txt", root) == "outside"


def test_newline_write_in_second_line_outside(scope_root: tuple[Path, Path]):
    root, _temp = scope_root
    assert _targets("echo a\ncp /etc/hosts /tmp/x", root) == ["/etc/hosts", "/tmp/x"]
    assert _verdict("echo a\ncp /etc/hosts /tmp/x", root) == "outside"


def test_newline_segments_are_independent(scope_root: tuple[Path, Path]):
    # Every line must still pass on its own: a safe multi-line command
    # must not regress into a prompt.
    root, _temp = scope_root
    assert _targets("echo start\nmkdir sub", root) == ["sub"]
    assert _verdict("echo start\nmkdir sub", root) == "workspace"


def test_quoted_multiline_string_survives_tokenizing(scope_root: tuple[Path, Path]):
    # A quoted string spanning lines is one token, not a segment cut.
    root, _temp = scope_root
    assert _targets('echo "line1\nline2" > out.txt', root) == ["out.txt"]
    assert _verdict('echo "line1\nline2" > out.txt', root) == "workspace"


# ---------------------------------------------------------------------------
# cmd.exe variable expansion: %VAR% in a write position is unresolvable
# ---------------------------------------------------------------------------


def test_percent_variable_redirect_target_is_conservative(scope_root: tuple[Path, Path]):
    root, _temp = scope_root
    assert _targets("echo hi > %TEMP%\\out.txt", root) == ["%TEMP%\\out.txt"]
    assert _verdict("echo hi > %TEMP%\\out.txt", root) == "outside"


def test_percent_variable_write_command_arg_is_conservative(scope_root: tuple[Path, Path]):
    root, _temp = scope_root
    assert _verdict("copy file.txt %APPDATA%\\evil.txt", root) == "outside"
    assert _verdict("del %APPDATA%\\keep.txt", root) == "outside"


def test_percent_variable_in_read_position_does_not_escalate(scope_root: tuple[Path, Path]):
    # Same documented middle ground as $VAR: only a write position escalates.
    root, _temp = scope_root
    assert _verdict("echo %USERNAME% > out.txt", root) == "workspace"


# ---------------------------------------------------------------------------
# Slash-switch tokens: only cmd.exe builtins with real /X switches skip them
# ---------------------------------------------------------------------------


def test_cmd_switches_still_skipped_for_cmd_builtins(scope_root: tuple[Path, Path]):
    root, _temp = scope_root
    assert _targets("copy /Y a.txt b.txt", root) == ["a.txt", "b.txt"]
    assert _targets("del /q /s junk.txt", root) == ["junk.txt"]
    assert _targets("rd /s /q build", root) == ["build"]
    assert _verdict("copy /Y a.txt b.txt", root) == "workspace"


def test_posix_single_segment_path_is_a_target_not_a_switch(scope_root: tuple[Path, Path]):
    # ``rm /etc`` used to be swallowed as a cmd.exe switch and classified
    # workspace; under POSIX spellings a /X token is an absolute path.
    root, _temp = scope_root
    assert _targets("rm /etc", root) == ["/etc"]
    assert _verdict("rm /etc", root) == "outside"
    assert _verdict("touch /etc/hosts", root) == "outside"


def test_mkdir_slash_token_is_treated_as_path(scope_root: tuple[Path, Path]):
    # cmd.exe's mkdir/md take no slash switches, so /X stays a path there.
    root, _temp = scope_root
    assert _targets("mkdir /etc/evil", root) == ["/etc/evil"]
    assert _verdict("mkdir /etc/evil", root) == "outside"


def test_git_never_treats_slash_tokens_as_switches(scope_root: tuple[Path, Path]):
    root, _temp = scope_root
    assert _targets("git clean -fdx /etc/x", root) == ["/etc/x"]
    assert _verdict("git clean -fdx /etc/x", root) == "outside"


# ---------------------------------------------------------------------------
# Wiring: bash handler routes outside writes through the approval callback
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


def _make_context(tmp_path: Path, callback=None, hitl_mode: str = "auto") -> ToolContext:
    config = load_config(project_root=tmp_path, env={})
    config.policy.hitl_mode = hitl_mode
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    return ToolContext(cwd=str(ws), config=config, approval_callback=callback)


def _tool(name: str = "bash"):
    registry = ToolRegistry()
    registry.register_all(get_builtin_tools())
    tool = registry.get(name)
    assert tool is not None
    return tool


def _run(
    command: str,
    context: ToolContext,
    name: str = "bash",
    **payload,
) -> ToolResult:
    return asyncio.run(_tool(name).execute({"command": command, **payload}, context))


def test_outside_write_prompts_then_runs_when_approved(
    tmp_path: Path, sandbox_home: Path, monkeypatch: pytest.MonkeyPatch
):
    temp = tmp_path / "tmp"
    temp.mkdir()
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(temp))
    prompts: list[dict] = []

    def callback(request):
        prompts.append(request)
        return "approve"

    context = _make_context(tmp_path, callback=callback)
    result = _run("echo hi > ../outside.txt", context)

    assert not result.is_error, result.content
    assert (tmp_path / "outside.txt").read_text(encoding="utf-8").strip() == "hi"
    assert len(prompts) == 1
    assert prompts[0]["tool_name"] == "bash"
    assert prompts[0]["danger_level"] == "high"
    assert "../outside.txt" in prompts[0]["description"]
    assert prompts[0]["input"]["command"] == "echo hi > ../outside.txt"


def test_outside_write_denied_when_callback_denies(
    tmp_path: Path, sandbox_home: Path, monkeypatch: pytest.MonkeyPatch
):
    temp = tmp_path / "tmp"
    temp.mkdir()
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(temp))
    prompts: list[dict] = []

    def callback(request):
        prompts.append(request)
        return "deny"

    context = _make_context(tmp_path, callback=callback)
    result = _run("echo hi > ../outside.txt", context)

    assert result.is_error
    assert "write-scope" in result.content or "outside" in result.content
    assert len(prompts) == 1
    assert not (tmp_path / "outside.txt").exists()


def test_never_mode_fails_closed_without_prompting(
    tmp_path: Path, sandbox_home: Path, monkeypatch: pytest.MonkeyPatch
):
    temp = tmp_path / "tmp"
    temp.mkdir()
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(temp))

    def callback(_request):
        raise AssertionError("never mode must not prompt")

    context = _make_context(tmp_path, callback=callback, hitl_mode="never")
    result = _run("echo hi > ../outside.txt", context)

    assert result.is_error
    assert "outside the workspace" in result.content


def test_no_callback_fails_closed(tmp_path: Path, sandbox_home: Path):
    context = _make_context(tmp_path, callback=None)
    outside = (
        "C:\\nexuscli-no-callback\\x.txt"
        if os.name == "nt"
        else "/usr/local/nexuscli-no-callback/x.txt"
    )
    result = _run(f"echo hi > {outside}", context)

    assert result.is_error
    assert "outside the workspace" in result.content


def test_inside_write_never_prompts(
    tmp_path: Path, sandbox_home: Path, monkeypatch: pytest.MonkeyPatch
):
    temp = tmp_path / "tmp"
    temp.mkdir()
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(temp))
    prompts: list[dict] = []

    def callback(request):
        prompts.append(request)
        return "approve"

    context = _make_context(tmp_path, callback=callback)
    result = _run("mkdir subdir", context)

    assert not result.is_error, result.content
    assert (tmp_path / "ws" / "subdir").is_dir()
    assert prompts == []


def test_approve_run_answer_is_narrowed_to_one_call(
    tmp_path: Path, sandbox_home: Path, monkeypatch: pytest.MonkeyPatch
):
    temp = tmp_path / "tmp"
    temp.mkdir()
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(temp))

    def callback(_request):
        return "approve_run"

    context = _make_context(tmp_path, callback=callback)
    result = _run("echo hi > ../outside.txt", context)

    # The command proceeds, but the run-scope grant set must stay untouched:
    # this prompt authorized one command's write scope, not the tool.
    assert not result.is_error, result.content
    assert context.run_scope_approved == set()


def test_execute_command_alias_gets_the_same_gate(
    tmp_path: Path, sandbox_home: Path, monkeypatch: pytest.MonkeyPatch
):
    temp = tmp_path / "tmp"
    temp.mkdir()
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(temp))
    prompts: list[dict] = []

    def callback(_request):
        prompts.append(_request)
        return "deny"

    context = _make_context(tmp_path, callback=callback)
    result = _run("echo hi > ../outside.txt", context, name="execute_command")

    assert result.is_error
    assert len(prompts) == 1


def test_background_outside_write_is_gated(
    tmp_path: Path, sandbox_home: Path, monkeypatch: pytest.MonkeyPatch
):
    temp = tmp_path / "tmp"
    temp.mkdir()
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(temp))

    def callback(_request):
        return "deny"

    context = _make_context(tmp_path, callback=callback)
    result = _run("echo hi > ../bg-outside.txt", context, run_in_background=True)

    assert result.is_error
    assert not (tmp_path / "bg-outside.txt").exists()


def test_plain_echo_runs_without_prompting(tmp_path: Path, sandbox_home: Path):
    prompts: list[dict] = []

    def callback(request):
        prompts.append(request)
        return "approve"

    context = _make_context(tmp_path, callback=callback)
    result = _run("echo hello", context)

    assert not result.is_error, result.content
    assert prompts == []
