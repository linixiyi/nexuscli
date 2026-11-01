"""Tests for the requirements availability filter (spec 2026-11-02 §2.3/§2.4).

Semantics adapted from OpenViking vikingbot ``skills.py`` ``_check_requirements``
(rewritten for this module's line-based parser): a skill declaring
``requirements: {bins: [...], env: [...]}`` with an unmet item — a bin that
does not resolve on PATH, or an env var that is not set — stays listed and
explicitly loadable but is excluded from the automatic Top-K candidates, and
``/skill list`` marks it unavailable with the missing items. Skills without
the field behave exactly as before.
"""

from __future__ import annotations

from pathlib import Path

from nexuscli.entrypoints.commands.skill import _skill_layers
from nexuscli.skill import SkillRegistry

_TEST_ENV = "NEXUSCLI_TEST_REQUIREMENTS_TOKEN"
_MISSING_BIN = "nexuscli-missing-bin-for-tests"


def _write_skill_md(root: Path, name: str, frontmatter: list[str], body: str) -> None:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n" + "\n".join(frontmatter) + "\n---\n" + body + "\n", encoding="utf-8"
    )


def _registry(tmp_path: Path, monkeypatch) -> SkillRegistry:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr("nexuscli.skill.registry.Path.home", lambda: home)
    return SkillRegistry(tmp_path, builtin_root=tmp_path / "builtin")


def test_requirements_parsed_and_availability_checked(tmp_path, monkeypatch):
    # Deterministic PATH check: only "git" resolves.
    monkeypatch.setattr(
        "nexuscli.skill.registry.shutil.which",
        lambda name: "/usr/bin/git" if name == "git" else None,
    )
    monkeypatch.delenv(_TEST_ENV, raising=False)

    _write_skill_md(
        tmp_path / ".nexuscli" / "skills",
        "needs",
        [
            "name: needs",
            "description: declares bins and env",
            "requirements: {bins: [git, " + _MISSING_BIN + "], env: [" + _TEST_ENV + "]}",
        ],
        "Body.",
    )
    registry = _registry(tmp_path, monkeypatch)

    skill = registry.load("needs")

    assert skill is not None  # loads fine; availability is a filter, not a gate
    assert skill.requirements_bins == ["git", _MISSING_BIN]
    assert skill.requirements_env == [_TEST_ENV]
    assert skill.missing_requirements == [f"bin: {_MISSING_BIN}", f"env: {_TEST_ENV}"]
    assert skill.unavailable is True

    # Meeting every requirement flips the verdict.
    monkeypatch.setenv(_TEST_ENV, "1")
    monkeypatch.setattr("nexuscli.skill.registry.shutil.which", lambda name: "/usr/bin/tool")
    registry.reload()
    met = registry.load("needs")
    assert met is not None
    assert met.missing_requirements == []
    assert met.unavailable is False


def test_requirements_parse_tolerates_unknown_keys_and_absence(tmp_path, monkeypatch):
    _write_skill_md(
        tmp_path / ".nexuscli" / "skills",
        "odd-reqs",
        [
            "name: odd-reqs",
            "description: unknown sub-keys are ignored",
            # Review regression: a hyphenated sub-key ending in bins/env
            # (missing-env, my-bins) must not be read as env/bins.
            "requirements: {bins: [git], missing-env: [X], my-bins: [y]}",
        ],
        "Body.",
    )
    _write_skill_md(
        tmp_path / ".nexuscli" / "skills",
        "no-reqs",
        ["name: no-reqs", "description: no requirements field"],
        "Body.",
    )
    registry = _registry(tmp_path, monkeypatch)

    odd = registry.load("odd-reqs")
    assert odd is not None
    assert odd.requirements_bins == ["git"]
    assert odd.requirements_env == []
    assert odd.missing_requirements == []  # git resolves on the test runner too

    plain = registry.load("no-reqs")
    assert plain is not None
    assert plain.requirements_bins == []
    assert plain.requirements_env == []
    assert plain.missing_requirements == []
    assert plain.unavailable is False


def test_unmet_requirements_excluded_from_candidates_but_listed_and_loadable(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "nexuscli.skill.registry.shutil.which",
        lambda name: "/usr/bin/git" if name == "git" else None,
    )
    monkeypatch.delenv(_TEST_ENV, raising=False)
    _write_skill_md(
        tmp_path / ".nexuscli" / "skills",
        "available-skill",
        ["name: available-skill", "description: deploy the release candidate"],
        "Body.",
    )
    _write_skill_md(
        tmp_path / ".nexuscli" / "skills",
        "missing-dep-skill",
        [
            "name: missing-dep-skill",
            "description: deploy the release candidate with extra tooling",
            f"requirements: {{bins: [{_MISSING_BIN}]}}",
        ],
        "Body.",
    )
    registry = _registry(tmp_path, monkeypatch)

    # Both match the query on description terms, but the one whose bin is
    # missing must not enter the candidate list.
    matched = [skill.name for skill in registry.match("deploy release candidate", top_k=5)]
    assert matched == ["available-skill"]

    # Still listed and explicitly loadable (spec §2.4: filtering is a
    # candidate-phase decision, not a load gate).
    assert [skill.name for skill in registry.all_skills()] == [
        "available-skill",
        "missing-dep-skill",
    ]
    assert registry.load("missing-dep-skill") is not None


def test_skill_list_marks_unavailable_with_missing_items(tmp_path, monkeypatch):
    monkeypatch.delenv(_TEST_ENV, raising=False)
    _write_skill_md(
        tmp_path / ".nexuscli" / "skills",
        "missing-dep-skill",
        [
            "name: missing-dep-skill",
            "description: needs tooling that is not installed",
            f"requirements: {{bins: [{_MISSING_BIN}], env: [{_TEST_ENV}]}}",
        ],
        "Body.",
    )
    registry = _registry(tmp_path, monkeypatch)

    skill = registry.load("missing-dep-skill")
    assert skill is not None
    assert _skill_layers(skill) == (
        f"L0:- L1:gen unavailable (missing: bin: {_MISSING_BIN} env: {_TEST_ENV})"
    )


def test_skills_without_requirements_keep_layer_column_unchanged(tmp_path, monkeypatch):
    _write_skill_md(
        tmp_path / ".nexuscli" / "skills",
        "plain",
        ["name: plain", "description: plain skill"],
        "Body paragraph.",
    )
    registry = _registry(tmp_path, monkeypatch)

    skill = registry.load("plain")
    assert skill is not None
    assert _skill_layers(skill) == "L0:- L1:gen"
