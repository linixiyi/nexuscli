"""Cron task table for nexuscli — an offline scheduled-prompt registry.

Tasks live in ``~/.nexuscli/cron.json`` as ``{"tasks": [{"id", "cron",
"prompt", "enabled"}, ...]}``, where ``cron`` is a standard 5-field cron
expression (minute hour day-of-month month day-of-week, ported from ZCode's
automation contracts) evaluated in the user's local timezone. All datetimes
here are naive local time; ``next`` renders trigger times as
``%Y-%m-%d %H:%M``.

Offline form, deliberately: no resident daemon, no scheduling loop, and no
LLM/network call anywhere in this module. Firing is driven by the user or an
external scheduler (Windows Task Scheduler / system cron) that runs
``nexuscli cron run <id>`` — which prints the task's prompt verbatim — and
pipes that prompt into ``nexuscli -p`` for the actual execution.

Ported from ZCode (contracts/tools/automation.ts, interfaces/automation.port.ts)
in read-only spirit: only the 5-field cron syntax and the ``list``/``next``/
``run`` read-side surface. Explicitly NOT ported: resident daemon and
scheduling loop, ``delayMinutes`` relative scheduling and
``intervalUnit+interval`` scheduling, session-boundary constraints, the
create/update/delete write operations, and desktop notifications.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path


class CronError(ValueError):
    """Raised for malformed expressions, storage shapes, or lookups (CLI exits 1)."""


@dataclass(slots=True)
class CronTask:
    id: str
    cron: str
    prompt: str
    enabled: bool = True


@dataclass(slots=True)
class CronSpec:
    minutes: frozenset[int]
    hours: frozenset[int]
    days_of_month: frozenset[int]
    months: frozenset[int]
    days_of_week: frozenset[int]


# Field order and value domains of a 5-field expression (vixie cron layout).
_FIELDS: tuple[tuple[str, range], ...] = (
    ("minute", range(0, 60)),
    ("hour", range(0, 24)),
    ("day-of-month", range(1, 32)),
    ("month", range(1, 13)),
    ("day-of-week", range(0, 8)),  # 7 accepted, normalized to 0 (Sunday)
)

# Full domains: a parsed set equal to these means the field was a literal
# ``*`` (unrestricted), which next_run's day-matching rule needs to tell
# apart from a restricted field.
_ALL_DAYS_OF_MONTH = frozenset(range(1, 32))
_ALL_DAYS_OF_WEEK = frozenset(range(7))

_MAX_MINUTE_STEPS = 366 * 24 * 60  # one leap year of minutes; guards unsatisfiable specs


def cron_tasks_path(home: Path | None = None) -> Path:
    """Return the cron task table path under ``home`` (default ``Path.home()``).

    The home directory is resolved per call instead of at import time so tests
    can redirect ``Path.home()`` to a temporary directory (same seam as
    bg_tasks.py).
    """
    base = home if home is not None else Path.home()
    return base / ".nexuscli" / "cron.json"


def load_tasks(path: Path) -> list[CronTask]:
    """Read the task table; unreadable or malformed files yield ``[]``.

    Tolerant read (same spirit as ``config._read_json``): a missing file,
    corrupt JSON, a non-dict top level, or a non-list ``tasks`` key all
    produce an empty table instead of an error. Entries that are not objects
    or that miss ``id`` / ``cron`` / ``prompt`` are skipped, so one bad entry
    cannot hide the rest of the schedule.
    """
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(raw, dict):
        return []
    entries = raw.get("tasks")
    if not isinstance(entries, list):
        return []
    tasks: list[CronTask] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        task_id = entry.get("id")
        cron = entry.get("cron")
        prompt = entry.get("prompt")
        if not isinstance(task_id, str):
            continue
        if not isinstance(cron, str):
            continue
        if not isinstance(prompt, str):
            continue
        enabled = bool(entry.get("enabled", True))
        tasks.append(CronTask(id=task_id, cron=cron, prompt=prompt, enabled=enabled))
    return tasks


def save_tasks(path: Path, tasks: list[CronTask]) -> None:
    """Write the task table as ``{"tasks": [...]}``, creating parent directories."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "tasks": [
            {"id": task.id, "cron": task.cron, "prompt": task.prompt, "enabled": task.enabled}
            for task in tasks
        ]
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_cron(expr: str) -> CronSpec:
    """Parse a standard 5-field cron expression into per-field value sets.

    Syntax per field: ``*``, a number, comma lists (``a,b``), ranges
    (``a-b``), and steps (``*/n`` or ``a-b/n``). Day-of-week follows vixie
    cron: 0 = Sunday .. 6 = Saturday, with 7 accepted and normalized to 0.
    Raises :class:`CronError` on a wrong field count, unknown tokens, or
    values outside a field's domain; the message names the field and the
    offending value.
    """
    fields = expr.split()
    if len(fields) != len(_FIELDS):
        raise CronError(
            f"cron expression must have {len(_FIELDS)} fields, got {len(fields)}: {expr!r}"
        )
    parsed = [
        _parse_field(field, name, domain)
        for field, (name, domain) in zip(fields, _FIELDS, strict=True)
    ]
    return CronSpec(
        minutes=parsed[0],
        hours=parsed[1],
        days_of_month=parsed[2],
        months=parsed[3],
        days_of_week=parsed[4],
    )


def next_run(spec: CronSpec, now: datetime) -> datetime:
    """First local time strictly after ``now`` matching ``spec`` (minute granularity).

    Steps forward minute by minute starting at ``now`` truncated to the minute
    plus one minute, so a ``now`` that itself matches is never returned. When
    both day-of-month and day-of-week are restricted (i.e. not a literal
    ``*``), vixie cron's OR semantics apply — a day matches if EITHER field
    matches; otherwise both day fields must match.
    """
    candidate = now.replace(second=0, microsecond=0) + timedelta(minutes=1)
    for _ in range(_MAX_MINUTE_STEPS):
        if _matches(spec, candidate):
            return candidate
        candidate += timedelta(minutes=1)
    raise CronError("no matching time within a year")


def list_tasks(path: Path) -> list[CronTask]:
    """Return every stored cron task (tolerant read; see :func:`load_tasks`)."""
    return load_tasks(path)


def next_trigger(
    path: Path, *, now: datetime | None = None
) -> list[tuple[CronTask, datetime | None]]:
    """Next trigger time per task; ``None`` marks a task that cannot fire.

    A task whose ``cron`` fails to parse (or that matches no time within a
    year) stays in the result with ``None`` instead of aborting the whole
    listing — the CLI renders such entries as a ``[warn]`` line.
    """
    moment = now if now is not None else _now()
    results: list[tuple[CronTask, datetime | None]] = []
    for task in list_tasks(path):
        try:
            upcoming = next_run(parse_cron(task.cron), moment)
        except CronError:
            upcoming = None
        results.append((task, upcoming))
    return results


def run_task(path: Path, task_id: str) -> str:
    """Return the task's prompt verbatim — offline form, nothing is executed.

    No LLM call happens here by design: an external scheduler pipes the
    returned prompt into ``nexuscli -p`` to actually run it.
    """
    for task in list_tasks(path):
        if task.id == task_id:
            if not task.enabled:
                raise CronError(f"cron task disabled: {task_id}")
            return task.prompt
    raise CronError(f"cron task not found: {task_id}")


def _now() -> datetime:
    """Wall-clock seam for the CLI layer.

    cli.py reads the current time only through this helper, so tests pin the
    schedule with ``monkeypatch.setattr("nexuscli.runtime.cron._now", ...)``.
    """
    return datetime.now()


def _parse_field(field: str, name: str, domain: range) -> frozenset[int]:
    values: set[int] = set()
    for part in field.split(","):
        body, step = _split_step(part, name, field)
        start, end = _bounds(body, name, field, domain)
        values.update(range(start, end + 1, step))
    if name == "day-of-week":
        values = {0 if value == 7 else value for value in values}
    return frozenset(values)


def _split_step(part: str, name: str, raw_field: str) -> tuple[str, int]:
    if "/" not in part:
        return part, 1
    body, _, step_text = part.partition("/")
    step = _parse_int(step_text, name, raw_field)
    if step < 1:
        raise CronError(f"cron {name} field: step must be >= 1, got {step} in {raw_field!r}")
    return body, step


def _bounds(body: str, name: str, raw_field: str, domain: range) -> tuple[int, int]:
    if body == "*":
        return domain[0], domain[-1]
    if "-" in body:
        start_text, _, end_text = body.partition("-")
        start = _parse_int(start_text, name, raw_field)
        end = _parse_int(end_text, name, raw_field)
    else:
        start = end = _parse_int(body, name, raw_field)
    if start not in domain or end not in domain or start > end:
        raise CronError(
            f"cron {name} field: value out of range {domain[0]}-{domain[-1]}: got {body!r}"
        )
    return start, end


def _parse_int(token: str, name: str, raw_field: str) -> int:
    if not token.isdigit():
        raise CronError(f"cron {name} field: expected a number, got {token!r} in {raw_field!r}")
    return int(token)


def _cron_weekday(moment: datetime) -> int:
    """Map Python Monday=0..Sunday=6 to cron Sunday=0..Saturday=6."""
    return (moment.weekday() + 1) % 7


def _matches(spec: CronSpec, moment: datetime) -> bool:
    if moment.minute not in spec.minutes or moment.hour not in spec.hours:
        return False
    if moment.month not in spec.months:
        return False
    dom_hit = moment.day in spec.days_of_month
    dow_hit = _cron_weekday(moment) in spec.days_of_week
    dom_restricted = spec.days_of_month != _ALL_DAYS_OF_MONTH
    dow_restricted = spec.days_of_week != _ALL_DAYS_OF_WEEK
    if dom_restricted and dow_restricted:
        # vixie cron: with both day fields restricted, a day matches if
        # EITHER field matches; otherwise both must match.
        return dom_hit or dow_hit
    return dom_hit and dow_hit
