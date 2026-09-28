from __future__ import annotations

import asyncio

from nexuscli.config import NexusCliConfig
from nexuscli.entrypoints.repl import _match_custom_command
from nexuscli.entrypoints.slash_commands import (
    CustomCommand,
    expand_command,
    expand_custom_command,
    load_slash_commands,
    split_command_message,
    split_positional_args,
)


def _write_command(directory, name: str, text: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(text, encoding="utf-8")


def test_load_slash_commands_reads_user_and_project_scopes(tmp_path):
    user_dir = tmp_path / "home" / ".nexuscli" / "commands"
    project_dir = tmp_path / "proj" / ".nexuscli" / "commands"
    _write_command(user_dir, "review.md", "Review the code carefully.")
    _write_command(
        project_dir,
        "deploy.md",
        "---\ndescription: Ship to prod\n---\nDeploy $ARGUMENTS",
    )

    commands = load_slash_commands(str(tmp_path / "proj"), home=tmp_path / "home")

    assert set(commands) == {"review", "deploy"}
    assert commands["review"].source == "user"
    assert commands["review"].description == ""
    assert commands["deploy"].source == "project"
    assert commands["deploy"].description == "Ship to prod"


def test_project_scope_overrides_user_scope_on_name_conflict(tmp_path):
    user_dir = tmp_path / "home" / ".nexuscli" / "commands"
    project_dir = tmp_path / "proj" / ".nexuscli" / "commands"
    _write_command(user_dir, "fix.md", "user version")
    _write_command(project_dir, "fix.md", "project version")

    commands = load_slash_commands(str(tmp_path / "proj"), home=tmp_path / "home")

    assert commands["fix"].body == "project version"
    assert commands["fix"].source == "project"


def test_load_slash_commands_ignores_invalid_entries(tmp_path):
    commands_dir = tmp_path / "proj" / ".nexuscli" / "commands"
    commands_dir.mkdir(parents=True)
    (commands_dir / "empty.md").write_text("---\ndescription: no body\n---\n", encoding="utf-8")
    (commands_dir / "weird name.md").write_text("body", encoding="utf-8")
    (commands_dir / "notes.txt").write_text("body", encoding="utf-8")

    assert load_slash_commands(str(tmp_path / "proj"), home=tmp_path / "home") == {}


def test_expand_custom_command_substitutes_arguments_placeholder():
    command = CustomCommand(
        name="greet",
        description="",
        body="Say hello to $ARGUMENTS in French.",
        source="user",
        path=None,
    )

    assert expand_custom_command(command, "Alice") == "Say hello to Alice in French."
    assert expand_custom_command(command, "") == "Say hello to  in French."


def test_expand_custom_command_appends_args_without_placeholder():
    command = CustomCommand(
        name="audit",
        description="",
        body="Audit the repository.",
        source="project",
        path=None,
    )

    assert (
        expand_custom_command(command, "focus on tests")
        == "Audit the repository.\n\nfocus on tests"
    )
    assert expand_custom_command(command, "") == "Audit the repository."


def test_split_command_message_normalizes_case():
    assert split_command_message("/Review the diff") == ("/review", "the diff")
    assert split_command_message("/deploy") == ("/deploy", "")
    assert split_command_message("not a command") is None


def test_match_custom_command_returns_expanded_prompt(tmp_path):
    project_dir = tmp_path / "proj" / ".nexuscli" / "commands"
    _write_command(project_dir, "explain.md", "Explain $ARGUMENTS step by step.")
    commands = load_slash_commands(str(tmp_path / "proj"), home=tmp_path / "home")
    config = NexusCliConfig()

    match = asyncio.run(_match_custom_command("/explain the session store", commands, config))

    assert match is not None
    assert match[0].name == "explain"
    assert match[1].prompt == "Explain the session store step by step."

    assert asyncio.run(_match_custom_command("/unknown-command hi", commands, config)) is None
    assert asyncio.run(_match_custom_command("plain message", commands, config)) is None


# ---------------------------------------------------------------------------
# Frontmatter extension: mode / allowed-tools / argument-hint
# ---------------------------------------------------------------------------


def test_parse_command_file_reads_mode_allowed_tools_and_argument_hint(tmp_path):
    directory = tmp_path / "proj" / ".nexuscli" / "commands"
    _write_command(
        directory,
        "ship.md",
        "---\n"
        "description: Ship it\n"
        "mode: plan\n"
        "allowed-tools: read_file, glob , read_file,bash\n"
        "argument-hint: [target]\n"
        "---\n"
        "Ship $ARGUMENTS",
    )

    command = load_slash_commands(str(tmp_path / "proj"), home=tmp_path / "home")["ship"]

    assert command.description == "Ship it"
    assert command.mode == "plan"
    assert command.allowed_tools == ("read_file", "glob", "bash")
    assert command.argument_hint == "[target]"


def test_parse_command_file_ignores_invalid_mode(tmp_path):
    directory = tmp_path / "proj" / ".nexuscli" / "commands"
    _write_command(directory, "yolo.md", "---\nmode: yolo\ndescription: skip\n---\nBody text.")

    command = load_slash_commands(str(tmp_path / "proj"), home=tmp_path / "home")["yolo"]

    assert command.mode == ""
    assert command.description == "skip"


# ---------------------------------------------------------------------------
# Positional arguments $1..$9
# ---------------------------------------------------------------------------


def test_split_positional_args_splits_like_a_shell():
    assert split_positional_args('one "two words" three') == ["one", "two words", "three"]
    assert split_positional_args("") == []


def test_expand_command_fills_positional_args_and_blanks_out_of_range():
    command = CustomCommand(name="pick", body="first=$1 second=$2 third=$3")

    expansion = asyncio.run(expand_command(command, "'a b' c d"))
    assert expansion.prompt == "first=a b second=c third=d"

    out_of_range = asyncio.run(expand_command(command, "only"))
    assert out_of_range.prompt == "first=only second= third="


def test_expand_command_arguments_placeholder_and_append_compat():
    placeholder = CustomCommand(name="greet", body="Hello $ARGUMENTS!")
    assert asyncio.run(expand_command(placeholder, "world")).prompt == "Hello world!"

    plain = CustomCommand(name="audit", body="Audit the repository.")
    assert (
        asyncio.run(expand_command(plain, "focus on tests")).prompt
        == "Audit the repository.\n\nfocus on tests"
    )
    assert asyncio.run(expand_command(plain, "")).prompt == "Audit the repository."


def test_expand_command_returns_frontmatter_hints():
    command = CustomCommand(name="fix", body="Fix $1", mode="team", allowed_tools=("read_file",))

    expansion = asyncio.run(expand_command(command, "'the bug'"))

    assert expansion.prompt == "Fix the bug"
    assert expansion.mode == "team"
    assert expansion.allowed_tools == ("read_file",)
    assert expansion.rejected_injections == ()


# ---------------------------------------------------------------------------
# Shell injection !`cmd`
# ---------------------------------------------------------------------------


def test_expand_command_runs_shell_injection():
    command = CustomCommand(name="sh", body="Output: !`echo hi`")

    expansion = asyncio.run(expand_command(command, ""))

    assert expansion.prompt == "Output: hi"
    assert expansion.rejected_injections == ()


def test_expand_command_refuses_dangerous_injection():
    command = CustomCommand(name="nuke", body="Run: !`rm -rf /`")

    expansion = asyncio.run(expand_command(command, ""))

    assert expansion.prompt == "Run: [refused: rm -rf / — blocked by command guard]"
    assert expansion.rejected_injections == ("[refused: rm -rf / — blocked by command guard]",)


def test_expand_command_reports_failed_injection_without_raising():
    command = CustomCommand(name="boom", body="Try: !`exit 1`")

    expansion = asyncio.run(expand_command(command, ""))

    assert expansion.prompt == "Try: [failed: exit 1 — exit code 1]"
    assert expansion.rejected_injections == ("[failed: exit 1 — exit code 1]",)


# ---------------------------------------------------------------------------
# Regressions
# ---------------------------------------------------------------------------


def test_expand_command_old_format_command_unchanged(tmp_path):
    directory = tmp_path / "proj" / ".nexuscli" / "commands"
    _write_command(
        directory,
        "deploy.md",
        "---\ndescription: Ship to prod\n---\nDeploy $ARGUMENTS",
    )
    commands = load_slash_commands(str(tmp_path / "proj"), home=tmp_path / "home")

    expansion = asyncio.run(expand_command(commands["deploy"], "now"))

    assert expansion.prompt == "Deploy now"
    assert expansion.mode == ""
    assert expansion.allowed_tools == ()
