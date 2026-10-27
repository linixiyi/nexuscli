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
    asked = _stub_review(monkeypatch, ["y", "s"])
    console = _console()

    _skill_command("propose demo", console, str(tmp_path), _agent(FakeProposalLLM(_PROPOSAL_REPLY)))

    text = _console_text(console)
    # The reason line carries the item's evidence (the approval-prompt
    # convention).
    assert "Reason: scenario todo_flow: expected tool write_file was never called" in text
    # One prompt per item: y for item 1 (lands), s for item 2 (skips it and
    # would skip any rest).
    assert len(asked) == 2
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
