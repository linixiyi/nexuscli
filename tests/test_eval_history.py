"""Tests for the eval history snapshots and the ``eval history`` listing.

Spec 2026-11-03_add-eval-history-and-skill-polish (task 2): every successful
run appends the SAME report document as a UTC-second-stamped snapshot under
``.nexuscli/evals/history/<track>/`` — best-effort by contract: a snapshot
failure degrades to exactly one :class:`EvalHistoryWarning` and never blocks
the run or the main report. ``nexuscli eval history [N]`` lists the newest
snapshots per track (time, passed/total, exit verdict, and the track's
headline signal). The three existing report/comparison files keep their
exact paths, writers and schemas — the history feature only ever ADDS
snapshot files, it never mutates a main report.
"""

from __future__ import annotations

import json
import re
import warnings
from pathlib import Path

from typer.testing import CliRunner

import nexuscli.entrypoints.cli as cli
import nexuscli.eval.reporter as reporter
from nexuscli.eval import (
    EVAL_HISTORY_RELATIVE,
    REAL_REPORT_RELATIVE,
    REPORT_RELATIVE,
    append_report_history,
    build_real_report,
    build_report,
    write_real_report,
    write_report,
)
from nexuscli.eval.real import (
    REAL_COMPARISON_RELATIVE,
    RealEvalResult,
    append_real_report_history,
)
from nexuscli.eval.runner import EvalRunResult

runner = CliRunner()

_STAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}Z$")


def _hermetic_report(passed: bool = True) -> dict:
    result = EvalRunResult(
        name="s1",
        passed=passed,
        failure_reasons=[] if passed else ["boom"],
        turns=2,
    )
    return build_report([result], previous=None)


def _real_report(passed: bool = True) -> dict:
    return build_real_report(
        [RealEvalResult(name="r1", passed=passed, failure_reasons=[] if passed else ["boom"])],
        repeat=1,
    )


def _hermetic_doc(passed: int, total: int, cost_increased: int) -> dict:
    """A minimal hand-built hermetic report for the listing tests."""
    return {
        "generated_at": "2026-01-01T08:00:00",
        "scenarios": [],
        "summary": {
            "total": total,
            "passed": passed,
            "failed": total - passed,
            "regressions": 0,
            "fixed": 0,
            "cost_increased": cost_increased,
            "cost_decreased": 0,
            "cost_unchanged": 0,
        },
    }


def _real_doc(passed: int, total_runs: int, all_pass_rate: float) -> dict:
    """A minimal hand-built real-track report for the listing tests."""
    return {
        "generated_at": "2026-01-01T08:00:00",
        "track": "real",
        "repeat": 3,
        "scenarios": [],
        "summary": {
            "total_runs": total_runs,
            "passed": passed,
            "failed": total_runs - passed,
            "scenarios_total": 3,
            "scenarios_all_pass": 1,
            "all_pass_rate": all_pass_rate,
        },
    }


def _snapshots(root: Path, track: str) -> list[Path]:
    directory = root / EVAL_HISTORY_RELATIVE / track
    return sorted(directory.glob("*.json")) if directory.is_dir() else []


def _append_stamp(root: Path, doc: dict, track: str, stamp: str, monkeypatch) -> None:
    monkeypatch.setattr(reporter, "_history_stamp", lambda s=stamp: s)
    path = append_report_history(root, doc, track=track)
    assert path is not None, "controlled-stamp append must succeed"


# ---------------------------------------------------------------------------
# Snapshot append — the two writers hook history without changing the reports
# ---------------------------------------------------------------------------


def test_write_report_appends_timestamped_snapshot(tmp_path):
    report = _hermetic_report()
    report_path = write_report(tmp_path, report)

    snapshots = _snapshots(tmp_path, "hermetic")
    assert len(snapshots) == 1
    assert _STAMP_RE.fullmatch(snapshots[0].stem), snapshots[0].name
    # The snapshot is the SAME document: byte-identical serialization.
    assert snapshots[0].read_text(encoding="utf-8") == report_path.read_text(encoding="utf-8")
    assert json.loads(snapshots[0].read_text(encoding="utf-8")) == report
    # The main report keeps its exact path and its exact content (no schema
    # drift from the history feature).
    assert report_path == tmp_path / REPORT_RELATIVE
    assert json.loads(report_path.read_text(encoding="utf-8")) == report
    # History only ever ADDS the hermetic snapshot: the other track's report,
    # the comparison file, and the real history dir are never created here.
    assert not (tmp_path / REAL_REPORT_RELATIVE).exists()
    assert not (tmp_path / REAL_COMPARISON_RELATIVE).exists()
    assert not (tmp_path / EVAL_HISTORY_RELATIVE / "real").exists()


def test_write_real_report_appends_snapshot_and_leaves_hermetic_alone(tmp_path):
    report = _real_report()
    report_path = write_real_report(tmp_path, report)

    snapshots = _snapshots(tmp_path, "real")
    assert len(snapshots) == 1
    assert _STAMP_RE.fullmatch(snapshots[0].stem), snapshots[0].name
    assert snapshots[0].read_text(encoding="utf-8") == report_path.read_text(encoding="utf-8")
    assert json.loads(report_path.read_text(encoding="utf-8")) == report
    # A real run never touches the hermetic baseline or the comparison file.
    assert not (tmp_path / REPORT_RELATIVE).exists()
    assert not (tmp_path / REAL_COMPARISON_RELATIVE).exists()
    assert not (tmp_path / EVAL_HISTORY_RELATIVE / "hermetic").exists()


def test_same_second_snapshots_collision_suffix(tmp_path, monkeypatch):
    first = _hermetic_report(passed=True)
    second = _hermetic_report(passed=False)
    third = _real_report()

    monkeypatch.setattr(reporter, "_history_stamp", lambda: "2026-06-01T00-00-00Z")
    write_report(tmp_path, first)
    write_report(tmp_path, second)
    write_real_report(tmp_path, third)

    hermetic = _snapshots(tmp_path, "hermetic")
    real = _snapshots(tmp_path, "real")
    hermetic_dir = tmp_path / EVAL_HISTORY_RELATIVE / "hermetic"
    base = hermetic_dir / "2026-06-01T00-00-00Z.json"
    collided = hermetic_dir / "2026-06-01T00-00-00Z_02.json"
    assert {path.name for path in hermetic} == {base.name, collided.name}
    assert [path.name for path in real] == ["2026-06-01T00-00-00Z.json"]
    # The zero-padded suffix sorts AFTER the base name ('_' > '.'): the CLI's
    # lexicographic newest-first stays chronological within a same-second pair.
    assert sorted([base.name, collided.name]) == [base.name, collided.name]
    # Same-second runs all survive: the second writer lost the exclusive
    # create and retried with the next suffix — nothing was overwritten.
    assert json.loads(base.read_text(encoding="utf-8")) == first
    assert json.loads(collided.read_text(encoding="utf-8")) == second
    assert json.loads(real[0].read_text(encoding="utf-8")) == third
    assert (tmp_path / REPORT_RELATIVE).is_file()
    assert (tmp_path / REAL_REPORT_RELATIVE).is_file()


# ---------------------------------------------------------------------------
# Degradation — a history failure is exactly one warning, never an error
# ---------------------------------------------------------------------------


def _fail_history(monkeypatch) -> None:
    def boom(directory, stamp):  # noqa: ANN001, ARG001
        raise OSError("disk full")

    monkeypatch.setattr(reporter, "_history_snapshot_path", boom)


def test_hermetic_history_failure_degrades_to_one_warning(tmp_path, monkeypatch):
    _fail_history(monkeypatch)
    report = _hermetic_report()

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        report_path = write_report(tmp_path, report)  # must not raise

    assert report_path == tmp_path / REPORT_RELATIVE
    assert json.loads(report_path.read_text(encoding="utf-8")) == report
    assert not _snapshots(tmp_path, "hermetic")
    assert len(caught) == 1, [str(warning.message) for warning in caught]
    assert caught[0].category is reporter.EvalHistoryWarning
    assert "eval history snapshot not written" in str(caught[0].message)
    assert "disk full" in str(caught[0].message)


def test_real_history_failure_degrades_to_one_warning(tmp_path, monkeypatch):
    _fail_history(monkeypatch)
    report = _real_report()

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        report_path = write_real_report(tmp_path, report)  # must not raise

    assert report_path == tmp_path / REAL_REPORT_RELATIVE
    assert json.loads(report_path.read_text(encoding="utf-8")) == report
    assert not _snapshots(tmp_path, "real")
    assert len(caught) == 1
    assert caught[0].category is reporter.EvalHistoryWarning


def test_repeated_history_failure_warns_once_per_location(tmp_path, monkeypatch):
    """The warning is one-time: an identical repeated failure does not re-emit."""
    _fail_history(monkeypatch)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("default")  # the production filter: dedup per location
        for _ in range(2):
            write_report(tmp_path, _hermetic_report())

    messages = [
        str(warning.message)
        for warning in caught
        if warning.category is reporter.EvalHistoryWarning
    ]
    assert messages == [
        "eval history snapshot not written (disk full); the run report itself is unaffected"
    ]


def test_history_failure_survives_warnings_error_filter(tmp_path, monkeypatch):
    """Even under an error-action warning filter (-W error), where the warn
    call itself would raise, the snapshot failure must not fail the run."""
    _fail_history(monkeypatch)
    report = _hermetic_report()

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("error")
        report_path = write_report(tmp_path, report)  # must not raise

    assert report_path == tmp_path / REPORT_RELATIVE
    assert json.loads(report_path.read_text(encoding="utf-8")) == report
    assert not _snapshots(tmp_path, "hermetic")
    assert [w for w in caught if w.category is reporter.EvalHistoryWarning] == []


def test_append_rejects_unknown_track(tmp_path):
    try:
        append_report_history(tmp_path, {}, track="bogus")
    except ValueError as exc:
        assert "bogus" in str(exc)
    else:
        raise AssertionError("unknown track must fail loudly (caller bug, not I/O)")


def test_append_real_wrapper_uses_the_real_track(tmp_path, monkeypatch):
    monkeypatch.setattr(reporter, "_history_stamp", lambda: "2026-06-01T00-00-00Z")
    report = _real_report()

    path = append_real_report_history(tmp_path, report)

    assert path == tmp_path / EVAL_HISTORY_RELATIVE / "real" / "2026-06-01T00-00-00Z.json"
    assert json.loads(path.read_text(encoding="utf-8")) == report


# ---------------------------------------------------------------------------
# CLI — `eval history [N]` listing, filtering, friendly empty state
# ---------------------------------------------------------------------------


def test_history_cli_lists_both_tracks_newest_first(tmp_path, monkeypatch):
    runs = [
        ("2026-01-01T00-00-01Z", "hermetic", _hermetic_doc(2, 3, cost_increased=1)),
        ("2026-01-01T00-00-02Z", "hermetic", _hermetic_doc(3, 3, cost_increased=0)),
        ("2026-01-01T00-00-03Z", "hermetic", _hermetic_doc(1, 3, cost_increased=2)),
        ("2026-01-01T00-00-04Z", "real", _real_doc(4, total_runs=6, all_pass_rate=0.5)),
    ]
    for stamp, track, doc in runs:
        _append_stamp(tmp_path, doc, track, stamp, monkeypatch)

    result = runner.invoke(cli.app, ["eval", "history", "--cwd", str(tmp_path)])

    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    hermetic_head = lines.index("hermetic (last 3):")
    real_head = lines.index("real (last 1):")
    newest = lines.index("2026-01-01T00-00-03Z\tpassed=1/3\texit=1\tcost_increased=2")
    middle = lines.index("2026-01-01T00-00-02Z\tpassed=3/3\texit=0\tcost_increased=0")
    oldest = lines.index("2026-01-01T00-00-01Z\tpassed=2/3\texit=1\tcost_increased=1")
    real_line = lines.index("2026-01-01T00-00-04Z\tpassed=4/6\texit=1\tall_pass_rate=0.5")
    assert hermetic_head < newest < middle < oldest, "newest first within the track"
    assert hermetic_head < oldest < real_head < real_line, "tracks stay in their blocks"


def test_history_cli_limit_and_track_filter(tmp_path, monkeypatch):
    runs = [
        ("2026-01-01T00-00-01Z", "hermetic", _hermetic_doc(1, 1, cost_increased=0)),
        ("2026-01-01T00-00-02Z", "hermetic", _hermetic_doc(1, 1, cost_increased=0)),
        ("2026-01-01T00-00-03Z", "hermetic", _hermetic_doc(0, 1, cost_increased=1)),
        ("2026-01-01T00-00-04Z", "real", _real_doc(2, total_runs=3, all_pass_rate=0.6667)),
    ]
    for stamp, track, doc in runs:
        _append_stamp(tmp_path, doc, track, stamp, monkeypatch)

    limited = runner.invoke(cli.app, ["eval", "history", "2", "--cwd", str(tmp_path)])
    assert limited.exit_code == 0, limited.output
    assert "hermetic (last 2):" in limited.output
    assert "2026-01-01T00-00-01Z" not in limited.output, "oldest beyond the limit is dropped"
    assert "2026-01-01T00-00-02Z" in limited.output
    assert "2026-01-01T00-00-03Z" in limited.output
    assert "real (last 1):" in limited.output, "the limit is per track"
    assert "2026-01-01T00-00-04Z" in limited.output

    only_hermetic = runner.invoke(
        cli.app, ["eval", "history", "--track", "hermetic", "--cwd", str(tmp_path)]
    )
    assert only_hermetic.exit_code == 0, only_hermetic.output
    assert "2026-01-01T00-00-04Z" not in only_hermetic.output
    assert "all_pass_rate" not in only_hermetic.output
    assert "cost_increased=1" in only_hermetic.output

    only_real = runner.invoke(
        cli.app, ["eval", "history", "--track", "real", "--cwd", str(tmp_path)]
    )
    assert only_real.exit_code == 0, only_real.output
    assert "2026-01-01T00-00-04Z" in only_real.output
    assert "all_pass_rate=0.6667" in only_real.output
    assert "cost_increased" not in only_real.output

    bad_track = runner.invoke(
        cli.app, ["eval", "history", "--track", "nope", "--cwd", str(tmp_path)]
    )
    assert bad_track.exit_code != 0
    bad_limit = runner.invoke(cli.app, ["eval", "history", "0", "--cwd", str(tmp_path)])
    assert bad_limit.exit_code != 0


def test_history_cli_friendly_when_no_history(tmp_path):
    result = runner.invoke(cli.app, ["eval", "history", "--cwd", str(tmp_path)])

    assert result.exit_code == 0
    assert "no eval history yet" in result.output
    assert str(tmp_path / EVAL_HISTORY_RELATIVE / "hermetic") in result.output
    assert str(tmp_path / EVAL_HISTORY_RELATIVE / "real") in result.output


def test_history_cli_friendly_when_track_dir_empty(tmp_path):
    (tmp_path / EVAL_HISTORY_RELATIVE / "hermetic").mkdir(parents=True)

    result = runner.invoke(
        cli.app, ["eval", "history", "--track", "hermetic", "--cwd", str(tmp_path)]
    )

    assert result.exit_code == 0
    assert "no eval history yet" in result.output


def test_history_cli_tolerates_unreadable_and_summary_less_snapshots(tmp_path, monkeypatch):
    _append_stamp(
        tmp_path,
        _hermetic_doc(1, 1, cost_increased=0),
        "hermetic",
        "2026-02-02T00-00-00Z",
        monkeypatch,
    )
    history_dir = tmp_path / EVAL_HISTORY_RELATIVE / "hermetic"
    (history_dir / "2026-02-01T00-00-00Z.json").write_text("{broken", encoding="utf-8")
    (history_dir / "2026-02-01T00-00-01Z.json").write_text("{}", encoding="utf-8")

    result = runner.invoke(
        cli.app, ["eval", "history", "--track", "hermetic", "--cwd", str(tmp_path)]
    )

    assert result.exit_code == 0, result.output
    assert "2026-02-01T00-00-00Z\t<unreadable snapshot>" in result.output
    assert "2026-02-01T00-00-01Z\t<snapshot without summary>" in result.output
    assert "passed=1/1" in result.output, "the good snapshot still lists"
