"""Tests for the skill self-evolution mechanism.

Covers SkillEvolutionStore (snapshot / versioned rule append / rollback /
audit lines / version-cap eviction), SkillStateStore usage accounting
(usage.json next to the state file), the opt-in SkillContextBuffer recorder,
and the /skill evolve / stats / rollback / load subcommands.

On Windows Path.home() reads USERPROFILE (not HOME), so every test redirects
both environment variables (mirrors tests/test_skill_force_load.py).
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from rich.console import Console

from nexuscli.entrypoints.commands.skill import _skill_command
from nexuscli.skill import SkillContextBuffer, SkillStateStore
from nexuscli.skill.evolve import SkillEvolutionStore


def _isolate_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect Path.home() into a temp home so store writes stay isolated."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


def _write_user_skill(home: Path, name: str, body: str = "demo body") -> Path:
    skill_dir = home / ".nexuscli" / "skills" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    path = skill_dir / "SKILL.md"
    path.write_text(
        f"---\nname: {name}\ndescription: {name} desc\n---\n{body}\n",
        encoding="utf-8",
    )
    return path


def _console() -> Console:
    return Console(file=io.StringIO(), width=200)


def _console_text(console: Console) -> str:
    return console.file.getvalue()


def _stats_line(console: Console, name: str) -> str:
    """Find the /skill stats row for ``name`` (rich renders tabs as spaces)."""
    for line in _console_text(console).splitlines():
        tokens = line.split()
        if tokens and tokens[0] == name and any(t.startswith("used=") for t in tokens):
            return line
    return ""


def _audit_lines(home: Path) -> list[dict]:
    path = home / ".nexuscli" / "skill_history" / "audit.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _agent_stub() -> SimpleNamespace:
    return SimpleNamespace(skill_context_buffer=SkillContextBuffer())


# ---------------------------------------------------------------------------
# SkillEvolutionStore
# ---------------------------------------------------------------------------


def test_snapshot_copies_skill_and_tracks_versions(tmp_path, monkeypatch):
    home = _isolate_home(tmp_path, monkeypatch)
    skill_path = _write_user_skill(home, "demo")
    store = SkillEvolutionStore()

    assert store.version_info("demo") == {
        "skill": "demo",
        "version": 0,
        "history": [],
    }

    first = store.snapshot("demo")
    skill_path.write_text(
        "---\nname: demo\ndescription: d\n---\nbody v2\n", encoding="utf-8"
    )
    second = store.snapshot("demo")

    assert (first, second) == (1, 2)
    history_root = home / ".nexuscli" / "skill_history" / "demo"
    assert (history_root / "v1.md").read_text(encoding="utf-8").endswith("demo body\n")
    assert (history_root / "v2.md").read_text(encoding="utf-8").endswith("body v2\n")
    info = store.version_info("demo")
    assert info["version"] == 2
    assert [item["version"] for item in info["history"]] == [1, 2]


def test_save_versioned_snapshots_appends_timestamped_rule_and_audits(tmp_path, monkeypatch):
    home = _isolate_home(tmp_path, monkeypatch)
    skill_path = _write_user_skill(home, "demo")
    store = SkillEvolutionStore()

    version = store.save_versioned(
        "demo", "prefer the task tooling for repo-wide work", reason="user feedback"
    )

    assert version == 1
    updated = skill_path.read_text(encoding="utf-8")
    assert "## Evolved Rules" in updated
    assert "- [" in updated  # timestamped entry
    assert "prefer the task tooling for repo-wide work" in updated
    assert "user feedback" in updated
    # The snapshot frozen before the append must not contain the new rule.
    assert "prefer the task tooling" not in (
        home / ".nexuscli" / "skill_history" / "demo" / "v1.md"
    ).read_text(encoding="utf-8")

    records = _audit_lines(home)
    assert len(records) == 1
    assert records[0]["skill"] == "demo"
    assert records[0]["op"] == "save"
    assert records[0]["version"] == 1
    assert records[0]["reason"] == "user feedback"
    assert records[0]["ts"]

    second = store.save_versioned("demo", "second rule", reason="r2")

    assert second == 2
    assert len(_audit_lines(home)) == 2
    assert _audit_lines(home)[1]["version"] == 2
    content = skill_path.read_text(encoding="utf-8")
    # Chronological order inside the rules section.
    assert content.index("second rule") > content.index("prefer the task tooling")


def test_save_versioned_appends_into_existing_rules_section(tmp_path, monkeypatch):
    home = _isolate_home(tmp_path, monkeypatch)
    skill_path = _write_user_skill(
        home, "demo", body="intro\n\n## Evolved Rules\n\n- old\n\n## Next\n\nafter"
    )
    store = SkillEvolutionStore()

    store.save_versioned("demo", "new rule", reason="r")

    content = skill_path.read_text(encoding="utf-8")
    assert content.index("- old") < content.index("new rule") < content.index("## Next")


def test_rollback_restores_version_and_keeps_pre_rollback_snapshot(tmp_path, monkeypatch):
    home = _isolate_home(tmp_path, monkeypatch)
    skill_path = _write_user_skill(home, "demo", body="original body")
    store = SkillEvolutionStore()
    store.save_versioned("demo", "rule one", reason="r1")
    v1_content = (home / ".nexuscli" / "skill_history" / "demo" / "v1.md").read_text(
        encoding="utf-8"
    )
    store.save_versioned("demo", "rule two", reason="r2")

    safety_version = store.rollback("demo", 1)

    # The pre-rollback state (with both rules) is frozen as v3 before restoring.
    assert safety_version == 3
    assert skill_path.read_text(encoding="utf-8") == v1_content
    assert "rule two" not in skill_path.read_text(encoding="utf-8")
    records = _audit_lines(home)
    assert [record["op"] for record in records] == ["save", "save", "rollback"]
    assert records[-1]["version"] == 1
    assert records[-1]["reason"] == "rollback"
    # The safety snapshot survives: rolling forward again is possible.
    assert [
        item["version"] for item in store.version_info("demo")["history"]
    ] == [1, 2, 3]


def test_rollback_missing_version_returns_none(tmp_path, monkeypatch):
    home = _isolate_home(tmp_path, monkeypatch)
    _write_user_skill(home, "demo")
    store = SkillEvolutionStore()

    assert store.rollback("demo", 99) is None
    assert _audit_lines(home) == []


def test_snapshot_missing_skill_raises(tmp_path, monkeypatch):
    _isolate_home(tmp_path, monkeypatch)
    store = SkillEvolutionStore()

    with pytest.raises(FileNotFoundError):
        store.snapshot("nope")


def test_version_cap_evicts_oldest(tmp_path, monkeypatch):
    home = _isolate_home(tmp_path, monkeypatch)
    _write_user_skill(home, "demo")
    store = SkillEvolutionStore()

    for index in range(11):
        store.save_versioned("demo", f"rule {index}", reason=f"r{index}")

    version_files = {
        int(path.stem[1:])
        for path in (home / ".nexuscli" / "skill_history" / "demo").glob("v*.md")
    }
    assert version_files == set(range(2, 12))
    info = store.version_info("demo")
    assert info["version"] == 11
    assert len(info["history"]) == 10
    assert 1 not in version_files  # oldest (v1) evicted


# ---------------------------------------------------------------------------
# SkillStateStore usage accounting
# ---------------------------------------------------------------------------


def test_state_store_usage_accounting(tmp_path):
    state = SkillStateStore(tmp_path / "skills.json")

    assert state.usage() == {}

    state.record_use("demo")
    state.record_use("demo")
    state.record_use("other")

    usage = state.usage()
    assert usage["demo"]["used"] == 2
    assert usage["demo"]["last_used"]
    assert usage["other"]["used"] == 1
    # usage.json lives next to the state file, not in it.
    assert (tmp_path / "usage.json").is_file()


def test_context_buffer_records_usage_only_when_store_attached(tmp_path):
    state = SkillStateStore(tmp_path / "skills.json")
    buffer = SkillContextBuffer(usage_store=state)

    buffer.push("demo", "body")
    buffer.push(None, "ignored")
    buffer.push("also-ignored", None)

    assert state.usage()["demo"]["used"] == 1
    # Default buffers stay inert: no accidental writes for callers that did
    # not opt in (the buffer also carries non-skill reminders).
    SkillContextBuffer().push("demo", "body")
    assert state.usage()["demo"]["used"] == 1


# ---------------------------------------------------------------------------
# /skill subcommands
# ---------------------------------------------------------------------------


def test_skill_command_load_records_usage(tmp_path, monkeypatch):
    home = _isolate_home(tmp_path, monkeypatch)
    _write_user_skill(home, "demo")
    agent = _agent_stub()
    console = _console()

    _skill_command("load demo", console, str(tmp_path), agent)

    assert not agent.skill_context_buffer.is_empty()
    assert 'Skill "demo" loaded' in _console_text(console)
    assert SkillStateStore().usage()["demo"]["used"] == 1


def test_skill_command_evolve_rollback_stats(tmp_path, monkeypatch):
    home = _isolate_home(tmp_path, monkeypatch)
    skill_path = _write_user_skill(home, "demo")
    original = skill_path.read_text(encoding="utf-8")
    agent = _agent_stub()

    evolve_console = _console()
    _skill_command(
        "evolve demo prefer pytest for every check", evolve_console, str(tmp_path), agent
    )
    assert "prefer pytest for every check" in skill_path.read_text(encoding="utf-8")
    _skill_command("evolve demo second rule", evolve_console, str(tmp_path), agent)

    stats_console = _console()
    _skill_command("stats", stats_console, str(tmp_path), agent)
    demo_stats = _stats_line(stats_console, "demo")
    assert demo_stats, _console_text(stats_console)
    assert "used=0" in demo_stats  # evolved but never loaded
    assert "version=2" in demo_stats
    assert "history=2" in demo_stats

    rollback_console = _console()
    _skill_command("rollback demo 1", rollback_console, str(tmp_path), agent)
    restored = skill_path.read_text(encoding="utf-8")
    # v1 is the snapshot taken before the first rule was appended, so rolling
    # back to it restores the pristine skill file exactly.
    assert restored == original

    _skill_command("load demo", evolve_console, str(tmp_path), agent)
    final_console = _console()
    _skill_command("stats", final_console, str(tmp_path), agent)
    assert "used=1" in _stats_line(final_console, "demo")


def test_skill_command_stats_degrades_non_slug_skill_names(tmp_path, monkeypatch):
    """A frontmatter ``name`` that is not a slug degrades one row, not stats.

    The registry accepts whatever ``name`` the frontmatter carries (e.g.
    ``Demo``), but the version store keys snapshots by lowercase slug, so
    ``version_info("Demo")`` raises ValueError. /skill stats must keep the
    whole listing usable and show that skill as version=0/history=0.
    """
    home = _isolate_home(tmp_path, monkeypatch)
    _write_user_skill(home, "demo")
    non_slug_dir = home / ".nexuscli" / "skills" / "demo-non-slug"
    non_slug_dir.mkdir(parents=True)
    (non_slug_dir / "SKILL.md").write_text(
        "---\nname: Demo\ndescription: hand-written frontmatter\n---\nbody\n",
        encoding="utf-8",
    )
    agent = _agent_stub()
    console = _console()

    _skill_command("stats", console, str(tmp_path), agent)  # must not raise

    text = _console_text(console)
    non_slug_line = _stats_line(console, "Demo")
    assert non_slug_line, text
    assert "version=0" in non_slug_line
    assert "history=0" in non_slug_line
    # The slug-named skills are still listed alongside.
    assert _stats_line(console, "demo"), text


def test_skill_command_evolve_unknown_skill_reports_not_found(tmp_path, monkeypatch):
    _isolate_home(tmp_path, monkeypatch)
    agent = _agent_stub()
    console = _console()

    _skill_command("evolve nope some rule", console, str(tmp_path), agent)

    assert 'not found in user skills' in _console_text(console)


def test_skill_command_rollback_missing_version_reports(tmp_path, monkeypatch):
    home = _isolate_home(tmp_path, monkeypatch)
    _write_user_skill(home, "demo")
    agent = _agent_stub()
    console = _console()

    _skill_command("rollback demo 42", console, str(tmp_path), agent)

    assert 'Version v42 of "demo" not found' in _console_text(console)
