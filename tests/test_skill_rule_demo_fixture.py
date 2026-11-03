"""Regression tests for the narrowed ``rule-demo`` eval fixture skill.

Spec 2026-11-03_add-eval-history-and-skill-polish (rule-demo description
narrowing + candidate-matching regression): the fixture's frontmatter used to
carry a wide "real-track eval demo skill" description, so everyday technical
questions matched it in the Top-K candidates and the model polluted its
replies with the SKILL-RULE-OK marker (2026-11-03 self-test). The frontmatter
is now scenario-only; what must keep working:

- a general technical query scores 0 against the narrowed frontmatter (the
  old wide description scored 2 and ranked — the single-candidate pool below
  turns any widening back red deterministically);
- the committed scenario 03 prompt names the skill explicitly, so the
  explicit-name bonus keeps it the first candidate (the suite scenarios
  skc-sh-01/02 name it too);
- the load_skill builtin still resolves the committed fixture and injects
  the rule body into the next model turn — the same chain
  ``test_real_track_load_skill_reachable_and_rule_followed`` pins hermetically
  with an inline copy of the skill.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

from nexuscli.config import load_config
from nexuscli.skill import SkillContextBuffer, SkillRegistry, SkillStateStore
from nexuscli.tools.base import ToolContext
from nexuscli.tools.builtins import load_skill

REPO_ROOT = Path(__file__).resolve().parents[1]
COMMITTED_SKILL = REPO_ROOT / ".agents" / "skills" / "rule-demo" / "SKILL.md"
COMMITTED_SCENARIO = REPO_ROOT / "examples" / "evals" / "real" / "03_skill_rule_follow.json"

# The general technical query shape of the 2026-11-03 pollution self-test:
# everyday repo questions whose token set (real, py, 四象限键名 n-grams) has
# zero overlap with the narrowed frontmatter — no "eval", "fixture", "rule",
# "skill", or "demo" tokens in the query.
GENERAL_QUERIES = [
    "real.py 的四象限键名分别是什么？",
    "帮我看看 real.py 里四象限键名的定义",
]


def _fixture_registry(tmp_path: Path) -> SkillRegistry:
    """A registry whose only project skill is the committed fixture file.

    The committed SKILL.md is copied verbatim into an isolated project root
    (user/agent/builtin roots point at absent throwaway directories), so the
    score assertions see a single-candidate pool: ``match() == []`` iff the
    narrowed frontmatter scores zero for the query.
    """
    root = tmp_path / "project"
    fixture_dir = root / ".agents" / "skills" / "rule-demo"
    fixture_dir.mkdir(parents=True)
    shutil.copy(COMMITTED_SKILL, fixture_dir / "SKILL.md")
    for empty in ("builtin", "user-root", "user-agents"):
        (root / empty).mkdir()
    return SkillRegistry(
        root,
        builtin_root=root / "builtin",
        user_root=root / "user-root",
        user_agents_root=root / "user-agents",
        state_store=SkillStateStore(tmp_path / "skills.json"),
    )


def _repo_registry(tmp_path: Path) -> SkillRegistry:
    """The production-shaped registry: the repo checkout as project root.

    Only the state store is redirected (its default lands under HOME, which
    the conftest autouse fixture already isolates — the explicit store just
    keeps the test self-contained). Builtins stay in the pool exactly like a
    real run inside the repository.
    """
    return SkillRegistry(REPO_ROOT, state_store=SkillStateStore(tmp_path / "skills.json"))


def test_committed_fixture_frontmatter_is_scenario_only(tmp_path):
    registry = _fixture_registry(tmp_path)
    skill = registry.load("rule-demo")
    assert skill is not None

    # Narrowed frontmatter: scenario-only wording, single fixture tag, no
    # frontmatter lint issues introduced by the rewrite.
    assert skill.tags == ["eval-fixture"]
    assert "03_skill_rule_follow" in skill.description
    assert "not for general use" in skill.description
    assert skill.validation_issues == []

    # The SKILL.md body — the observable the scenario's final_contains
    # assertion judges — is untouched by the frontmatter rewrite.
    assert "Rule to follow once this skill is loaded" in skill.body
    assert "SKILL-RULE-OK" in skill.body

    # Zero-score pin on a single-candidate pool: any widening of the
    # description/tags that overlaps these general token sets re-ranks the
    # fixture and fails here (the old description scored 2 on the first).
    for query in GENERAL_QUERIES:
        assert registry.match(query, top_k=5) == []


def test_general_repo_queries_leave_fixture_out_of_candidates(tmp_path):
    registry = _repo_registry(tmp_path)
    for query in GENERAL_QUERIES:
        names = [skill.name for skill in registry.match(query, top_k=5)]
        assert "rule-demo" not in names, query


def test_committed_scenario03_prompt_ranks_fixture_first(tmp_path):
    """The explicit-name path: the committed prompt names rule-demo, so the
    +10_000 bonus keeps it the first Top-K candidate despite the narrowed
    description (this is why the narrowing is safe for scenario 03 and the
    skc-sh-01/02 suite scenarios, which name it explicitly too)."""
    prompt = json.loads(COMMITTED_SCENARIO.read_text(encoding="utf-8"))["prompt"]
    registry = _repo_registry(tmp_path)

    names = [skill.name for skill in registry.match(prompt, top_k=5)]

    assert names, "scenario 03 prompt must produce candidates"
    assert names[0] == "rule-demo"
    # ...and the load_skill resolution path can fetch it by that name.
    assert registry.load("rule-demo") is not None


def test_load_skill_builtin_injects_committed_fixture_rule(tmp_path):
    """The load_skill chain reaches the committed fixture end to end.

    The real builtin handler resolves the name against the repo checkout,
    reports the load, and the drained context buffer carries the rule body
    the scenario's final reply is judged on.
    """
    config = load_config(project_root=REPO_ROOT)
    buffer = SkillContextBuffer()
    context = ToolContext(cwd=str(REPO_ROOT), config=config, skill_context_buffer=buffer)

    result = asyncio.run(load_skill({"name": "rule-demo"}, context))

    assert not result.is_error
    assert 'Loaded skill "rule-demo" instructions' in result.content
    drained = buffer.drain()
    assert "## Loaded Skill: rule-demo" in drained
    assert "Rule to follow once this skill is loaded" in drained
    assert "SKILL-RULE-OK" in drained
