"""Multi-signal memory recall (spec 2026-10-27_memory-multi-signal-recall).

Pins the signal layer and its fusion: each of the three default signals fires
on its own, the fused ranking beats the lexical-only baseline on an adversarial
batch, the temporal layer migrates and weights recall by query intent, the
vector slot is absent by default, the ported hotness function keeps its
boundary values, and the ADD-only write path never redefines a saved fact.
"""

from __future__ import annotations

import asyncio
import io
import math
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from rich.console import Console

from nexuscli.config import NexusCliConfig, load_config
from nexuscli.entrypoints.repl import _handle_slash, _parse_save_options
from nexuscli.memory import (
    BM25Signal,
    EntitySignal,
    LexicalSignal,
    MemoryEntry,
    MemoryManager,
    VectorSignal,
    default_signals,
    detect_temporal_intent,
    extract_entities,
    hotness_score,
)
from nexuscli.memory.manager import _hash_content
from nexuscli.memory.signals import (
    CONTENT_BUDGET,
    TEMPORAL_MISMATCH_MULTIPLIER,
    WEIGHT_BM25,
    WEIGHT_CONFIDENCE,
    WEIGHT_ENTITY,
    WEIGHT_HOTNESS,
    WEIGHT_IMPORTANCE,
    WEIGHT_LEXICAL,
    SignalQuery,
    content_scores,
    derive_temporal_kind,
)
from nexuscli.prompt import PromptAssembler
from nexuscli.rag.code_index import BM25_B, BM25_K1, CodeIndex, bm25_score
from nexuscli.tools.base import ToolContext
from nexuscli.tools.builtins import get_builtin_tools, save_memory, search_memory

NOW = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)


def _entry(memory_id: int, content: str, **overrides) -> MemoryEntry:
    fields = {
        "id": memory_id,
        "scope": "project",
        "content": content,
        "created_at": NOW.isoformat(),
        "updated_at": NOW.isoformat(),
    }
    fields.update(overrides)
    return MemoryEntry(**fields)


# ---------------------------------------------------------------------------
# Each default signal hits on its own
# ---------------------------------------------------------------------------


def test_lexical_signal_scores_coverage_and_substring_bonus():
    query = SignalQuery.build("memory retrieval")
    entries = [
        _entry(1, "memory retrieval uses keyword matching"),
        _entry(2, "retrieval only"),
        _entry(3, "completely unrelated phrase"),
    ]

    scores = LexicalSignal().score(query, entries)

    # Full coverage plus the whole-query substring bonus caps at 1.0; half
    # coverage without the bonus is 0.4; no overlap is a hard zero.
    assert scores == [pytest.approx(1.0), pytest.approx(0.4), 0.0]


def test_bm25_signal_prefers_rare_terms_and_never_touches_the_code_index(tmp_path, monkeypatch):
    def _boom(*_args, **_kwargs):
        raise AssertionError("BM25Signal must not instantiate CodeIndex")

    monkeypatch.setattr(CodeIndex, "__init__", _boom)
    monkeypatch.chdir(tmp_path)
    query = SignalQuery.build("orion deploy")
    entries = [
        _entry(1, "deploy to staging cluster one"),
        _entry(2, "deploy to staging cluster two"),
        _entry(3, "orion deploy runs on jenkins"),
    ]

    scores = BM25Signal().score(query, entries)

    # "orion" is rare (df=1) and "deploy" common (df=3): the entry holding both
    # scores near the top of the [0, 1] range, the others stay low.
    assert scores[2] > 0.9
    assert scores[0] < 0.2 and scores[1] < 0.2
    assert all(0.0 <= value <= 1.0 for value in scores)
    assert not list(tmp_path.rglob("code_index.sqlite3"))


def test_bm25_score_is_a_public_pure_function_with_the_reference_formula():
    docs = {"a": "alpha alpha tail", "b": "alpha once"}
    terms = ["alpha"]
    corpus_size = 2
    avgdl = sum(len(doc) for doc in docs.values()) / corpus_size
    df = {"alpha": 2}
    idf = math.log(1 + (corpus_size - 2 + 0.5) / (2 + 0.5))
    for lowered in docs.values():
        tf = lowered.count("alpha")
        expected = (
            idf
            * (tf * (BM25_K1 + 1))
            / (tf + BM25_K1 * (1 - BM25_B + BM25_B * len(lowered) / avgdl))
        )
        assert bm25_score(terms, df, corpus_size, avgdl, len(lowered), lowered) == expected


def test_entity_signal_matches_stored_entities_and_is_silent_without_query_entities():
    entries = [
        _entry(1, "Orion runs its deploy on Jenkins", entities=["jenkins", "orion"]),
        _entry(2, "deploy pipeline config", entities=[]),
    ]

    with_entity = EntitySignal().score(SignalQuery.build("Orion deploy pipeline"), entries)
    explicit = EntitySignal().score(
        SignalQuery.build("deploy pipeline", entities=["Jenkins", "Orion"]), entries
    )
    without = EntitySignal().score(SignalQuery.build("deploy pipeline"), entries)

    assert with_entity == [pytest.approx(1.0), 0.0]
    assert explicit == [pytest.approx(1.0), 0.0]
    assert without == [0.0, 0.0]


def test_extract_entities_uses_quotes_capitalised_runs_and_identifier_patterns():
    assert extract_entities("这个项目的发布代号是 BLUE-OTTER-77") == ["blue-otter-77"]
    assert extract_entities('Use "pnpm" for the Volcano Engine project, see pyproject.toml') == [
        "engine",
        "pnpm",
        "pyproject.toml",
        "volcano",
        "volcano engine",
    ]
    # Capitalised sentence starters and generic nouns are not entities.
    assert extract_entities("The release checklist requires a clean build") == []
    assert extract_entities("User prefers uv over pip") == []


# ---------------------------------------------------------------------------
# Fusion: adversarial top-K, weights, vector slot
# ---------------------------------------------------------------------------


def _adversarial_batch(db_path: Path, signals=None) -> tuple[MemoryManager, int]:
    manager = MemoryManager(db_path, scope="project", signals=signals)
    for index in range(1, 12):
        manager.save(f"deploy pipeline config for staging cluster {index}", importance=0.6)
    target_id = manager.save("Orion runs its deploy on Jenkins", importance=0.5)
    return manager, target_id


def test_multi_signal_recall_puts_the_target_in_top_k_where_lexical_only_cannot(tmp_path):
    query = "Orion deploy pipeline config"
    lexical_only, lexical_target = _adversarial_batch(
        tmp_path / "lexical.db", signals=[LexicalSignal()]
    )
    fused, fused_target = _adversarial_batch(tmp_path / "fused.db")

    lexical_top = [entry.id for entry in lexical_only.recall(query, limit=5, mark_access=False)]
    fused_top = [entry.id for entry in fused.recall(query, limit=5, mark_access=False)]

    # Eleven noise rows each cover 3/4 of the query words; the target covers
    # only 2/4, so coverage alone ranks it 12th. BM25 (rare "orion") and the
    # entity tag lift it to first place.
    assert lexical_target not in lexical_top
    assert fused_top[0] == fused_target


def test_content_signals_gate_the_score_and_metadata_only_breaks_ties(tmp_path):
    manager = MemoryManager(tmp_path / "memory.db", scope="project")
    manager.save("The release checklist requires a clean build", importance=1.0)
    low_id = manager.save("memory retrieval uses keyword matching", importance=0.2)
    high_id = manager.save("memory retrieval uses semantic matching", importance=0.9)

    results = manager.recall("memory retrieval", limit=5, mark_access=False)

    assert [entry.id for entry in results] == [high_id, low_id]
    assert manager.recall("completely unrelated phrase") == []


def test_fusion_weights_are_fixed_constants_that_sum_to_one():
    assert (WEIGHT_LEXICAL, WEIGHT_BM25, WEIGHT_ENTITY) == (0.40, 0.25, 0.15)
    assert math.isclose(WEIGHT_LEXICAL + WEIGHT_BM25 + WEIGHT_ENTITY, CONTENT_BUDGET)
    assert math.isclose(
        CONTENT_BUDGET + WEIGHT_IMPORTANCE + WEIGHT_CONFIDENCE + WEIGHT_HOTNESS, 1.0
    )
    assert 0.0 < TEMPORAL_MISMATCH_MULTIPLIER < 1.0


def test_vector_slot_is_absent_by_default_and_the_three_signals_carry_recall(tmp_path):
    assert [signal.name for signal in default_signals()] == ["lexical", "bm25", "entity"]
    assert not any(isinstance(signal, VectorSignal) for signal in default_signals())

    manager = MemoryManager(tmp_path / "memory.db", scope="project")
    target_id = manager.save("Orion runs its deploy on Jenkins")
    manager.save("unrelated note about lunch")

    assert [entry.id for entry in manager.recall("Orion deploy", mark_access=False)] == [target_id]


def test_vector_signal_participates_only_when_injected_and_scale_is_unchanged():
    entries = [_entry(1, "alpha"), _entry(2, "beta")]
    query = SignalQuery.build("alpha")

    def embedder(text: str) -> list[float]:
        return [1.0, 0.0] if "alpha" in text else [0.0, 1.0]

    default = content_scores(query, entries, default_signals())
    with_vector = content_scores(query, entries, [*default_signals(), VectorSignal(embedder)])

    # Rescaling keeps the content budget fixed however many signals are active.
    assert max(default) <= CONTENT_BUDGET + 1e-9
    assert max(with_vector) <= CONTENT_BUDGET + 1e-9
    assert with_vector[0] > with_vector[1]
    assert with_vector[1] == 0.0


def test_a_signal_returning_the_wrong_shape_is_rejected():
    class Broken:
        name = "broken"
        weight = 0.5

        def score(self, query, entries):
            return [1.0]

    with pytest.raises(ValueError, match="broken"):
        content_scores(SignalQuery.build("x"), [_entry(1, "x"), _entry(2, "x y")], [Broken()])


# ---------------------------------------------------------------------------
# Temporal layer: intent table, migration backfill, weighting
# ---------------------------------------------------------------------------


def test_temporal_intent_keyword_table_covers_chinese_and_english():
    assert detect_temporal_intent("我们计划下周迁移到 uv") == "future_plan"
    assert detect_temporal_intent("what did we decide last week") == "past_event"
    assert detect_temporal_intent("之前为什么放弃 npm") == "past_event"
    assert detect_temporal_intent("currently the project uses uv") == "current_state"
    assert detect_temporal_intent("目前默认用哪个包管理器") == "current_state"
    # Plain questions carry no temporal intent, and mixed markers are ambiguous.
    assert detect_temporal_intent("项目测试怎么运行") is None
    assert detect_temporal_intent("which package manager") is None
    assert detect_temporal_intent("we will migrate tomorrow but yesterday it failed") is None


def test_derive_temporal_kind_prefers_explicit_then_content_then_kind_mapping():
    assert derive_temporal_kind("fact", "we plan to migrate", "past_event") == "past_event"
    assert derive_temporal_kind("fact", "we plan to migrate next month") == "future_plan"
    assert derive_temporal_kind("fact", "release codename is BLUE") == "past_event"
    assert derive_temporal_kind("preference", "prefer uv") == "current_state"
    assert derive_temporal_kind("constraint", "tests via pytest") == "current_state"
    assert derive_temporal_kind("decision", "ruff for lint") == "current_state"
    assert derive_temporal_kind("correction", "it is Orion, not Orien") == "current_state"
    assert derive_temporal_kind("architecture", "custom kind") == "current_state"
    with pytest.raises(ValueError, match="temporal_kind"):
        derive_temporal_kind("fact", "x", "someday")


def _v1_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        create table memories (
            id integer primary key autoincrement,
            scope text not null,
            content text not null,
            created_at text not null,
            kind text not null default 'fact',
            source text not null default 'manual',
            importance real not null default 0.5,
            confidence real not null default 1.0,
            updated_at text not null default '',
            expires_at text,
            access_count integer not null default 0,
            content_hash text not null default ''
        )
        """
    )
    conn.execute("pragma user_version = 1")


def test_schema_v2_backfills_temporal_kind_from_kind_and_entities_from_content(tmp_path):
    db_path = tmp_path / "memory.db"
    stamp = "2026-01-01T00:00:00+00:00"
    rows = [
        ("release codename is BLUE-OTTER-77", "fact"),
        ("Always run tests with pytest", "constraint"),
        ("Prefer uv over pip", "preference"),
        ("Use ruff for lint", "decision"),
        ("The API is called Orion", "correction"),
        ("architecture note", "architecture"),
    ]
    with sqlite3.connect(db_path) as conn:
        _v1_schema(conn)
        for content, kind in rows:
            conn.execute(
                """
                insert into memories(scope, content, created_at, kind, source, updated_at,
                                     content_hash)
                values (?, ?, ?, ?, 'agent', ?, ?)
                """,
                ("project", content, stamp, kind, stamp, _hash_content(content)),
            )

    manager = MemoryManager(db_path, scope="project")
    by_content = {entry.content: entry for entry in manager.list(limit=10)}

    assert by_content["release codename is BLUE-OTTER-77"].temporal_kind == "past_event"
    assert by_content["release codename is BLUE-OTTER-77"].entities == ["blue-otter-77"]
    for content in ("Always run tests with pytest", "Prefer uv over pip", "Use ruff for lint"):
        assert by_content[content].temporal_kind == "current_state"
    assert by_content["The API is called Orion"].temporal_kind == "current_state"
    assert by_content["The API is called Orion"].entities == ["api", "orion"]
    assert by_content["architecture note"].temporal_kind == "current_state"
    assert all(entry.event_at is None for entry in by_content.values())
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("pragma user_version").fetchone()[0] == 2
        columns = {row[1] for row in conn.execute("pragma table_info(memories)")}
    assert {"entities", "event_at", "temporal_kind"} <= columns

    # Re-opening is idempotent: the marker stays at 2 and nothing is rewritten.
    MemoryManager(db_path, scope="project")
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("pragma user_version").fetchone()[0] == 2
        stored = conn.execute(
            "select temporal_kind, entities from memories where content = ?",
            ("release codename is BLUE-OTTER-77",),
        ).fetchone()
    assert stored == ("past_event", '["blue-otter-77"]')


def test_legacy_v0_database_reaches_v2_with_backfill(tmp_path):
    db_path = tmp_path / "memory.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            create table memories (
                id integer primary key autoincrement,
                scope text not null,
                content text not null,
                created_at text not null
            )
            """
        )
        conn.execute(
            "insert into memories(scope, content, created_at) values (?, ?, ?)",
            ("project", "release codename is BLUE-OTTER-77", "2026-01-01T00:00:00+00:00"),
        )

    rows = MemoryManager(db_path, scope="project").list()

    assert len(rows) == 1
    assert rows[0].temporal_kind == "past_event"
    assert rows[0].entities == ["blue-otter-77"]
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("pragma user_version").fetchone()[0] == 2


def test_query_intent_weights_entries_of_the_matching_temporal_layer(tmp_path):
    manager = MemoryManager(tmp_path / "memory.db", scope="project")
    future_id = manager.save("Team build uses uv variant alpha", temporal_kind="future_plan")
    # Saved last, so it wins every recency tie-break: only the temporal
    # multiplier can put the plan ahead of it.
    past_id = manager.save("Team build uses uv variant beta", temporal_kind="past_event")

    def ids(query: str, **kwargs) -> list[int]:
        return [entry.id for entry in manager.recall(query, mark_access=False, **kwargs)]

    assert ids("team build uv")[0] == past_id  # no intent -> no weighting
    assert ids("will the team build use uv")[0] == future_id
    assert ids("did the team build use uv")[0] == past_id
    assert ids("team build uv", temporal_kind="future_plan")[0] == future_id
    assert ids("will the team build use uv", temporal_kind="past_event")[0] == past_id


def test_future_plan_whose_event_time_passed_is_matched_as_a_past_event(tmp_path):
    manager = MemoryManager(tmp_path / "memory.db", scope="project")
    upcoming_id = manager.save(
        "Release the Orion build variant gamma",
        temporal_kind="future_plan",
        event_at=(datetime.now(UTC) + timedelta(days=30)).isoformat(),
    )
    manager.save(
        "Release the Orion build variant delta",
        temporal_kind="future_plan",
        event_at="2020-01-01T00:00:00+00:00",
    )

    results = manager.recall("will we release the orion build", mark_access=False)

    assert results[0].id == upcoming_id


def test_save_derives_temporal_kind_from_content_markers_when_not_explicit(tmp_path):
    manager = MemoryManager(tmp_path / "memory.db", scope="project")
    plan_id = manager.save("We plan to migrate the build to uv next month")
    fact_id = manager.save("release codename is BLUE-OTTER-77")
    preference_id = manager.save("Prefer uv over pip", kind="preference")

    by_id = {entry.id: entry for entry in manager.list()}
    assert by_id[plan_id].temporal_kind == "future_plan"
    assert by_id[fact_id].temporal_kind == "past_event"
    assert by_id[preference_id].temporal_kind == "current_state"
    with pytest.raises(ValueError, match="temporal_kind must be one of"):
        manager.save("bad layer", temporal_kind="someday")
    with pytest.raises(ValueError, match="event_at must be a valid ISO-8601"):
        manager.save("bad time", event_at="not-a-date")


# ---------------------------------------------------------------------------
# OpenViking hotness port: boundary values
# ---------------------------------------------------------------------------


def test_hotness_score_boundaries_match_the_ported_formula():
    assert hotness_score(0, None, NOW) == 0.0
    assert hotness_score(0, NOW, NOW) == pytest.approx(0.5)
    assert hotness_score(0, NOW - timedelta(days=7), NOW) == pytest.approx(0.25)
    assert hotness_score(0, NOW - timedelta(days=30), NOW, half_life_days=30.0) == pytest.approx(
        0.25
    )
    heavy = hotness_score(1_000_000, NOW, NOW)
    assert 0.999 < heavy < 1.0
    # Clock skew (updated_at ahead of now) clamps the age at zero.
    assert hotness_score(3, NOW + timedelta(days=5), NOW) == hotness_score(3, NOW, NOW)
    # Naive timestamps are treated as UTC.
    assert hotness_score(1, NOW.replace(tzinfo=None), NOW) == hotness_score(1, NOW, NOW)
    for count, age in ((0, 0), (5, 3), (50, 400), (10**9, 10**4)):
        assert 0.0 <= hotness_score(count, NOW - timedelta(days=age), NOW) <= 1.0


# ---------------------------------------------------------------------------
# ADD-only write path
# ---------------------------------------------------------------------------


def test_save_same_hash_accumulates_without_redefining_the_original_declaration(tmp_path):
    manager = MemoryManager(tmp_path / "memory.db", scope="project")
    expires = (datetime.now(UTC) + timedelta(days=30)).replace(microsecond=0)
    first_id = manager.save(
        "Orion deploys on Jenkins",
        kind="decision",
        source="user",
        importance=0.4,
        confidence=0.6,
        expires_at=expires,
        entities=["Orion"],
        temporal_kind="current_state",
    )
    first = manager.list()[0]

    second_id = manager.save(
        "orion deploys on jenkins",
        kind="fact",
        source="agent",
        importance=0.9,
        confidence=0.5,
        expires_at=expires + timedelta(days=30),
        entities=["Jenkins"],
        event_at="2026-01-01T00:00:00+00:00",
        temporal_kind="past_event",
    )
    entry = manager.list()[0]

    assert first_id == second_id
    assert (entry.kind, entry.source) == ("decision", "user")
    assert entry.expires_at == expires.isoformat()
    assert entry.temporal_kind == "current_state"
    assert (entry.importance, entry.confidence) == (0.9, 0.6)
    assert entry.entities == ["jenkins", "orion"]
    assert entry.event_at == "2026-01-01T00:00:00+00:00"
    assert entry.updated_at >= first.updated_at
    assert entry.created_at == first.created_at

    manager.save("Orion deploys on Jenkins", event_at="2027-06-01T00:00:00+00:00")
    assert manager.list()[0].event_at == "2026-01-01T00:00:00+00:00"
    assert manager.stats()["total"] == 1


# ---------------------------------------------------------------------------
# Tool, prompt and REPL surfaces
# ---------------------------------------------------------------------------


def test_memory_tool_schemas_expose_the_temporal_and_entity_parameters():
    tools = {tool.name: tool for tool in get_builtin_tools()}
    save_props = tools["save_memory"].parameters["properties"]
    search_props = tools["search_memory"].parameters["properties"]

    assert {"entities", "event_at", "temporal_kind"} <= set(save_props)
    assert save_props["temporal_kind"]["enum"] == ["current_state", "past_event", "future_plan"]
    assert {"entities", "temporal_kind"} <= set(search_props)
    assert tools["save_memory"].parameters["required"] == ["content"]
    assert tools["search_memory"].parameters["required"] == ["query"]


def test_memory_tools_pass_through_and_validate_the_new_parameters(tmp_path):
    config = load_config(project_root=tmp_path)
    context = ToolContext(cwd=str(tmp_path), config=config)

    saved = asyncio.run(
        save_memory(
            {
                "content": "Orion ships the Jenkins migration",
                "kind": "decision",
                "entities": ["Orion"],
                "event_at": "2026-12-01T00:00:00Z",
                "temporal_kind": "future_plan",
            },
            context,
        )
    )
    assert not saved.is_error
    entry = MemoryManager(config.memory.long_term_db_path, scope=str(tmp_path)).list()[0]
    assert entry.temporal_kind == "future_plan"
    assert entry.entities == ["orion"]
    assert entry.event_at == "2026-12-01T00:00:00+00:00"

    bad_layer = asyncio.run(save_memory({"content": "x", "temporal_kind": "someday"}, context))
    bad_entities = asyncio.run(save_memory({"content": "x", "entities": "Orion"}, context))
    assert bad_layer.is_error and "temporal_kind" in bad_layer.content
    assert bad_entities.is_error and "entities" in bad_entities.content

    recalled = asyncio.run(
        search_memory(
            {"query": "jenkins migration", "temporal_kind": "future_plan", "entities": ["Orion"]},
            context,
        )
    )
    assert not recalled.is_error
    assert recalled.content.startswith(f"#{entry.id} [decision, importance=0.50, ")
    assert "temporal=future_plan] Orion ships the Jenkins migration" in recalled.content
    bad_search = asyncio.run(search_memory({"query": "x", "temporal_kind": 7}, context))
    assert bad_search.is_error


def test_prompt_recall_block_tags_each_item_with_its_temporal_layer(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUSCLI_API_KEY", "test-key")
    config = load_config(project_root=tmp_path)
    MemoryManager(config.memory.long_term_db_path, scope=str(tmp_path)).save(
        "发布代号是 BLUE-OTTER-77", kind="fact", temporal_kind="future_plan"
    )
    assembler = PromptAssembler(config, str(tmp_path), [], "model", "provider")

    dynamic = assembler.build_dynamic("发布代号是什么")

    assert '<recalled-memory trust="untrusted-data">' in dynamic
    assert "temporal=future_plan importance=0.50] 发布代号是 BLUE-OTTER-77" in dynamic


def _slash(command: str, tmp_path: Path, config: NexusCliConfig) -> str:
    console = Console(file=io.StringIO(), width=120)
    asyncio.run(
        _handle_slash(command, console, str(tmp_path), config, None, None, None, None, None)
    )
    return console.file.getvalue()


def test_save_slash_command_accepts_option_tokens_and_keeps_plain_facts_intact(tmp_path):
    assert _parse_save_options("project   uses uv") == ({}, "project   uses uv")
    assert _parse_save_options("temporal=future_plan entities=Orion,Jenkins Orion moves CI") == (
        {"temporal_kind": "future_plan", "entities": ["Orion", "Jenkins"]},
        "Orion moves CI",
    )
    assert _parse_save_options("kind=decision event_at=2026-12-01 x=1 is fine") == (
        {"kind": "decision", "event_at": "2026-12-01"},
        "x=1 is fine",
    )

    config = NexusCliConfig()
    config.memory.long_term_db_path = str(tmp_path / "memory.db")
    out = _slash(
        "/save temporal=future_plan entities=Orion,Jenkins Orion will move CI to Jenkins",
        tmp_path,
        config,
    )
    assert "Saved memory #" in out
    entry = MemoryManager(config.memory.long_term_db_path, scope=str(tmp_path)).list()[0]
    assert entry.content == "Orion will move CI to Jenkins"
    assert entry.temporal_kind == "future_plan"
    assert entry.entities == ["jenkins", "orion"]
    assert entry.source == "manual"

    rejected = _slash("/save temporal=someday some fact", tmp_path, config)
    assert "Cannot save memory" in rejected and "Traceback" not in rejected
    usage = _slash("/save", tmp_path, config)
    # The usage line must survive rich rendering verbatim (no "[...]" markup
    # swallowed) and name the option tokens.
    assert "Usage: /save <fact>" in usage
    assert "temporal=<current_state|past_event|future_plan>" in usage
