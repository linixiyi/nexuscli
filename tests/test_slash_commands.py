from __future__ import annotations

from nexuscli.entrypoints.repl import _match_custom_command
from nexuscli.entrypoints.slash_commands import (
    CustomCommand,
    expand_custom_command,
    load_slash_commands,
    split_command_message,
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

    match = _match_custom_command("/explain the session store", commands)

    assert match is not None
    assert match[0].name == "explain"
    assert match[1] == "Explain the session store step by step."

    assert _match_custom_command("/unknown-command hi", commands) is None
    assert _match_custom_command("plain message", commands) is None
