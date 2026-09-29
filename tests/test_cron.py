from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

import nexuscli.entrypoints.cli as cli
from nexuscli.runtime.cron import (
    CronError,
    CronTask,
    cron_tasks_path,
    list_tasks,
    load_tasks,
    next_run,
    parse_cron,
    run_task,
    save_tasks,
)

runner = CliRunner()


def test_parse_cron_accepts_standard_fields():
    spec = parse_cron("*/20 * * * *")
    assert spec.minutes == frozenset({0, 20, 40})
    assert spec.hours == frozenset(range(24))
    assert spec.days_of_month == frozenset(range(1, 32))
    assert spec.months == frozenset(range(1, 13))
    assert spec.days_of_week == frozenset(range(7))

    spec = parse_cron("0 * * * *")
    assert spec.minutes == frozenset({0})
    assert spec.hours == frozenset(range(24))

    spec = parse_cron("0 9 * * 1-5")
    assert spec.days_of_week == frozenset({1, 2, 3, 4, 5})

    spec = parse_cron("30 2 1,15 * *")
    assert spec.minutes == frozenset({30})
    assert spec.hours == frozenset({2})
    assert spec.days_of_month == frozenset({1, 15})
    assert spec.months == frozenset(range(1, 13))
    assert spec.days_of_week == frozenset(range(7))

    # 0 and 7 both denote Sunday and normalize to the same set.
    assert parse_cron("0 0 * * 0").days_of_week == frozenset({0})
    assert parse_cron("0 0 * * 7").days_of_week == frozenset({0})


@pytest.mark.parametrize(
    "expr",
    [
        "61 * * * *",
        "* 25 * * *",
        "* * 32 * *",
        "* * * 13 *",
        "* * * * 8",
        "* * * *",  # four fields
        "a * * * *",  # non-numeric
    ],
)
def test_parse_cron_rejects_invalid(expr: str):
    with pytest.raises(CronError):
        parse_cron(expr)


def test_next_run_computes_with_fixed_now():
    now = datetime(2026, 9, 29, 10, 0)  # Tuesday
    # `*/20` includes minute 0, but a matching `now` itself is never returned
    # (strictly-after semantics).
    assert next_run(parse_cron("*/20 * * * *"), now) == datetime(2026, 9, 29, 10, 20)
    assert next_run(parse_cron("0 9 * * *"), now) == datetime(2026, 9, 30, 9, 0)

    friday = datetime(2026, 10, 2, 10, 0)
    assert next_run(parse_cron("0 9 * * 1-5"), friday) == datetime(2026, 10, 5, 9, 0)

    # Day-of-month 1 crosses into the next month.
    assert next_run(parse_cron("30 2 1 * *"), now) == datetime(2026, 10, 1, 2, 30)

    # Day-of-month and day-of-week both restricted: vixie cron OR semantics —
    # the 1st of the month (Thursday) fires before the next Monday.
    assert next_run(parse_cron("0 9 1 * 1"), now) == datetime(2026, 10, 1, 9, 0)


def test_load_save_tasks_roundtrip(tmp_path: Path):
    path = tmp_path / "cron.json"
    tasks = [CronTask("t1", "*/20 * * * *", "跑测试")]
    save_tasks(path, tasks)
    assert load_tasks(path) == tasks

    # Tolerant read: corrupt JSON / non-dict top level / non-list tasks -> [].
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert load_tasks(broken) == []
    top_list = tmp_path / "top-list.json"
    top_list.write_text("[1, 2]", encoding="utf-8")
    assert load_tasks(top_list) == []
    bad_tasks = tmp_path / "bad-tasks.json"
    bad_tasks.write_text(json.dumps({"tasks": "bad"}), encoding="utf-8")
    assert load_tasks(bad_tasks) == []

    # Entries missing id/cron/prompt (or not objects) are skipped; valid
    # entries survive.
    mixed = tmp_path / "mixed.json"
    mixed.write_text(
        json.dumps(
            {
                "tasks": [
                    {"id": "ok", "cron": "0 9 * * *", "prompt": "fine"},
                    {"cron": "0 9 * * *", "prompt": "no id"},
                    {"id": "no-cron", "prompt": "no cron"},
                    {"id": "no-prompt", "cron": "0 9 * * *"},
                    "not-a-dict",
                ]
            }
        ),
        encoding="utf-8",
    )
    assert [task.id for task in load_tasks(mixed)] == ["ok"]


def test_list_and_run_operations(tmp_path: Path):
    path = tmp_path / "cron.json"
    tasks = [
        CronTask("t1", "*/20 * * * *", "run the tests"),
        CronTask("t2", "0 9 * * *", "morning brief", enabled=False),
    ]
    save_tasks(path, tasks)

    assert list_tasks(path) == tasks
    assert run_task(path, "t1") == "run the tests"
    with pytest.raises(CronError):
        run_task(path, "unknown")
    with pytest.raises(CronError):
        run_task(path, "t2")  # disabled tasks refuse to run


def test_cli_cron_commands(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _isolate_home(monkeypatch, tmp_path)
    save_tasks(
        cron_tasks_path(),
        [
            CronTask("t1", "*/20 * * * *", "say hello"),
            CronTask("bad", "99 99 99 99 99", "never parses"),
        ],
    )

    result = runner.invoke(cli.app, ["cron", "list"])
    assert result.exit_code == 0
    assert "t1" in result.output
    assert "*/20 * * * *" in result.output

    monkeypatch.setattr("nexuscli.runtime.cron._now", lambda: datetime(2026, 9, 29, 10, 0))
    result = runner.invoke(cli.app, ["cron", "next"])
    assert result.exit_code == 0
    assert "2026-09-29 10:20" in result.output
    # Invalid expressions are skipped per entry with a warning line, not fatal.
    assert "[warn] invalid cron expression: 99 99 99 99 99" in result.output

    result = runner.invoke(cli.app, ["cron", "run", "t1"])
    assert result.exit_code == 0
    assert result.output == "say hello\n"

    result = runner.invoke(cli.app, ["cron", "run", "unknown"])
    assert result.exit_code != 0


def test_cli_cron_list_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _isolate_home(monkeypatch, tmp_path)

    result = runner.invoke(cli.app, ["cron", "list"])

    assert result.exit_code == 0
    assert "No cron tasks." in result.output


def _isolate_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
