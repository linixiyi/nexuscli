"""Tests for the L0/L1/L2 layered skill loading (spec
2026-11-02_add-skill-layered-loading).

Covers:
- the registry parses optional frontmatter ``abstract`` (L0) and ``overview``
  (L1), auto-generates the overview from the body's first paragraph when
  absent (marked generated), and old SKILL.md files without the new fields
  behave exactly as before (backward compatibility);
- the load_skill tool's optional ``level`` parameter: ``full`` (default) is
  byte-for-byte compatible with the pre-layer tool, ``overview`` returns the
  L1 text with a generated marker and layer info in the result;
- the agent candidate line prefers the abstract when present and falls back
  to the description otherwise, while Top-K matching keeps scoring the
  description field;
- ``/skill list`` shows the layer column (L0 presence, L1 declared/generated);
- the frontmatter validation rules adapted from OpenViking's skill_loader
  (required keys, name format, length caps) are recorded without changing
  tolerant loading, and unknown keys are tolerated.
"""

from __future__ import annotations

import asyncio
import io
from pathlib import Path

from rich.console import Console

from nexuscli.agent.agent import _prepend_skill_candidates
from nexuscli.config import load_config
from nexuscli.entrypoints.commands.skill import _skill_command, _skill_layers
from nexuscli.skill import SkillContextBuffer, SkillRegistry
from nexuscli.tools.base import ToolContext
from nexuscli.tools.builtins import load_skill


def _write_skill_md(root: Path, name: str, frontmatter: list[str], body: str) -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    path = skill_dir / "SKILL.md"
    path.write_text("---\n" + "\n".join(frontmatter) + "\n---\n" + body + "\n", encoding="utf-8")
    return path


def _project_registry(tmp_path: Path, monkeypatch) -> SkillRegistry:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr("nexuscli.skill.registry.Path.home", lambda: home)
    return SkillRegistry(tmp_path, builtin_root=tmp_path / "builtin")


# ----------------------------------------------------------------------
# Registry: L0/L1 parsing, generation, fallbacks
# ----------------------------------------------------------------------


def test_explicit_abstract_and_overview_parsed(tmp_path, monkeypatch):
    _write_skill_md(
        tmp_path / ".nexuscli" / "skills",
        "layered",
        [
            "name: layered",
            "description: the matching description",
            "abstract: L0 candidate one-liner",
            "overview: L1 one-screen condensation",
        ],
        "# Layered\n\nBody intro paragraph.\n\n## Steps\n\nDo things.",
    )
    registry = _project_registry(tmp_path, monkeypatch)

    skill = registry.load("layered")

    assert skill is not None
    assert skill.abstract == "L0 candidate one-liner"
    assert skill.overview == "L1 one-screen condensation"
    assert skill.overview_generated is False
    assert skill.validation_issues == []


def test_overview_generated_from_first_paragraph_before_section(tmp_path, monkeypatch):
    _write_skill_md(
        tmp_path / ".nexuscli" / "skills",
        "auto-l1",
        ["name: auto-l1", "description: generated overview source"],
        "# Auto L1\n\nIntro paragraph explains the workflow.\n\n## Step One\n\nDetails here.",
    )
    registry = _project_registry(tmp_path, monkeypatch)

    skill = registry.load("auto-l1")

    assert skill is not None
    # 首段：H1 title skipped, paragraph before the first ## heading.
    assert skill.overview == "Intro paragraph explains the workflow."
    assert skill.overview_generated is True


def test_generated_overview_falls_back_past_opening_section(tmp_path, monkeypatch):
    _write_skill_md(
        tmp_path / ".nexuscli" / "skills",
        "opens-section",
        ["name: opens-section", "description: body opens with a section"],
        "## Steps\n\nFirst instruction line.\n\nMore detail.",
    )
    registry = _project_registry(tmp_path, monkeypatch)

    skill = registry.load("opens-section")

    assert skill is not None
    assert skill.overview == "First instruction line."
    assert skill.overview_generated is True


def test_generated_overview_truncated_at_500_chars(tmp_path, monkeypatch):
    long_paragraph = "word " * 150  # 750 chars
    _write_skill_md(
        tmp_path / ".nexuscli" / "skills",
        "long-body",
        ["name: long-body", "description: long body"],
        long_paragraph,
    )
    registry = _project_registry(tmp_path, monkeypatch)

    skill = registry.load("long-body")

    assert skill is not None
    assert len(skill.overview) == 500
    assert skill.overview.endswith("...")


def test_old_skill_without_new_fields_keeps_old_behavior(tmp_path, monkeypatch):
    _write_skill_md(
        tmp_path / ".nexuscli" / "skills",
        "old-style",
        ["name: old-style", "description: legacy skill description"],
        "# Old Style\n\nLegacy body paragraph.\n\n## Steps\n\nLegacy steps.",
    )
    registry = _project_registry(tmp_path, monkeypatch)

    skill = registry.load("old-style")

    assert skill is not None
    # L0 absent: consumers fall back to the description.
    assert skill.abstract == ""
    assert skill.description == "legacy skill description"
    # L1 falls back to the generated body condensation, marked generated.
    assert skill.overview == "Legacy body paragraph."
    assert skill.overview_generated is True
    assert skill.validation_issues == []
    # The full body is untouched.
    assert "Legacy steps." in skill.body


# ----------------------------------------------------------------------
# load_skill: level parameter
# ----------------------------------------------------------------------


def _tool_context(tmp_path: Path, buffer=None) -> ToolContext:
    config = load_config(project_root=tmp_path)
    return ToolContext(cwd=str(tmp_path), config=config, skill_context_buffer=buffer)


def test_load_skill_level_full_is_byte_compatible(tmp_path, monkeypatch):
    body = "demo body line one\nline two"
    _write_skill_md(
        tmp_path / ".nexuscli" / "skills",
        "demo",
        ["name: demo", "description: demo desc"],
        body,
    )
    _project_registry(tmp_path, monkeypatch)

    # Direct return, level omitted (the pre-layer call shape).
    result = asyncio.run(load_skill({"name": "demo"}, _tool_context(tmp_path)))
    assert not result.is_error
    assert result.content == body
    assert result.display_summary == "Loaded skill demo"

    # Explicit level=full is the identical call.
    explicit = asyncio.run(load_skill({"name": "demo", "level": "full"}, _tool_context(tmp_path)))
    assert explicit.content == result.content
    assert explicit.display_summary == result.display_summary

    # Buffer path: same return text and same pushed body as before layers.
    skill_buffer = SkillContextBuffer()
    buffered = asyncio.run(load_skill({"name": "demo"}, _tool_context(tmp_path, skill_buffer)))
    assert buffered.content == 'Loaded skill "demo" instructions for the next model turn.'
    assert buffered.display_summary == "Loaded skill demo"
    assert skill_buffer.drain() == f"## Loaded Skill: demo\n{body}"


def test_load_skill_level_full_truncates_long_bodies(tmp_path, monkeypatch):
    body = "x" * 6_000
    _write_skill_md(
        tmp_path / ".nexuscli" / "skills",
        "long",
        ["name: long", "description: long body"],
        body,
    )
    _project_registry(tmp_path, monkeypatch)

    result = asyncio.run(load_skill({"name": "long"}, _tool_context(tmp_path)))

    expected = body[:5_000] + "\n... [truncated; use /skill show for the full skill]"
    assert result.content == expected


def test_load_skill_level_overview_returns_layer_info(tmp_path, monkeypatch):
    _write_skill_md(
        tmp_path / ".nexuscli" / "skills",
        "layered",
        [
            "name: layered",
            "description: the matching description",
            "overview: L1 condensation text",
        ],
        "Full body that stays out of the overview response.",
    )
    _project_registry(tmp_path, monkeypatch)

    result = asyncio.run(
        load_skill({"name": "layered", "level": "overview"}, _tool_context(tmp_path))
    )

    assert not result.is_error
    # Declared overview: no generated marker, body excluded.
    assert result.content == "L1 condensation text"
    assert "generated" not in result.content
    assert "Full body" not in result.content
    assert result.display_summary == "Loaded skill layered (overview)"

    # Buffer path: layer info in the return message, overview in the buffer.
    skill_buffer = SkillContextBuffer()
    buffered = asyncio.run(
        load_skill({"name": "layered", "level": "overview"}, _tool_context(tmp_path, skill_buffer))
    )
    assert buffered.content == 'Loaded skill "layered" overview (L1) for the next model turn.'
    assert buffered.display_summary == "Loaded skill layered (overview)"
    drained = skill_buffer.drain()
    assert "## Loaded Skill: layered" in drained
    assert "L1 condensation text" in drained


def test_load_skill_level_overview_marks_generated(tmp_path, monkeypatch):
    _write_skill_md(
        tmp_path / ".nexuscli" / "skills",
        "auto-l1",
        ["name: auto-l1", "description: generated overview"],
        "# Auto L1\n\nGenerated intro paragraph.\n\n## Steps\n\nDetails.",
    )
    _project_registry(tmp_path, monkeypatch)

    result = asyncio.run(
        load_skill({"name": "auto-l1", "level": "overview"}, _tool_context(tmp_path))
    )

    assert not result.is_error
    assert result.content == (
        "Generated intro paragraph.\n[overview; generated from the skill body]"
    )


def test_load_skill_rejects_unknown_level(tmp_path, monkeypatch):
    _write_skill_md(
        tmp_path / ".nexuscli" / "skills",
        "demo",
        ["name: demo", "description: demo desc"],
        "demo body",
    )
    _project_registry(tmp_path, monkeypatch)

    result = asyncio.run(load_skill({"name": "demo", "level": "abstract"}, _tool_context(tmp_path)))

    assert result.is_error
    assert 'level must be "full" or "overview"' in result.content


# ----------------------------------------------------------------------
# Agent candidate line: abstract replaces description, matching unchanged
# ----------------------------------------------------------------------


def test_candidate_line_prefers_abstract_and_matching_keeps_description(tmp_path, monkeypatch):
    _write_skill_md(
        tmp_path / ".nexuscli" / "skills",
        "deploy-rollout",
        [
            "name: deploy-rollout",
            "description: ship the release candidate to production safely",
            "abstract: Zero-downtime deploy checklist",
        ],
        "Deploy body.",
    )
    _write_skill_md(
        tmp_path / ".nexuscli" / "skills",
        "plain-skill",
        ["name: plain-skill", "description: plain fallback description text"],
        "Plain body.",
    )
    _project_registry(tmp_path, monkeypatch)
    config = load_config(project_root=tmp_path)

    # Top-K matching still scores the description field: the query terms only
    # exist in the description, not in the abstract.
    matched = SkillRegistry(tmp_path).match("ship release production", top_k=1)
    assert [skill.name for skill in matched] == ["deploy-rollout"]

    text = _prepend_skill_candidates("ship release production", str(tmp_path), config)
    assert "- deploy-rollout: Zero-downtime deploy checklist" in text
    assert "ship the release candidate to production safely" not in text
    # No abstract: the description stays in the candidate line.
    plain_text = _prepend_skill_candidates("plain fallback description", str(tmp_path), config)
    assert "- plain-skill: plain fallback description text" in plain_text
    assert "Zero-downtime deploy checklist" not in plain_text


# ----------------------------------------------------------------------
# /skill list: layer column
# ----------------------------------------------------------------------


def _console() -> Console:
    return Console(file=io.StringIO(), width=250)


def _console_text(console: Console) -> str:
    return console.file.getvalue()


def test_skill_list_shows_layer_info(tmp_path, monkeypatch):
    _write_skill_md(
        tmp_path / ".nexuscli" / "skills",
        "layered",
        [
            "name: layered",
            "description: explicit layers",
            "abstract: L0 line",
            "overview: L1 line",
        ],
        "Body.",
    )
    _write_skill_md(
        tmp_path / ".nexuscli" / "skills",
        "generated-only",
        ["name: generated-only", "description: no explicit layers"],
        "Body paragraph for generation.",
    )
    registry = _project_registry(tmp_path, monkeypatch)

    # The layer helper is exact per skill (rich may reformat the table text).
    layered = registry.load("layered")
    generated_only = registry.load("generated-only")
    assert _skill_layers(layered) == "L0:+ L1:+"
    assert _skill_layers(generated_only) == "L0:- L1:gen"

    console = _console()
    _skill_command("list", console, str(tmp_path), None)
    text = _console_text(console)
    assert "L0:+ L1:+" in text
    assert "L0:- L1:gen" in text
    assert "explicit layers" in text  # description column still present


# ----------------------------------------------------------------------
# Frontmatter validation rules (adapted from OpenViking skill_loader)
# ----------------------------------------------------------------------


def test_validation_rules_recorded_without_blocking_load(tmp_path, monkeypatch):
    _write_skill_md(
        tmp_path / ".nexuscli" / "skills",
        "rules-skill",
        ["description: description only, no name key"],
        "Body.",
    )
    registry = _project_registry(tmp_path, monkeypatch)

    skill = registry.load("rules-skill")

    assert skill is not None  # tolerant: still loads with the dir-name fallback
    assert skill.name == "rules-skill"
    rules = {issue.split(":", 1)[0] for issue in skill.validation_issues}
    assert "name_required" in rules
    # The recorded issues surface in /skill list's layer column as warn codes.
    assert _skill_layers(skill) == "L0:- L1:gen warn:name_required"


def test_validation_records_name_format_and_directory_mismatch(tmp_path, monkeypatch):
    _write_skill_md(
        tmp_path / ".nexuscli" / "skills",
        "dir-name",
        ["name: Bad Name!", "description: bad charset and mismatch"],
        "Body.",
    )
    registry = _project_registry(tmp_path, monkeypatch)

    skill = registry.load("Bad Name!")

    assert skill is not None  # loads; issues are recorded, not fatal
    rules = {issue.split(":", 1)[0] for issue in skill.validation_issues}
    assert "name_allowed_characters" in rules
    assert "name_matches_directory" in rules
    assert any(
        "does not match directory name 'dir-name'" in issue for issue in skill.validation_issues
    )


def test_validation_records_description_and_body_limits(tmp_path, monkeypatch):
    _write_skill_md(
        tmp_path / ".nexuscli" / "skills",
        "too-long",
        ["name: too-long", "description: " + "x" * 1100],  # > 1024 chars
        "\n".join(f"line {i}" for i in range(600)),  # > 500 lines
    )
    registry = _project_registry(tmp_path, monkeypatch)

    skill = registry.load("too-long")

    assert skill is not None
    rules = {issue.split(":", 1)[0] for issue in skill.validation_issues}
    assert "description_max_length" in rules
    assert "body_max_lines" in rules


def test_validation_tolerates_unknown_keys_and_clean_skills(tmp_path, monkeypatch):
    _write_skill_md(
        tmp_path / ".nexuscli" / "skills",
        "clean",
        ["name: clean", "description: clean skill", "future-field: whatever"],
        "Body.",
    )
    registry = _project_registry(tmp_path, monkeypatch)

    skill = registry.load("clean")

    assert skill is not None
    assert skill.validation_issues == []  # unknown keys are tolerated silently


# ----------------------------------------------------------------------
# Builtin skill ships the layers as the reference example
# ----------------------------------------------------------------------


def test_builtin_web_access_ships_explicit_layers(tmp_path):
    registry = SkillRegistry(tmp_path)

    skill = registry.load("web-access")

    assert skill is not None
    assert skill.abstract
    assert skill.overview
    assert skill.overview_generated is False
    assert skill.validation_issues == []
