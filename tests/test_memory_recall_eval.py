"""Recall eval for multi-signal memory (spec 2026-10-27_memory-multi-signal-recall).

Table-driven top-K recall eval over the real recall chain
(``MemoryManager.recall`` -> ``_relevance_scores`` -> signal fusion):

- every query group must put its target entry inside the top-K under the
  default fused signals (lexical + BM25 + entity + temporal weighting);
- on the adversarial groups the *same batch* scored by the lexical signal
  alone must keep the target out of the top-K. The baseline is the signal
  isolation/degradation entry point ``MemoryManager(signals=[LexicalSignal()])``
  — no weight constant is touched, so the improvement is attributable to the
  extra signals, not to tuning;
- the ``examples/evals/04_*`` scenario is pinned to reality: replaying its
  scripted save/query arguments through the real ``save_memory`` /
  ``search_memory`` handlers must reproduce the frozen tool result, rank the
  target first, and miss the target under a lexical-only ranking (the
  adversarial framing the scenario description claims).
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from nexuscli.config import load_config
from nexuscli.memory import LexicalSignal, MemoryManager
from nexuscli.tools.base import ToolContext
from nexuscli.tools.builtins import save_memory, search_memory

TOP_K = 5
_DISTRACTOR_COUNT = 10
_DISTRACTOR_IMPORTANCE = 0.9
_TARGET_IMPORTANCE = 0.4

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCENARIO_PATH = _REPO_ROOT / "examples" / "evals" / "04_memory_recall_target_first.json"


@dataclass(frozen=True)
class RecallGroup:
    """One eval query group: a saved batch, the query, and the target contract."""

    name: str
    query: str
    # (content, save kwargs) in insertion order; the last entry is the target.
    entries: list[tuple[str, dict]] = field(default_factory=list)
    # True when the batch is built so a lexical-only ranking drops the target
    # out of the top-K (the multi-signal improvement is then observable).
    adversarial: bool = False


def _repeated(template: str, **kwargs) -> list[tuple[str, dict]]:
    return [
        (template.format(index=index), dict(kwargs)) for index in range(1, _DISTRACTOR_COUNT + 1)
    ]


GROUPS = [
    RecallGroup(
        name="entity-anchor",
        query="Bluefin Keycloak auth service configuration",
        entries=_repeated(
            "auth service configuration checklist for realm {index}",
            importance=_DISTRACTOR_IMPORTANCE,
        )
        + [
            (
                "Bluefin delegates its logins to Keycloak",
                {
                    "importance": _TARGET_IMPORTANCE,
                    "kind": "decision",
                    "entities": ["Bluefin", "Keycloak"],
                },
            )
        ],
        adversarial=True,
    ),
    RecallGroup(
        name="rare-term-bm25",
        query="quokka release checklist rollout",
        entries=_repeated(
            "release checklist rollout plan revision {index}",
            importance=_DISTRACTOR_IMPORTANCE,
        )
        + [("the quokka release ships on thursday", {"importance": _TARGET_IMPORTANCE})],
        adversarial=True,
    ),
    RecallGroup(
        name="temporal-intent",
        query="when will the queue service move to Ivory",
        entries=_repeated(
            "the queue service moves to ivory cluster node {index}",
            importance=_DISTRACTOR_IMPORTANCE,
        )
        + [
            (
                "the Ivory switch for our queue service is planned",
                {"importance": _TARGET_IMPORTANCE, "entities": ["Ivory"]},
            )
        ],
        adversarial=True,
    ),
    RecallGroup(
        name="cjk-paraphrase",
        query="数据库连接池的上限是多少",
        entries=[
            ("数据库备份在凌晨执行", {"importance": _DISTRACTOR_IMPORTANCE}),
            ("发布代号是苍鹭", {"importance": _DISTRACTOR_IMPORTANCE}),
            ("测试统一用 pytest 跑", {"importance": _DISTRACTOR_IMPORTANCE}),
            ("数据库连接池上限是二十", {"importance": _TARGET_IMPORTANCE}),
        ],
    ),
]


def _build_group(db_path: Path, group: RecallGroup) -> tuple[MemoryManager, MemoryManager, int]:
    """Save the batch once, then open the lexical-only baseline on the same db.

    Both managers see the identical rows, so any ranking difference comes from
    the signal set alone. ``mark_access=False`` everywhere keeps recall
    read-only, so the two rankings never perturb each other.
    """
    fused = MemoryManager(db_path, scope="project")
    for content, kwargs in group.entries:
        fused.save(content, **kwargs)
    lexical = MemoryManager(db_path, scope="project", signals=[LexicalSignal()])
    target_id = max(entry.id for entry in fused.list(limit=len(group.entries) + 1))
    return fused, lexical, target_id


def _rank(manager: MemoryManager, query: str, limit: int) -> list[int]:
    return [entry.id for entry in manager.recall(query, limit=limit, mark_access=False)]


# ---------------------------------------------------------------------------
# Top-K hit contract (fused signals, every group)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("group", GROUPS, ids=lambda group: group.name)
def test_top_k_hit_group_puts_the_target_inside_top_k(tmp_path, group: RecallGroup):
    fused, _lexical, target_id = _build_group(tmp_path / "memory.db", group)

    top = _rank(fused, group.query, TOP_K)

    assert target_id in top, f"{group.name}: target {target_id} not in top-{TOP_K}: {top}"


# ---------------------------------------------------------------------------
# Adversarial comparison: multi-signal vs lexical-only on the same batch
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "group", [group for group in GROUPS if group.adversarial], ids=lambda group: group.name
)
def test_adversarial_group_multi_signal_rescues_what_lexical_only_cannot(tmp_path, group):
    fused, lexical, target_id = _build_group(tmp_path / "memory.db", group)

    fused_top_k = _rank(fused, group.query, TOP_K)
    lexical_top_k = _rank(lexical, group.query, TOP_K)
    fused_rank = _rank(fused, group.query, len(group.entries) + 1).index(target_id) + 1
    lexical_rank = _rank(lexical, group.query, len(group.entries) + 1).index(target_id) + 1

    # The contract: pure lexical scoring keeps the target out of the top-K
    # (it shares too few query features with the query), fused scoring not
    # only reaches the top-K but takes rank 1.
    assert target_id not in lexical_top_k
    assert fused_top_k[0] == target_id
    assert fused_rank < lexical_rank


def test_every_adversarial_target_loses_lexically_for_a_content_reason(tmp_path):
    """The lexical-only miss must come from coverage, not from an empty match.

    Every batch entry must actually match the query lexically (positive
    lexical signal) — otherwise the baseline would be trivially weak and the
    comparison would prove nothing about ranking quality.
    """
    from nexuscli.memory.signals import SignalQuery, content_scores

    for group in (g for g in GROUPS if g.adversarial):
        fused, _lexical, _target = _build_group(tmp_path / f"{group.name}.db", group)
        entries = fused.list(limit=len(group.entries) + 1)
        scores = content_scores(SignalQuery.build(group.query), entries, [LexicalSignal()])
        assert all(score > 0 for score in scores), f"{group.name}: some entry has no lexical match"


# ---------------------------------------------------------------------------
# examples/evals/04 scenario: frozen result pinned to the real handlers
# ---------------------------------------------------------------------------


def test_eval_scenario_replay_reproduces_the_frozen_result_and_ranks_target_first(tmp_path):
    scenario = json.loads(_SCENARIO_PATH.read_text(encoding="utf-8"))
    config = load_config(project_root=tmp_path)
    context = ToolContext(cwd=str(tmp_path), config=config)

    saves: list[dict] = []
    searches: list[dict] = []
    for turn in scenario["scripted_turns"]:
        for call in turn.get("tool_calls", []):
            if call["name"] == "save_memory":
                saves.append(call["arguments"])
                assert not asyncio.run(save_memory(call["arguments"], context)).is_error
            elif call["name"] == "search_memory":
                searches.append(call["arguments"])
    assert saves and len(searches) == 1

    replayed = asyncio.run(search_memory(searches[0], context))
    assert not replayed.is_error
    frozen_result = next(
        tool["result"] for tool in scenario["tools"] if tool["name"] == "search_memory"
    )
    assert replayed.content == frozen_result

    # The target line leads the recall, and the scenario's final_contains
    # needles are key points OF that target line (the final answer can only
    # hit them because recall surfaced the target first).
    target_line = frozen_result.splitlines()[0]
    for needle in scenario["assertions"]["final_contains"]:
        assert needle in target_line

    # The description's adversarial claim: a lexical-only ranking at the same
    # query and limit returns only the distractors — the target is gone.
    lexical = MemoryManager(
        config.memory.long_term_db_path, scope=str(tmp_path), signals=[LexicalSignal()]
    )
    lexical_rows = lexical.recall(
        searches[0]["query"], limit=int(searches[0]["limit"]), mark_access=False
    )
    assert lexical_rows, "lexical-only baseline must still match the distractors"
    assert all(row.content != target_line.split("] ", 1)[1] for row in lexical_rows)
