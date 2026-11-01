"""Tests for the eval-report-driven skill proposal flow (skill/propose.py +
/skill propose / /skill proposals).

Everything stays hermetic: the LLM is a scripted fake injected through the
ProposalGenerator constructor seam (no client is ever built from config in
the passing paths), HOME/USERPROFILE are redirected (autouse fixture plus a
Path.home() patch), and eval reports are fabricated under tmp_path. The
real-track test only reads a fabricated last-real-report.json — no network.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from rich.console import Console

from nexuscli.entrypoints.commands.skill import _skill_command
from nexuscli.skill import SkillContextBuffer
from nexuscli.skill.propose import (
    ProposalGenerator,
    ProposeCredentialsError,
    ReportUnavailableError,
    _evidence_supported,
    digest_has_signal,
    list_proposal_records,
    load_report,
    run_post_landing_diff,
    save_proposal_record,
    summarize_report,
)

# Environment variables that could put a real key into the config chain; the
# no-key tests must not depend on the developer's shell.
_KEY_ENV_VARS = (
    "NEXUSCLI_API_KEY",
    "DEEPSEEK_API_KEY",
    "ZAI_API_KEY",
    "GLM_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "KIMI_API_KEY",
    "MOONSHOT_API_KEY",
    "STEP_API_KEY",
    "XFYUN_API_KEY",
    "AGNES_API_KEY",
    "FREELLMAPI_API_KEY",
)


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


def _isolate_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


def _clear_llm_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in _KEY_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.delenv("NEXUSCLI_OFFLINE_DEMO", raising=False)


def _write_user_skill(home: Path, name: str, body: str = "demo body") -> Path:
    skill_dir = home / ".nexuscli" / "skills" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    path = skill_dir / "SKILL.md"
    path.write_text(
        f"---\nname: {name}\ndescription: {name} desc\n---\n{body}\n",
        encoding="utf-8",
    )
    return path


def _write_report(root: Path, filename: str, report: dict[str, Any]) -> Path:
    path = root / ".nexuscli" / "evals" / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _hermetic_report() -> dict[str, Any]:
    return {
        "generated_at": "2026-10-27T10:00:00",
        "scenarios": [
            {
                "name": "todo_flow",
                "status": "fail",
                "failure_reasons": [
                    "expected tool write_file was never called",
                    "final answer missing the summary heading",
                ],
                "cost": {"change": "unchanged", "warnings": []},
            },
            {
                "name": "search_flow",
                "status": "pass",
                "failure_reasons": [],
                "cost": {"change": "increased", "warnings": ["4 turns → 7 turns"]},
            },
        ],
        "summary": {
            "total": 2,
            "passed": 1,
            "failed": 1,
            "regressions": 0,
            "fixed": 0,
            "cost_increased": 1,
            "cost_decreased": 0,
            "cost_unchanged": 1,
            "difficulty": {},
        },
    }


def _all_pass_report() -> dict[str, Any]:
    return {
        "generated_at": "2026-10-27T10:30:00",
        "scenarios": [
            {
                "name": "todo_flow",
                "status": "pass",
                "failure_reasons": [],
                "cost": {"change": "unchanged", "warnings": []},
            }
        ],
        "summary": {"total": 1, "passed": 1, "failed": 0},
    }


def _real_report() -> dict[str, Any]:
    return {
        "generated_at": "2026-10-27T11:00:00",
        "track": "real",
        "repeat": 1,
        "scenarios": [
            {
                "name": "judge_gate",
                "source": "evals/real/judge_gate.md",
                "pass_k": {
                    "repeat": 1,
                    "passed_runs": 0,
                    "failed_runs": 1,
                    "all_pass": False,
                },
                "cost": {"runs": 1, "total_usage": {}, "avg_usage": {}, "total_cost": {}},
                "runs": [
                    {
                        "index": 1,
                        "status": "fail",
                        "failure_reasons": [
                            "judge score 0.40 is below threshold 0.80: answer skipped the rubric"
                        ],
                        "tool_sequence": ["read_file"],
                        "turns": 3,
                        "usage": {},
                        "cost": {},
                        "judge": None,
                    }
                ],
            }
        ],
        "summary": {
            "total_runs": 1,
            "passed": 0,
            "failed": 1,
            "scenarios_total": 1,
            "scenarios_all_pass": 0,
            "all_pass_rate": 0.0,
            "total_usage": {},
            "total_cost": {},
        },
    }


# Evidence-backed items cite report content; the third one invents its grounds
# and must be dropped before review.
_PROPOSAL_REPLY = json.dumps(
    [
        {
            "rule": "Always call write_file to persist the todo list before finishing",
            "evidence": "scenario todo_flow: expected tool write_file was never called",
        },
        {
            "rule": "Keep search within its previous turn budget",
            "evidence": "scenario search_flow cost alarm: 4 turns → 7 turns",
        },
        {
            "rule": "Improve the general vibe of every answer",
            "evidence": "the model felt unconfident about it",
        },
    ],
    ensure_ascii=False,
)


class FakeProposalLLM:
    """Scripted single-call LLM: yields one text_delta with the canned reply."""

    def __init__(self, reply: str):
        self.reply = reply
        self.calls: list[dict[str, Any]] = []

    async def chat(self, messages, tools, *, system_prompt):
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "messages": [
                    {"role": message.role, "content": message.content} for message in messages
                ],
                "tools": list(tools),
            }
        )
        yield {"type": "text_delta", "text": self.reply}


class _FakeStdin:
    def __init__(self, isatty: bool):
        self._isatty = isatty

    def isatty(self) -> bool:
        return self._isatty


def _stub_review(monkeypatch: pytest.MonkeyPatch, answers: list[str]) -> list[str]:
    """Canned Prompt.ask answers; returns the prompts the flow asked for."""
    asked: list[str] = []

    import rich.prompt

    def _fake_ask(_cls, prompt_text, **_kwargs):
        asked.append(str(prompt_text))
        return answers.pop(0) if answers else "n"

    monkeypatch.setattr(rich.prompt.Prompt, "ask", classmethod(_fake_ask))
    return asked


def _console() -> Console:
    return Console(file=io.StringIO(), width=200)


def _console_text(console: Console) -> str:
    return console.file.getvalue()


def _flat_text(console: Console) -> str:
    """Console text with rich's line wrapping undone (one space per break)."""
    return " ".join(_console_text(console).split())


def _agent(llm: Any = None) -> SimpleNamespace:
    return SimpleNamespace(
        skill_context_buffer=SkillContextBuffer(),
        **({"llm_client": llm} if llm is not None else {}),
    )


def _audit_lines(home: Path) -> list[dict]:
    path = home / ".nexuscli" / "skill_history" / "audit.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _proposal_records(home: Path, name: str) -> list[tuple[Path, dict]]:
    return list_proposal_records(home / ".nexuscli" / "skill_history", name)


# ---------------------------------------------------------------------------
# propose.py: report loading / digesting
# ---------------------------------------------------------------------------


def test_load_report_missing_raises_with_path(tmp_path):
    with pytest.raises(ReportUnavailableError, match="last-report.json"):
        load_report(tmp_path, "hermetic")
    with pytest.raises(ReportUnavailableError, match="last-real-report.json"):
        load_report(tmp_path, "real")


def test_summarize_hermetic_extracts_failures_and_cost_warnings():
    digest = summarize_report(_hermetic_report(), track="hermetic")

    assert digest["track"] == "hermetic"
    assert digest["failures"] == [
        {
            "scenario": "todo_flow",
            "reasons": [
                "expected tool write_file was never called",
                "final answer missing the summary heading",
            ],
        }
    ]
    assert digest["cost_warnings"] == [
        {"scenario": "search_flow", "warnings": ["4 turns → 7 turns"]}
    ]
    assert digest["summary"]["failed"] == 1
    assert digest_has_signal(digest)


def test_summarize_real_extracts_run_failures():
    digest = summarize_report(_real_report(), track="real")

    assert digest["track"] == "real"
    assert digest["failures"] == [
        {
            "scenario": "judge_gate",
            "run": 1,
            "reasons": ["judge score 0.40 is below threshold 0.80: answer skipped the rubric"],
        }
    ]
    # The real track has no cost-warning concept; its cost context rides in
    # the report's own summary block.
    assert digest["cost_warnings"] == []
    assert digest["summary"]["failed"] == 1
    assert digest_has_signal(digest)


def test_digest_without_signal_is_the_no_input_case():
    digest = summarize_report(_all_pass_report(), track="hermetic")

    assert digest["failures"] == []
    assert digest["cost_warnings"] == []
    assert not digest_has_signal(digest)


# ---------------------------------------------------------------------------
# propose.py: LLM generation with the injected client
# ---------------------------------------------------------------------------


def test_generator_without_key_fails_through_config_chain(tmp_path, monkeypatch):
    _isolate_home(tmp_path, monkeypatch)
    _clear_llm_env(monkeypatch)

    with pytest.raises(ProposeCredentialsError, match="no LLM API key configured"):
        ProposalGenerator(cwd=tmp_path).generate("skill text", {"failures": []})


def test_generate_keeps_evidence_backed_items_and_drops_the_rest(tmp_path):
    skill_text = "---\nname: demo\ndescription: d\n---\nunique-skill-body-marker\n"
    digest = summarize_report(_hermetic_report(), track="hermetic")
    llm = FakeProposalLLM(_PROPOSAL_REPLY)

    proposal = ProposalGenerator(llm_client=llm).generate(skill_text, digest)

    assert [item.rule for item in proposal.items] == [
        "Always call write_file to persist the todo list before finishing",
        "Keep search within its previous turn budget",
    ]
    assert proposal.dropped == 1
    # Exactly one LLM call, carrying the skill text and the report digest.
    assert len(llm.calls) == 1
    user_message = llm.calls[0]["messages"][0]["content"]
    assert "unique-skill-body-marker" in user_message
    assert "todo_flow" in user_message
    assert "4 turns → 7 turns" in user_message


def test_generate_accepts_fenced_json_reply():
    digest = summarize_report(_hermetic_report(), track="hermetic")
    fenced = "```json\n" + _PROPOSAL_REPLY + "\n```"
    llm = FakeProposalLLM(fenced)

    proposal = ProposalGenerator(llm_client=llm).generate("body", digest)

    assert len(proposal.items) == 2
    assert proposal.dropped == 1


def test_generate_unparsable_reply_fails_loudly():
    from nexuscli.skill.propose import ProposalParseError

    llm = FakeProposalLLM("I would suggest improving things generally.")

    with pytest.raises(ProposalParseError, match="unparsable"):
        ProposalGenerator(llm_client=llm).generate("body", {"failures": []})


def test_evidence_gate_matches_names_and_verbatim_fragments_only():
    digest = summarize_report(_hermetic_report(), track="hermetic")

    # Scenario name (case-insensitive) is sufficient.
    assert _evidence_supported("TODO_FLOW failed", digest)
    # A verbatim failure-reason fragment (>= 12 chars) is sufficient.
    assert _evidence_supported(
        "the run ended because expected tool write_file was never called", digest
    )
    # Cost-warning scenario names count as evidence too.
    assert _evidence_supported("search_flow got slower", digest)
    # Paraphrase without any report anchor is not evidence.
    assert not _evidence_supported("the model seemed unsure", digest)
    assert not _evidence_supported("", digest)
    # A scenario the report never mentions is no evidence either.
    assert not _evidence_supported("scenario unreported_flow failed badly", digest)


# ---------------------------------------------------------------------------
# /skill propose — idle cases
# ---------------------------------------------------------------------------


def test_propose_missing_report_says_so_and_never_calls_llm(tmp_path, monkeypatch):
    home = _isolate_home(tmp_path, monkeypatch)
    _write_user_skill(home, "demo")
    llm = FakeProposalLLM(_PROPOSAL_REPLY)
    console = _console()

    _skill_command("propose demo", console, str(tmp_path), _agent(llm))

    text = _console_text(console)
    assert "eval report not found" in text
    assert "last-report.json" in text
    assert llm.calls == []
    assert _proposal_records(home, "demo") == []


def test_propose_report_without_signal_says_so_and_never_calls_llm(tmp_path, monkeypatch):
    home = _isolate_home(tmp_path, monkeypatch)
    _write_user_skill(home, "demo")
    _write_report(tmp_path, "last-report.json", _all_pass_report())
    llm = FakeProposalLLM(_PROPOSAL_REPLY)
    console = _console()

    _skill_command("propose demo", console, str(tmp_path), _agent(llm))

    text = _flat_text(console)
    assert "no failed scenarios and no cost warnings" in text
    assert llm.calls == []
    assert _proposal_records(home, "demo") == []


def test_propose_without_key_reports_config_chain_error(tmp_path, monkeypatch):
    home = _isolate_home(tmp_path, monkeypatch)
    _clear_llm_env(monkeypatch)
    _write_user_skill(home, "demo")
    _write_report(tmp_path, "last-report.json", _hermetic_report())
    console = _console()
    # No llm_client attribute: the generator must go through the config chain,
    # which fails loudly instead of silently skipping.
    agent = SimpleNamespace(skill_context_buffer=SkillContextBuffer())

    _skill_command("propose demo", console, str(tmp_path), agent)

    text = _console_text(console)
    assert "no LLM API key configured" in text
    assert "NEXUSCLI_API_KEY" in text
    assert _proposal_records(home, "demo") == []


def test_propose_unknown_skill_reports_not_found(tmp_path, monkeypatch):
    _isolate_home(tmp_path, monkeypatch)
    _write_report(tmp_path, "last-report.json", _hermetic_report())
    console = _console()

    _skill_command("propose nope", console, str(tmp_path), _agent())

    assert "not found in user skills" in _console_text(console)


def test_propose_invalid_slug_reports_instead_of_raising(tmp_path, monkeypatch):
    """A non-slug name must fail like evolve/rollback do — a one-line
    "propose failed:" message — not leak an uncaught ValueError from
    skill_path() into the REPL's generic error fallback."""
    _isolate_home(tmp_path, monkeypatch)
    console = _console()

    _skill_command("propose BadName", console, str(tmp_path), _agent())

    text = _console_text(console)
    assert "propose failed:" in text
    assert "lowercase slug" in text


def test_propose_rejects_unknown_report_track(tmp_path, monkeypatch):
    home = _isolate_home(tmp_path, monkeypatch)
    _write_user_skill(home, "demo")
    console = _console()

    _skill_command("propose demo --report nightly", console, str(tmp_path), _agent())

    assert "--report must be hermetic or real" in _console_text(console)
    assert _proposal_records(home, "demo") == []


# ---------------------------------------------------------------------------
# /skill propose — non-interactive (no TTY) fail-closed path
# ---------------------------------------------------------------------------


def test_non_interactive_propose_archives_without_landing(tmp_path, monkeypatch):
    home = _isolate_home(tmp_path, monkeypatch)
    skill_path = _write_user_skill(home, "demo")
    original = skill_path.read_text(encoding="utf-8")
    _write_report(tmp_path, "last-report.json", _hermetic_report())
    monkeypatch.setattr(sys, "stdin", _FakeStdin(isatty=False))
    console = _console()

    _skill_command("propose demo", console, str(tmp_path), _agent(FakeProposalLLM(_PROPOSAL_REPLY)))

    text = _flat_text(console)
    assert "archived without landing" in text
    assert "Review it in the REPL" in text
    # Fail-closed: nothing landed, no audit line, no version snapshot.
    assert skill_path.read_text(encoding="utf-8") == original
    assert _audit_lines(home) == []
    assert not (home / ".nexuscli" / "skill_history" / "demo" / "v1.md").exists()
    records = _proposal_records(home, "demo")
    assert len(records) == 1
    record = records[0][1]
    assert [item["decision"] for item in record["items"]] == ["pending", "pending"]
    assert record["applied_versions"] == []
    assert record["interactive"] is False
    assert record["track"] == "hermetic"
    assert record["report_summary"]["failures"][0]["scenario"] == "todo_flow"
    assert "write_file" in record["proposal"]


def test_propose_all_evidence_dropped_leaves_no_record(tmp_path, monkeypatch):
    home = _isolate_home(tmp_path, monkeypatch)
    _write_user_skill(home, "demo")
    _write_report(tmp_path, "last-report.json", _hermetic_report())
    unsupported = json.dumps([{"rule": "vibes", "evidence": "gut feeling"}, {"rule": "more vibes"}])
    console = _console()

    _skill_command("propose demo", console, str(tmp_path), _agent(FakeProposalLLM(unsupported)))

    text = _flat_text(console)
    assert "no evidence-backed proposals" in text
    assert "(1 dropped)" in text
    assert _proposal_records(home, "demo") == []
    assert _audit_lines(home) == []


# ---------------------------------------------------------------------------
# /skill propose — interactive review (y / n / s)
# ---------------------------------------------------------------------------


def test_review_y_lands_rule_with_snapshot_and_audit(tmp_path, monkeypatch):
    home = _isolate_home(tmp_path, monkeypatch)
    skill_path = _write_user_skill(home, "demo")
    original = skill_path.read_text(encoding="utf-8")
    _write_report(tmp_path, "last-report.json", _hermetic_report())
    monkeypatch.setattr(sys, "stdin", _FakeStdin(isatty=True))
    # y lands item 1, s skips the rest — and the post-landing real-eval offer
    # (only made because something landed) gets the final "n": declined.
    asked = _stub_review(monkeypatch, ["y", "s", "n"])
    console = _console()

    _skill_command("propose demo", console, str(tmp_path), _agent(FakeProposalLLM(_PROPOSAL_REPLY)))

    text = _console_text(console)
    # The reason line carries the item's evidence (the approval-prompt
    # convention).
    assert "Reason: scenario todo_flow: expected tool write_file was never called" in text
    # One prompt per item: y for item 1 (lands), s for item 2 (skips it and
    # would skip any rest), then the post-landing diff offer.
    assert len(asked) == 3
    assert "y / n / s" in asked[0]
    # Landing went through SkillEvolutionStore: rule appended, pre-change
    # state frozen, one audit line.
    content = skill_path.read_text(encoding="utf-8")
    assert "Always call write_file to persist the todo list before finishing" in content
    assert "(reason: propose)" in content
    v1 = (home / ".nexuscli" / "skill_history" / "demo" / "v1.md").read_text(encoding="utf-8")
    assert v1 == original
    audit = _audit_lines(home)
    assert len(audit) == 1
    assert audit[0]["op"] == "save"
    assert audit[0]["version"] == 1
    assert audit[0]["reason"] == "propose"
    # Record: per-item decisions with the landing version.
    records = _proposal_records(home, "demo")
    assert len(records) == 1
    record = records[0][1]
    assert [item["decision"] for item in record["items"]] == ["applied", "skipped"]
    assert record["items"][0]["version"] == 1
    assert record["items"][1]["version"] is None
    assert record["applied_versions"] == [1]
    assert record["interactive"] is True
    assert "Applied 1/2" in text
    # The post-landing offer was made (something landed) and declined.
    assert record["post_landing"] == {"declined": True}


def test_review_n_skips_everything_but_still_archives(tmp_path, monkeypatch):
    home = _isolate_home(tmp_path, monkeypatch)
    skill_path = _write_user_skill(home, "demo")
    original = skill_path.read_text(encoding="utf-8")
    _write_report(tmp_path, "last-report.json", _hermetic_report())
    monkeypatch.setattr(sys, "stdin", _FakeStdin(isatty=True))
    _stub_review(monkeypatch, ["n", "n"])
    console = _console()

    _skill_command("propose demo", console, str(tmp_path), _agent(FakeProposalLLM(_PROPOSAL_REPLY)))

    assert skill_path.read_text(encoding="utf-8") == original
    assert _audit_lines(home) == []
    records = _proposal_records(home, "demo")
    assert len(records) == 1
    record = records[0][1]
    assert [item["decision"] for item in record["items"]] == ["skipped", "skipped"]
    assert record["applied_versions"] == []
    assert "No rules applied" in _console_text(console)


def test_review_s_skips_the_rest_without_prompting(tmp_path, monkeypatch):
    home = _isolate_home(tmp_path, monkeypatch)
    skill_path = _write_user_skill(home, "demo")
    _write_report(tmp_path, "last-report.json", _hermetic_report())
    monkeypatch.setattr(sys, "stdin", _FakeStdin(isatty=True))
    asked = _stub_review(monkeypatch, ["n", "s"])
    console = _console()

    _skill_command("propose demo", console, str(tmp_path), _agent(FakeProposalLLM(_PROPOSAL_REPLY)))

    # Item 1 asked (n), item 2 asked (s) and stops the loop — with both items
    # answered, nothing is left pending either way.
    assert len(asked) == 2
    assert "Skipping the remaining items" in _console_text(console)
    assert (
        skill_path.read_text(encoding="utf-8")
        == "---\nname: demo\ndescription: demo desc\n---\ndemo body\n"
    )
    records = _proposal_records(home, "demo")
    record = records[0][1]
    assert [item["decision"] for item in record["items"]] == ["skipped", "skipped"]
    assert record["applied_versions"] == []


# ---------------------------------------------------------------------------
# --report real (fabricated real-track report; no network)
# ---------------------------------------------------------------------------


def test_propose_real_track_reads_real_report(tmp_path, monkeypatch):
    home = _isolate_home(tmp_path, monkeypatch)
    _write_user_skill(home, "demo")
    _write_report(tmp_path, "last-real-report.json", _real_report())
    monkeypatch.setattr(sys, "stdin", _FakeStdin(isatty=False))
    llm = FakeProposalLLM(
        json.dumps(
            [
                {
                    "rule": "Quote the rubric line the answer relies on",
                    "evidence": "scenario judge_gate: judge score 0.40 is below threshold 0.80",
                }
            ]
        )
    )
    console = _console()

    _skill_command("propose demo --report real", console, str(tmp_path), _agent(llm))

    text = _console_text(console)
    assert "archived without landing" in text
    record = _proposal_records(home, "demo")[0][1]
    assert record["track"] == "real"
    assert record["report_path"].endswith("last-real-report.json")
    assert record["report_summary"]["failures"][0]["scenario"] == "judge_gate"
    assert [item["decision"] for item in record["items"]] == ["pending"]
    # The hermetic baseline file was never consulted (and does not exist here).
    assert not (tmp_path / ".nexuscli" / "evals" / "last-report.json").exists()


# ---------------------------------------------------------------------------
# /skill proposals — listing
# ---------------------------------------------------------------------------


def test_proposals_lists_records_with_counts(tmp_path, monkeypatch):
    home = _isolate_home(tmp_path, monkeypatch)
    _write_user_skill(home, "demo")
    _write_report(tmp_path, "last-report.json", _hermetic_report())
    monkeypatch.setattr(sys, "stdin", _FakeStdin(isatty=False))
    first = _console()
    _skill_command("propose demo", first, str(tmp_path), _agent(FakeProposalLLM(_PROPOSAL_REPLY)))
    second = _console()
    _skill_command("propose demo", second, str(tmp_path), _agent(FakeProposalLLM(_PROPOSAL_REPLY)))

    listing = _console()
    _skill_command("proposals demo", listing, str(tmp_path), _agent())

    lines = [line for line in _console_text(listing).splitlines() if "items=" in line]
    assert len(lines) == 2
    assert all(
        "items=2" in line and "applied=0" in line and "track=hermetic" in line for line in lines
    )


def test_proposals_empty_reports_no_records(tmp_path, monkeypatch):
    home = _isolate_home(tmp_path, monkeypatch)
    _write_user_skill(home, "demo")
    console = _console()

    _skill_command("proposals demo", console, str(tmp_path), _agent())

    assert "No proposal records" in _console_text(console)


# ---------------------------------------------------------------------------
# Zero regression on the existing /skill evolve surface
# ---------------------------------------------------------------------------


def test_evolve_semantics_unchanged_alongside_propose(tmp_path, monkeypatch):
    home = _isolate_home(tmp_path, monkeypatch)
    skill_path = _write_user_skill(home, "demo")
    original = skill_path.read_text(encoding="utf-8")
    _write_report(tmp_path, "last-report.json", _hermetic_report())
    monkeypatch.setattr(sys, "stdin", _FakeStdin(isatty=False))

    evolve_console = _console()
    _skill_command("evolve demo manual rule still works", evolve_console, str(tmp_path), _agent())
    assert "manual rule still works" in skill_path.read_text(encoding="utf-8")
    audit = _audit_lines(home)
    assert len(audit) == 1
    assert audit[0]["reason"] == "evolve"

    # Proposals listing is empty before any propose run and unaffected by
    # evolve; a propose run then adds records without touching evolve history.
    before = _console()
    _skill_command("proposals demo", before, str(tmp_path), _agent())
    assert "No proposal records" in _console_text(before)

    propose_console = _console()
    _skill_command(
        "propose demo", propose_console, str(tmp_path), _agent(FakeProposalLLM(_PROPOSAL_REPLY))
    )
    audit = _audit_lines(home)
    # Still exactly the one evolve line: the non-interactive propose landed
    # nothing (fail-closed).
    assert [record["reason"] for record in audit] == ["evolve"]
    # The skill gained only the manual rule, not any proposal.
    content = skill_path.read_text(encoding="utf-8")
    assert "manual rule still works" in content
    assert "Always call write_file" not in content
    assert content.startswith("---")  # frontmatter untouched
    assert original in content

    stats_console = _console()
    _skill_command("stats", stats_console, str(tmp_path), _agent())
    stats_line = next(
        (line for line in _console_text(stats_console).splitlines() if line.startswith("demo ")),
        "",
    )
    assert "version=1" in stats_line
    assert "history=1" in stats_line

    after = _console()
    _skill_command("proposals demo", after, str(tmp_path), _agent())
    assert len([line for line in _console_text(after).splitlines() if "items=" in line]) == 1


def test_save_proposal_record_collision_suffix(tmp_path):
    root = tmp_path / "history"
    record = {"skill": "demo", "items": []}

    first = save_proposal_record(root, "demo", record)
    second = save_proposal_record(root, "demo", record)

    assert first != second
    assert first.parent == second.parent
    assert json.loads(first.read_text(encoding="utf-8"))["skill"] == "demo"
    assert json.loads(second.read_text(encoding="utf-8"))["skill"] == "demo"
    assert len(list_proposal_records(root, "demo")) == 2


# ---------------------------------------------------------------------------
# run_post_landing_diff — the four-step backup → run → diff → restore
# (all runner/LLM seams injected; no network, no real gateway)
# ---------------------------------------------------------------------------


def _scenario03_report(*, passed: bool, reasons: list[str] | None = None) -> dict[str, Any]:
    """A single-scenario real-track report — the post-landing run's shape
    (scenario 03, the skill-rule-follow validator). A failed run carries a
    realistic judge-score reason unless the test names its own."""
    if reasons is None:
        reasons = (
            []
            if passed
            else ["judge score 0.40 is below threshold 0.80: answer skipped the rubric"]
        )
    return {
        "generated_at": "2026-11-02T12:00:00",
        "track": "real",
        "repeat": 1,
        "scenarios": [
            {
                "name": "real-skill-rule-follow",
                "source": "examples/evals/real/03_skill_rule_follow.json",
                "pass_k": {
                    "repeat": 1,
                    "passed_runs": 1 if passed else 0,
                    "failed_runs": 0 if passed else 1,
                    "all_pass": passed,
                },
                "cost": {"runs": 1, "total_usage": {}, "avg_usage": {}, "total_cost": {}},
                "runs": [
                    {
                        "index": 1,
                        "status": "pass" if passed else "fail",
                        "failure_reasons": list(reasons or []),
                        "tool_sequence": ["load_skill"],
                        "turns": 3,
                        "usage": {},
                        "cost": {},
                        "judge": None,
                    }
                ],
            }
        ],
        "summary": {
            "total_runs": 1,
            "passed": 1 if passed else 0,
            "failed": 0 if passed else 1,
            "scenarios_total": 1,
            "scenarios_all_pass": 1 if passed else 0,
            "all_pass_rate": 1.0 if passed else 0.0,
            "total_usage": {},
            "total_cost": {},
        },
    }


def _nightly_before_report(*, scenario03_passed: bool) -> dict[str, Any]:
    """A pre-landing nightly-shaped real report: judge_gate plus scenario 03
    (the post-landing diff looks the same scenario up by name)."""
    judge_gate = _real_report()["scenarios"][0]
    scenario03 = _scenario03_report(passed=scenario03_passed)["scenarios"][0]
    return {
        "generated_at": "2026-11-02T09:00:00",
        "track": "real",
        "repeat": 1,
        "scenarios": [judge_gate, scenario03],
        "summary": {
            "total_runs": 2,
            "passed": 1 if scenario03_passed else 0,
            "failed": 2 - (1 if scenario03_passed else 0),
            "scenarios_total": 2,
            "scenarios_all_pass": 1 if scenario03_passed else 0,
            "all_pass_rate": 0.5 if scenario03_passed else 0.0,
            "total_usage": {},
            "total_cost": {},
        },
    }


def test_post_landing_confirmed_runs_diff_and_restores_bytes(tmp_path, monkeypatch):
    _isolate_home(tmp_path, monkeypatch)
    report_path = _write_report(
        tmp_path, "last-real-report.json", _nightly_before_report(scenario03_passed=False)
    )
    original_bytes = report_path.read_bytes()
    after = _scenario03_report(passed=True)
    runner_calls: list[dict[str, Any]] = []
    reasons: list[str] = []

    def fake_runner() -> dict[str, Any]:
        runner_calls.append({})
        return after

    def fake_confirm(reason: str) -> bool:
        reasons.append(reason)
        return True

    record: dict[str, Any] = {"skill": "rule-demo", "applied_versions": [1]}
    out = run_post_landing_diff(
        record, tmp_path, runner=fake_runner, confirm=fake_confirm, interactive=True
    )

    assert len(runner_calls) == 1
    post = out["post_landing"]
    # Both diff sides: status / pass_k.all_pass / failure reasons / all_pass_rate.
    assert post["scenario"] == "real-skill-rule-follow"
    assert post["before"]["status"] == "fail"
    assert post["before"]["all_pass"] is False
    assert post["before"]["failure_reasons"] == [
        "judge score 0.40 is below threshold 0.80: answer skipped the rubric"
    ]
    assert post["before"]["all_pass_rate"] == 0.0
    assert post["after"]["status"] == "pass"
    assert post["after"]["all_pass"] is True
    assert post["after"]["failure_reasons"] == []
    assert post["after"]["all_pass_rate"] == 1.0
    # Each rate rides with its denominator: the before side is the full
    # nightly aggregate, the after side the single-scenario run — the record
    # must show they are not like-for-like.
    assert post["before"]["scenarios_total"] == 2
    assert post["after"]["scenarios_total"] == 1
    assert post["report_restored"] is True
    assert "warnings" not in post
    # The nightly baseline file is back, byte for byte.
    assert report_path.read_bytes() == original_bytes
    # The confirm gate was asked with the real-call Reason line.
    assert "real gateway call" in reasons[0]
    assert "03_skill_rule_follow" in reasons[0]
    assert "10k-20k tokens" in reasons[0]


def test_post_landing_records_after_fail_side(tmp_path, monkeypatch):
    _isolate_home(tmp_path, monkeypatch)
    report_path = _write_report(
        tmp_path, "last-real-report.json", _nightly_before_report(scenario03_passed=True)
    )
    after = _scenario03_report(
        passed=False, reasons=["judge score 0.30 is below threshold 0.80: rule not followed"]
    )

    out = run_post_landing_diff(
        {"skill": "rule-demo"},
        tmp_path,
        runner=lambda: after,
        confirm=lambda reason: True,
        interactive=True,
    )

    post = out["post_landing"]
    assert post["before"]["status"] == "pass"
    assert post["before"]["all_pass_rate"] == 0.5
    assert post["after"]["status"] == "fail"
    assert post["after"]["all_pass"] is False
    assert post["after"]["failure_reasons"] == [
        "judge score 0.30 is below threshold 0.80: rule not followed"
    ]
    assert report_path.is_file()  # restored (byte check is the dedicated test's job)


def test_post_landing_declined_records_and_never_runs(tmp_path, monkeypatch):
    _isolate_home(tmp_path, monkeypatch)
    report_path = _write_report(tmp_path, "last-real-report.json", _real_report())
    original_bytes = report_path.read_bytes()
    reasons: list[str] = []

    def forbidden_runner() -> dict[str, Any]:
        raise AssertionError("runner must not run after a declined confirm")

    def refusing_confirm(reason: str) -> bool:
        reasons.append(reason)
        return False

    record: dict[str, Any] = {"skill": "rule-demo"}
    out = run_post_landing_diff(
        record, tmp_path, runner=forbidden_runner, confirm=refusing_confirm, interactive=True
    )

    # Declined path archives exactly the declined fact: no before/after, no
    # report_restored (nothing was ever touched).
    assert out["post_landing"] == {"declined": True}
    assert "real gateway call" in reasons[0]
    assert report_path.read_bytes() == original_bytes


def test_post_landing_runner_offloads_inside_running_loop(tmp_path, monkeypatch):
    """The REPL dispatches slash commands on the event loop thread
    (``_handle_slash`` is async); a real runner's per-scenario
    ``asyncio.run`` must be offloaded, not crash on the running loop."""
    import asyncio

    _isolate_home(tmp_path, monkeypatch)
    report_path = _write_report(
        tmp_path, "last-real-report.json", _nightly_before_report(scenario03_passed=False)
    )
    original_bytes = report_path.read_bytes()
    after = _scenario03_report(passed=True)

    async def repl_like_dispatch() -> dict[str, Any]:
        # A running loop is active here — exactly the REPL's situation.
        return run_post_landing_diff(
            {"skill": "rule-demo"},
            tmp_path,
            runner=lambda: after,
            confirm=lambda reason: True,
            interactive=True,
        )

    out = asyncio.run(repl_like_dispatch())

    post = out["post_landing"]
    assert post["after"]["status"] == "pass"
    assert post["report_restored"] is True
    assert report_path.read_bytes() == original_bytes


def test_post_landing_non_interactive_skips(tmp_path, monkeypatch):
    _isolate_home(tmp_path, monkeypatch)
    report_path = _write_report(tmp_path, "last-real-report.json", _real_report())
    original_bytes = report_path.read_bytes()

    def forbidden_runner() -> dict[str, Any]:
        raise AssertionError("runner must not run without a human attached")

    # Explicit flag from the caller.
    out = run_post_landing_diff(
        {"skill": "rule-demo"},
        tmp_path,
        runner=forbidden_runner,
        confirm=lambda reason: True,
        interactive=False,
    )
    assert out["post_landing"] == {"skipped_reason": "non-interactive"}

    # Or the internal isatty fallback (skill.py's existing gate style).
    monkeypatch.setattr(sys, "stdin", _FakeStdin(isatty=False))
    out = run_post_landing_diff(
        {"skill": "rule-demo"},
        tmp_path,
        runner=forbidden_runner,
        confirm=lambda reason: True,
    )
    assert out["post_landing"] == {"skipped_reason": "non-interactive"}
    assert report_path.read_bytes() == original_bytes


def test_post_landing_default_runner_without_key_records_guidance(tmp_path, monkeypatch):
    """runner=None with no credential: the real track's fail-loud contract
    surfaces as a recorded error with install guidance, and the backup is
    still restored (nothing was overwritten)."""
    _isolate_home(tmp_path, monkeypatch)
    _clear_llm_env(monkeypatch)
    report_path = _write_report(tmp_path, "last-real-report.json", _real_report())
    original_bytes = report_path.read_bytes()

    out = run_post_landing_diff(
        {"skill": "rule-demo"}, tmp_path, runner=None, confirm=lambda reason: True, interactive=True
    )

    post = out["post_landing"]
    assert post["errors"], post
    assert "NEXUSCLI_API_KEY" in post["errors"][0]
    # No diff happened, so neither side is invented.
    assert "before" not in post
    assert "after" not in post
    assert post["report_restored"] is True
    assert report_path.read_bytes() == original_bytes


def test_post_landing_runner_error_still_restores(tmp_path, monkeypatch):
    _isolate_home(tmp_path, monkeypatch)
    report_path = _write_report(
        tmp_path, "last-real-report.json", _nightly_before_report(scenario03_passed=False)
    )
    original_bytes = report_path.read_bytes()

    def exploding_runner() -> dict[str, Any]:
        raise RuntimeError("eval exploded")

    out = run_post_landing_diff(
        {"skill": "rule-demo"},
        tmp_path,
        runner=exploding_runner,
        confirm=lambda reason: True,
        interactive=True,
    )

    post = out["post_landing"]
    assert post["errors"] == ["eval exploded"]
    assert "after" not in post
    # The restore runs even though the runner blew up.
    assert post["report_restored"] is True
    assert report_path.read_bytes() == original_bytes


def test_post_landing_interrupted_run_marks_record_and_restores(tmp_path, monkeypatch):
    """Ctrl+C mid real run: the KeyboardInterrupt propagates, but the record
    already carries the interruption marker plus the restore state — a half
    run must be visible in the archive, not silently missing."""
    _isolate_home(tmp_path, monkeypatch)
    report_path = _write_report(
        tmp_path, "last-real-report.json", _nightly_before_report(scenario03_passed=False)
    )
    original_bytes = report_path.read_bytes()

    def interrupted_runner() -> dict[str, Any]:
        raise KeyboardInterrupt

    record: dict[str, Any] = {"skill": "rule-demo"}
    with pytest.raises(KeyboardInterrupt):
        run_post_landing_diff(
            record,
            tmp_path,
            runner=interrupted_runner,
            confirm=lambda reason: True,
            interactive=True,
        )

    post = record["post_landing"]
    assert "interrupted before completing" in post["errors"][0]
    assert "KeyboardInterrupt" in post["errors"][0]
    assert post["report_restored"] is True
    assert report_path.read_bytes() == original_bytes


def test_post_landing_restore_failure_warns_and_flags(tmp_path, monkeypatch):
    """A restore that cannot write back must be alarmed once and archived as
    report_restored=false — never a silent pollution of the nightly baseline."""
    _isolate_home(tmp_path, monkeypatch)
    report_path = _write_report(
        tmp_path, "last-real-report.json", _nightly_before_report(scenario03_passed=False)
    )

    def sabotaging_runner() -> dict[str, Any]:
        # Mid-run, the report file becomes a directory: the restore's
        # write_bytes now fails with IsADirectoryError.
        report_path.unlink()
        report_path.mkdir()
        return _scenario03_report(passed=True)

    out = run_post_landing_diff(
        {"skill": "rule-demo"},
        tmp_path,
        runner=sabotaging_runner,
        confirm=lambda reason: True,
        interactive=True,
    )

    post = out["post_landing"]
    # The diff itself still landed in the record.
    assert post["after"]["status"] == "pass"
    assert post["report_restored"] is False
    assert post["warnings"]
    assert "FAILED to restore" in post["warnings"][0]
    assert "nightly" in post["warnings"][0]


def test_post_landing_without_prior_report_restores_absence(tmp_path, monkeypatch):
    """Missing last-real-report.json: before=None, the diff still runs, and
    restoring means removing the diff run's report again — the file's absence
    is the pre-run state."""
    _isolate_home(tmp_path, monkeypatch)
    report_path = tmp_path / ".nexuscli" / "evals" / "last-real-report.json"

    out = run_post_landing_diff(
        {"skill": "rule-demo"},
        tmp_path,
        runner=lambda: _scenario03_report(passed=True),
        confirm=lambda reason: True,
        interactive=True,
    )

    post = out["post_landing"]
    assert post["before"] is None
    assert post["after"]["status"] == "pass"
    assert post["report_restored"] is True
    assert not report_path.exists()


def test_post_landing_backup_restore_is_byte_identical(tmp_path, monkeypatch):
    """The restore writes the backed-up bytes back exactly — unicode, stray
    whitespace and all; no re-serialization may touch the baseline."""
    _isolate_home(tmp_path, monkeypatch)
    report_path = tmp_path / ".nexuscli" / "evals" / "last-real-report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    raw_bytes = (
        '{"scenarios": [{"name": "real-skill-rule-follow", '
        '"pass_k": {"all_pass": false}, '
        '"runs": [{"status": "fail", "failure_reasons": ["判定分数 0.40 低于阈值 0.80"]}]}],'
        ' "summary": {"all_pass_rate": 0.0}}  \n'
    ).encode()
    report_path.write_bytes(raw_bytes)

    out = run_post_landing_diff(
        {"skill": "rule-demo"},
        tmp_path,
        runner=lambda: _scenario03_report(passed=True),
        confirm=lambda reason: True,
        interactive=True,
    )

    assert out["post_landing"]["before"]["failure_reasons"] == ["判定分数 0.40 低于阈值 0.80"]
    assert report_path.read_bytes() == raw_bytes


# ---------------------------------------------------------------------------
# /skill propose — post-landing wiring (second confirm gate; records rewrite)
# ---------------------------------------------------------------------------


def test_propose_wiring_post_landing_confirmed_end_to_end(tmp_path, monkeypatch):
    """The full /skill propose chain: review lands an item, the post-landing
    offer is made (Reason line), y runs the diff through the REAL four-step
    function with only the scenario runner faked, and the SAME record file is
    rewritten with the post_landing block."""
    import nexuscli.eval.real as eval_real

    home = _isolate_home(tmp_path, monkeypatch)
    _write_user_skill(home, "demo")
    _write_report(tmp_path, "last-report.json", _hermetic_report())
    report_path = _write_report(
        tmp_path, "last-real-report.json", _nightly_before_report(scenario03_passed=False)
    )
    original_bytes = report_path.read_bytes()
    monkeypatch.setattr(sys, "stdin", _FakeStdin(isatty=True))
    # y lands item 1, n skips item 2, y confirms the post-landing diff.
    _stub_review(monkeypatch, ["y", "n", "y"])

    def fake_run_real_scenarios(scenarios, *, root, repeat=1, llm_client_factory=None, judge=None):
        assert len(scenarios) == 1  # --file semantics: scenario 03 only
        return [
            eval_real.RealEvalResult(
                name="real-skill-rule-follow", passed=True, source=str(scenarios[0].source)
            )
        ]

    monkeypatch.setattr(eval_real, "run_real_scenarios", fake_run_real_scenarios)
    console = _console()

    _skill_command("propose demo", console, str(tmp_path), _agent(FakeProposalLLM(_PROPOSAL_REPLY)))

    text = _flat_text(console)
    assert "Reason: real gateway call" in text
    assert "03_skill_rule_follow" in text
    assert "10k-20k tokens" in text
    assert "Post-landing diff" in text
    # The rates on screen carry their denominators (nightly aggregate vs
    # single-scenario run) so the arrow cannot read as like-for-like.
    assert "all_pass_rate 0.0 (2 scenarios) → 1.0 (1 scenario)" in text
    records = _proposal_records(home, "demo")
    assert len(records) == 1  # rewritten in place, not forked into a second file
    post = records[0][1]["post_landing"]
    assert post["scenario"] == "real-skill-rule-follow"
    assert post["before"]["status"] == "fail"
    assert post["after"]["status"] == "pass"
    assert post["after"]["all_pass_rate"] == 1.0
    assert post["report_restored"] is True
    # The nightly baseline survived the whole REPL-side flow byte for byte.
    assert report_path.read_bytes() == original_bytes


def test_propose_wiring_post_landing_rewrite_survives_interrupt(tmp_path, monkeypatch):
    """Ctrl+C after confirmation: the interrupt propagates out of the command,
    but the record file on disk still gained the post_landing block — the
    rewrite is a finally, so an unwinding run cannot leave the archive stale."""
    import nexuscli.entrypoints.commands.skill as skill_module

    home = _isolate_home(tmp_path, monkeypatch)
    _write_user_skill(home, "demo")
    _write_report(tmp_path, "last-report.json", _hermetic_report())
    monkeypatch.setattr(sys, "stdin", _FakeStdin(isatty=True))
    # y lands item 1, n skips item 2; the (stubbed) post-landing run then dies.
    _stub_review(monkeypatch, ["y", "n"])

    def interrupted_diff(record, cwd, **_kwargs):
        record["post_landing"] = {
            "errors": ["post-landing diff interrupted before completing (KeyboardInterrupt)"],
            "report_restored": True,
        }
        raise KeyboardInterrupt

    monkeypatch.setattr(skill_module, "run_post_landing_diff", interrupted_diff)
    console = _console()

    with pytest.raises(KeyboardInterrupt):
        _skill_command(
            "propose demo", console, str(tmp_path), _agent(FakeProposalLLM(_PROPOSAL_REPLY))
        )

    record = _proposal_records(home, "demo")[0][1]
    assert record["post_landing"]["report_restored"] is True
    assert "interrupted before completing" in record["post_landing"]["errors"][0]


def test_propose_wiring_post_landing_declined_records_without_run(tmp_path, monkeypatch):
    home = _isolate_home(tmp_path, monkeypatch)
    _clear_llm_env(monkeypatch)
    _write_user_skill(home, "demo")
    _write_report(tmp_path, "last-report.json", _hermetic_report())
    monkeypatch.setattr(sys, "stdin", _FakeStdin(isatty=True))
    # y lands item 1, n skips item 2, n declines the post-landing diff.
    _stub_review(monkeypatch, ["y", "n", "n"])
    console = _console()

    _skill_command("propose demo", console, str(tmp_path), _agent(FakeProposalLLM(_PROPOSAL_REPLY)))

    text = _flat_text(console)
    assert "Post-landing real-eval diff declined" in text
    # Nothing ever reached the gateway: no credential error anywhere.
    assert "NEXUSCLI_API_KEY" not in text
    records = _proposal_records(home, "demo")
    assert len(records) == 1
    assert records[0][1]["post_landing"] == {"declined": True}


def test_propose_wiring_post_landing_without_key_records_guidance(tmp_path, monkeypatch):
    """Confirmed with no key: the recorded error (with install guidance) is
    printed, the landing itself is untouched, and the report is restored."""
    home = _isolate_home(tmp_path, monkeypatch)
    _clear_llm_env(monkeypatch)
    _write_user_skill(home, "demo")
    _write_report(tmp_path, "last-report.json", _hermetic_report())
    report_path = _write_report(tmp_path, "last-real-report.json", _real_report())
    original_bytes = report_path.read_bytes()
    monkeypatch.setattr(sys, "stdin", _FakeStdin(isatty=True))
    _stub_review(monkeypatch, ["y", "n", "y"])
    console = _console()

    _skill_command("propose demo", console, str(tmp_path), _agent(FakeProposalLLM(_PROPOSAL_REPLY)))

    text = _flat_text(console)
    assert "post-landing diff failed" in text
    assert "NEXUSCLI_API_KEY" in text
    records = _proposal_records(home, "demo")
    post = records[0][1]["post_landing"]
    assert "NEXUSCLI_API_KEY" in post["errors"][0]
    assert post["report_restored"] is True
    assert report_path.read_bytes() == original_bytes


def test_propose_wiring_post_landing_not_offered_without_applied(tmp_path, monkeypatch):
    home = _isolate_home(tmp_path, monkeypatch)
    _write_user_skill(home, "demo")
    _write_report(tmp_path, "last-report.json", _hermetic_report())
    monkeypatch.setattr(sys, "stdin", _FakeStdin(isatty=True))
    asked = _stub_review(monkeypatch, ["n", "n"])
    console = _console()

    _skill_command("propose demo", console, str(tmp_path), _agent(FakeProposalLLM(_PROPOSAL_REPLY)))

    # Nothing landed → no post-landing offer, no post_landing field at all.
    assert len(asked) == 2
    assert "real gateway call" not in _flat_text(console)
    record = _proposal_records(home, "demo")[0][1]
    assert "post_landing" not in record
