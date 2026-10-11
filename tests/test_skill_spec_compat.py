"""Tests for Agent Skills spec compatibility in the skill registry.

Covers:
- standard frontmatter fields ``allowed-tools`` and
  ``disable-model-invocation`` are parsed into :class:`Skill` (hyphen and
  underscore spellings, comma-separated and bracket list forms, boolean
  flag values);
- the Agent Skills standard directories ``~/.agents/skills`` and
  ``<project>/.agents/skills`` are scanned alongside the native ones, with
  the native directory winning a name conflict inside the same scope;
- ``disable-model-invocation`` skills are excluded from auto-matching
  (``match``) but still resolvable and loadable via ``load`` / load_skill;
- the load_skill tool surfaces the ``allowed-tools`` restriction to the
  model.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from nexuscli.config import load_config
from nexuscli.skill import SkillRegistry
from nexuscli.tools.base import ToolContext
from nexuscli.tools.builtins import load_skill


def _write_skill_file(
    root: Path,
    name: str,
    frontmatter: dict[str, str],
    body: str | None = None,
) -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    lines = ["---"]
    lines += [f"{key}: {value}" for key, value in frontmatter.items()]
    lines += ["---", body or f"body for {name}", ""]
    path = skill_dir / "SKILL.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _registry(project: Path, builtin: Path, home: Path, monkeypatch) -> SkillRegistry:
    monkeypatch.setattr("nexuscli.skill.registry.Path.home", lambda: home)
    return SkillRegistry(project, builtin_root=builtin)


# ----------------------------------------------------------------------
# Spec frontmatter fields
# ----------------------------------------------------------------------


def test_allowed_tools_and_disable_model_invocation_parsed(tmp_path, monkeypatch):
    project = tmp_path / "project"
    builtin = tmp_path / "builtin"
    home = tmp_path / "home"
    _write_skill_file(
        project / ".agents" / "skills",
        "git-review",
        {
            "name": "git-review",
            "description": "review a git diff",
            "allowed-tools": "Bash(git:*), Read",
            "disable-model-invocation": "true",
        },
    )
    registry = _registry(project, builtin, home, monkeypatch)

    skill = registry.load("git-review")
    assert skill is not None
    assert skill.allowed_tools == ["Bash(git:*)", "Read"]
    assert skill.disable_model_invocation is True


def test_spec_fields_accept_underscore_spelling_and_defaults(tmp_path, monkeypatch):
    project = tmp_path / "project"
    builtin = tmp_path / "builtin"
    home = tmp_path / "home"
    _write_skill_file(
        project / ".agents" / "skills",
        "underscored",
        {
            "name": "underscored",
            "description": "spec fields with underscore keys",
            "allowed_tools": "[Bash, Read]",
            "disable_model_invocation": "yes",
        },
    )
    _write_skill_file(
        project / ".agents" / "skills",
        "plain",
        {"name": "plain", "description": "no spec fields"},
    )
    registry = _registry(project, builtin, home, monkeypatch)

    underscored = registry.load("underscored")
    assert underscored is not None
    assert underscored.allowed_tools == ["Bash", "Read"]
    assert underscored.disable_model_invocation is True

    plain = registry.load("plain")
    assert plain is not None
    assert plain.allowed_tools == []
    assert plain.disable_model_invocation is False


def test_disable_flag_rejects_non_true_values(tmp_path, monkeypatch):
    project = tmp_path / "project"
    builtin = tmp_path / "builtin"
    home = tmp_path / "home"
    _write_skill_file(
        project / ".agents" / "skills",
        "false-flag",
        {
            "name": "false-flag",
            "description": "flag set to false stays auto-invocable",
            "disable-model-invocation": "false",
        },
    )
    registry = _registry(project, builtin, home, monkeypatch)

    skill = registry.load("false-flag")
    assert skill is not None
    assert skill.disable_model_invocation is False


# ----------------------------------------------------------------------
# Standard directories
# ----------------------------------------------------------------------


def test_user_standard_directory_scanned(tmp_path, monkeypatch):
    project = tmp_path / "project"
    builtin = tmp_path / "builtin"
    home = tmp_path / "home"
    _write_skill_file(
        home / ".agents" / "skills",
        "community-pack",
        {"name": "community-pack", "description": "from the ecosystem pack"},
    )
    registry = _registry(project, builtin, home, monkeypatch)

    skill = registry.load("community-pack")
    assert skill is not None
    assert skill.source == "user"
    assert skill.path == home / ".agents" / "skills" / "community-pack" / "SKILL.md"


def test_project_standard_directory_scanned(tmp_path, monkeypatch):
    project = tmp_path / "project"
    builtin = tmp_path / "builtin"
    home = tmp_path / "home"
    _write_skill_file(
        project / ".agents" / "skills",
        "team-workflow",
        {"name": "team-workflow", "description": "checked into the repo"},
    )
    registry = _registry(project, builtin, home, monkeypatch)

    skill = registry.load("team-workflow")
    assert skill is not None
    assert skill.source == "project"


def test_native_directory_wins_within_scope(tmp_path, monkeypatch):
    project = tmp_path / "project"
    builtin = tmp_path / "builtin"
    home = tmp_path / "home"
    _write_skill_file(
        project / ".agents" / "skills",
        "dup",
        {"name": "dup", "description": "standard dir copy"},
    )
    _write_skill_file(
        project / ".nexuscli" / "skills",
        "dup",
        {"name": "dup", "description": "native copy"},
    )
    registry = _registry(project, builtin, home, monkeypatch)

    skill = registry.load("dup")
    assert skill is not None
    assert skill.description == "native copy"


# ----------------------------------------------------------------------
# Auto-invocation gate
# ----------------------------------------------------------------------


def test_disable_model_invocation_excluded_from_match(tmp_path, monkeypatch):
    project = tmp_path / "project"
    builtin = tmp_path / "builtin"
    home = tmp_path / "home"
    _write_skill_file(
        project / ".agents" / "skills",
        "manual-only",
        {
            "name": "manual-only",
            "description": "review git diffs on request",
            "disable-model-invocation": "true",
        },
    )
    _write_skill_file(
        project / ".agents" / "skills",
        "auto-ok",
        {"name": "auto-ok", "description": "review git diffs automatically"},
    )
    registry = _registry(project, builtin, home, monkeypatch)

    candidates = registry.match("please review the git diff", top_k=5)
    names = [skill.name for skill in candidates]
    assert "manual-only" not in names
    assert "auto-ok" in names
    # Explicit loads keep working.
    assert registry.load("manual-only") is not None
    assert registry.enabled_skills()  # it is enabled, just not auto-invoked


# ----------------------------------------------------------------------
# load_skill surfaces allowed-tools
# ----------------------------------------------------------------------


def test_load_skill_tool_appends_allowed_tools_note(tmp_path, monkeypatch):
    project = tmp_path / "project"
    builtin = tmp_path / "builtin"
    home = tmp_path / "home"
    (home / ".nexuscli").mkdir(parents=True)
    monkeypatch.setattr("nexuscli.config._home", lambda: home)
    _write_skill_file(
        project / ".agents" / "skills",
        "git-review",
        {
            "name": "git-review",
            "description": "review a git diff",
            "allowed-tools": "Bash(git:*), Read",
        },
        body="Review the diff carefully.",
    )
    SkillRegistry(project, builtin_root=builtin)  # populate the registry for load_skill
    config = load_config(project_root=project, env={})
    context = ToolContext(cwd=str(project), config=config)

    result = asyncio.run(load_skill({"name": "git-review"}, context))

    assert not result.is_error
    assert "Review the diff carefully." in result.content
    assert "[Allowed tools while this skill is active] Bash(git:*), Read" in result.content


def test_load_skill_tool_without_allowed_tools_is_unchanged(tmp_path, monkeypatch):
    project = tmp_path / "project"
    builtin = tmp_path / "builtin"
    home = tmp_path / "home"
    (home / ".nexuscli").mkdir(parents=True)
    monkeypatch.setattr("nexuscli.config._home", lambda: home)
    _write_skill_file(
        project / ".agents" / "skills",
        "plain-skill",
        {"name": "plain-skill", "description": "no tool restrictions"},
        body="Plain body.",
    )
    SkillRegistry(project, builtin_root=builtin)  # populate the registry for load_skill
    config = load_config(project_root=project, env={})
    context = ToolContext(cwd=str(project), config=config)

    result = asyncio.run(load_skill({"name": "plain-skill"}, context))

    assert not result.is_error
    assert "Allowed tools" not in result.content
