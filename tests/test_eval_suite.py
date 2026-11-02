"""Consistency tests for the ``examples/evals/suite`` real-track scenario set.

The suite is a flat directory of per-scenario JSON files (30 of them: five
categories x three difficulties x two scenarios) plus a manifest at
``examples/evals/suite/meta/manifest.json``.

WHERE THE MANIFEST MAY LIVE — do not move it back to any directory top level.
Both directory loaders glob ``*.json`` NON-recursively, so the manifest is
only safe inside a subdirectory:

- ``examples/evals/`` (the top level itself) is the hermetic track's built-in
  scenario directory: ``builtin_eval_dir()`` (src/nexuscli/eval/loader.py)
  resolves there, and ``eval list`` / ``eval run`` load every top-level
  ``*.json`` as a hermetic scenario. A manifest placed at
  ``examples/evals/suite-manifest.json`` makes both commands fail loudly with
  ``EvalSchemaError`` (observed: ``unknown scenario key(s) ['categories',
  'language', 'scenarios']``, exit 1).
- ``examples/evals/suite/`` is the real track's directory: loading it via
  ``load_real_eval_scenarios`` would swallow a top-level manifest the same
  way.

``suite/meta/`` (like ``suite/fixtures*/``) is immune to both globs. If you
move the manifest anywhere else, keep it out of both glob roots.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from nexuscli.eval import load_real_eval_scenarios

REPO_ROOT = Path(__file__).resolve().parents[1]
SUITE_DIR = REPO_ROOT / "examples" / "evals" / "suite"
MANIFEST_PATH = SUITE_DIR / "meta" / "manifest.json"

EXPECTED_TOTAL = 30
EXPECTED_CATEGORIES = (
    "codebase-qa",
    "repo-facts",
    "diagnosis",
    "skill-compliance",
    "extraction",
)
EXPECTED_DIFFICULTIES = ("short", "medium", "long")
PER_CELL = 2  # scenarios per (category, difficulty) cell
FORBIDDEN_TRIPLE = ["bash", "write_file", "edit_file"]

# Scenario file name stem prefix -> manifest category (author-chosen short codes).
CATEGORY_BY_FILE_PREFIX = {
    "cqa": "codebase-qa",
    "rfa": "repo-facts",
    "dgn": "diagnosis",
    "skc": "skill-compliance",
    "ext": "extraction",
}

# Scenario file name prefix -> manifest difficulty.
DIFFICULTY_BY_PREFIX = {"-sh-": "short", "-md-": "medium", "-lg-": "long"}

# Difficulty -> max_turns as used uniformly by every suite author.
MAX_TURNS_BY_DIFFICULTY = {"short": 3, "medium": 5, "long": 8}

_CJK = re.compile(r"[\u4e00-\u9fff]")


@pytest.fixture(scope="module")
def scenarios():
    return load_real_eval_scenarios(SUITE_DIR)


@pytest.fixture(scope="module")
def manifest():
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _by_name(scenarios):
    return {s.name: s for s in scenarios}


def _manifest_by_id(manifest):
    return {entry["id"]: entry for entry in manifest["scenarios"]}


# ---------------------------------------------------------------------------
# 1. Directory loading yields exactly 30 scenarios
# ---------------------------------------------------------------------------


def test_suite_directory_loads_exactly_30_scenarios(scenarios):
    assert len(scenarios) == EXPECTED_TOTAL


def test_fixture_subdirectories_are_not_loaded_as_scenarios(scenarios):
    """``fixtures/`` holds frozen data files; the non-recursive ``*.json`` glob
    must never surface any of them as scenarios."""
    for scenario in scenarios:
        assert not scenario.name.startswith("fixtures"), scenario.name
    fixture_dirs = [p for p in SUITE_DIR.iterdir() if p.is_dir()]
    assert fixture_dirs, "expected the fixtures/ tree to exist alongside scenarios"


# ---------------------------------------------------------------------------
# 2. Global name uniqueness
# ---------------------------------------------------------------------------


def test_scenario_names_globally_unique(scenarios):
    names = [s.name for s in scenarios]
    assert len(names) == len(set(names))


# ---------------------------------------------------------------------------
# 3. Manifest <-> scenario name alignment (both directions)
# ---------------------------------------------------------------------------


def test_manifest_ids_align_with_scenario_names_both_ways(scenarios, manifest):
    scenario_names = set(_by_name(scenarios))
    manifest_ids = set(_manifest_by_id(manifest))
    assert manifest_ids == scenario_names, (
        f"manifest-only ids: {sorted(manifest_ids - scenario_names)}; "
        f"scenario-only names: {sorted(scenario_names - manifest_ids)}"
    )


def test_manifest_categories_cover_all_scenarios(scenarios, manifest):
    by_id = _manifest_by_id(manifest)
    for scenario in scenarios:
        stem_prefix = scenario.name.split("-")[0]
        assert stem_prefix in CATEGORY_BY_FILE_PREFIX, scenario.name
        entry = by_id[scenario.name]
        assert entry["category"] == CATEGORY_BY_FILE_PREFIX[stem_prefix], scenario.name


# ---------------------------------------------------------------------------
# 4. 5 categories x 3 difficulties x 2 scenarios grid
# ---------------------------------------------------------------------------


def _difficulty_from_file_name(name: str) -> str:
    for prefix, difficulty in DIFFICULTY_BY_PREFIX.items():
        if prefix in name:
            return difficulty
    raise AssertionError(f"scenario name carries no difficulty prefix: {name}")


def test_grid_full_and_prefixes_match_manifest(scenarios, manifest):
    by_id = _manifest_by_id(manifest)
    cells: dict[tuple[str, str], list[str]] = {}
    for scenario in scenarios:
        difficulty = _difficulty_from_file_name(scenario.name)
        entry = by_id[scenario.name]
        assert entry["difficulty"] == difficulty, scenario.name
        assert entry["difficulty"] in EXPECTED_DIFFICULTIES
        cells.setdefault((entry["category"], difficulty), []).append(scenario.name)
    for category in EXPECTED_CATEGORIES:
        for difficulty in EXPECTED_DIFFICULTIES:
            names = cells.get((category, difficulty), [])
            assert len(names) == PER_CELL, (
                f"cell ({category}, {difficulty}) has {len(names)} scenarios: {names}"
            )
    assert sum(len(v) for v in cells.values()) == EXPECTED_TOTAL


def test_max_turns_follows_difficulty_mapping(scenarios, manifest):
    by_id = _manifest_by_id(manifest)
    for scenario in scenarios:
        difficulty = _difficulty_from_file_name(scenario.name)
        assert scenario.assertions.max_turns == MAX_TURNS_BY_DIFFICULTY[difficulty], scenario.name
        assert (
            by_id[scenario.name]["assertions"]["max_turns"] == (MAX_TURNS_BY_DIFFICULTY[difficulty])
        ), scenario.name


# ---------------------------------------------------------------------------
# 5. Mechanical leak check: anchors never appear in their own prompt
# ---------------------------------------------------------------------------


def test_no_final_contains_anchor_leaks_into_prompt(scenarios):
    """The answer anchors must be produced by reasoning over the fixture, not
    quoted back from the prompt. Case-sensitive byte substring check."""
    for scenario in scenarios:
        anchors = scenario.assertions.final_contains or []
        for needle in anchors:
            assert needle not in scenario.prompt, (
                f"{scenario.name}: anchor {needle!r} leaks into the prompt"
            )


# ---------------------------------------------------------------------------
# 6. Uniform forbidden-tools write guard
# ---------------------------------------------------------------------------


def test_forbidden_tools_uniform_triple(scenarios):
    for scenario in scenarios:
        assert scenario.assertions.forbidden_tools == FORBIDDEN_TRIPLE, scenario.name


# ---------------------------------------------------------------------------
# 7. Prompts are Chinese
# ---------------------------------------------------------------------------


def test_prompts_contain_chinese(scenarios):
    for scenario in scenarios:
        assert _CJK.search(scenario.prompt), f"{scenario.name}: prompt has no CJK text"


# ---------------------------------------------------------------------------
# 8. Manifest provenance is populated
# ---------------------------------------------------------------------------


def test_manifest_source_rationale_non_empty(manifest):
    for entry in manifest["scenarios"]:
        rationale = entry.get("source_rationale")
        assert isinstance(rationale, str) and rationale.strip(), entry["id"]


# ---------------------------------------------------------------------------
# 9. Manifest assertion summary mirrors the scenario file
# ---------------------------------------------------------------------------


def test_manifest_assertion_summary_matches_scenario(scenarios, manifest):
    by_name = _by_name(scenarios)
    for entry in manifest["scenarios"]:
        scenario = by_name[entry["id"]]
        summary = entry["assertions"]
        assert summary["forbidden"] == (scenario.assertions.forbidden_tools or [])
        assert summary["has_judge"] == (scenario.judge is not None)
        assert summary["max_turns"] == scenario.assertions.max_turns
        assert summary["required_tools"] == (scenario.assertions.required_tools or [])
        assert summary.get("final_contains", []) == (scenario.assertions.final_contains or [])
        if scenario.judge is not None:
            assert summary["judge_threshold"] == pytest.approx(scenario.judge.threshold)
        else:
            assert "judge_threshold" not in summary
