"""evolve.py — skill self-evolution store.

Versioned snapshots of user-scope skills plus an append-only audit log, so
feedback ("this worked / this failed") can be sedimented into SKILL.md rules
without losing the ability to inspect or restore what came before.

Layout under the store root (default ``~/.nexuscli/skill_history``)::

    <root>/<name>/v<N>.md    frozen copy of the skill's SKILL.md per snapshot
    <root>/audit.jsonl       one JSON line {ts, skill, op, version, reason}

Honest boundaries:
- Operates on user-scope skills only (``~/.nexuscli/skills/<name>/SKILL.md``);
  project and builtin skills are out of scope by design.
- ``audit.jsonl`` records rule appends (op="save") and rollbacks (op="rollback");
  plain snapshots are not audited individually — they are visible via
  ``version_info``. Each public operation writes exactly one audit line.
- Rule text is appended verbatim as a markdown bullet into a dedicated
  ``## Evolved Rules`` section; nothing else in the file is parsed or rewritten.
"""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nexuscli.skill.registry import _validate_skill_slug

_MAX_VERSIONS = 10
_RULES_HEADING = "## Evolved Rules"


def _utc_timestamp() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class SkillEvolutionStore:
    """Snapshot / rule-append / rollback storage for user skills."""

    def __init__(
        self,
        root: str | Path | None = None,
        user_skills_root: str | Path | None = None,
    ):
        home = Path.home()
        self.root = Path(root or home / ".nexuscli" / "skill_history").expanduser()
        self.user_skills_root = Path(
            user_skills_root or home / ".nexuscli" / "skills"
        ).expanduser()

    # -- paths ------------------------------------------------------------

    def skill_path(self, name: str) -> Path:
        """Validated path of the user-scope SKILL.md for ``name``."""
        return self.user_skills_root / _validate_skill_slug(name) / "SKILL.md"

    def versions_dir(self, name: str) -> Path:
        """Validated snapshot directory for ``name``."""
        return self.root / _validate_skill_slug(name)

    # -- operations ---------------------------------------------------------

    def snapshot(self, name: str) -> int:
        """Freeze the current SKILL.md as ``v<N>.md`` and return the new version.

        Keeps at most the newest ``_MAX_VERSIONS`` files per skill; older ones
        are evicted. Raises FileNotFoundError when the skill file is missing.
        """
        source = self.skill_path(name)
        if not source.is_file():
            raise FileNotFoundError(f'skill "{name}" not found in user skills: {source}')
        version = self.version_info(name)["version"] + 1
        target = self.versions_dir(name) / f"v{version}.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        self._prune(name)
        return version

    def save_versioned(
        self,
        name: str,
        rule_text: str,
        reason: str = "",
    ) -> int:
        """Snapshot the skill, then append a timestamped rule entry.

        Writes one audit line (op="save") and returns the snapshot version —
        the state just before the rule was appended, so the append is always
        undoable via ``rollback(name, version)``.
        """
        rule_text = (rule_text or "").strip()
        if not rule_text:
            raise ValueError("rule text must not be empty")
        version = self.snapshot(name)
        path = self.skill_path(name)
        entry = f"- [{_utc_timestamp()}] {rule_text}"
        if reason:
            entry += f" (reason: {reason})"
        content = path.read_text(encoding="utf-8")
        path.write_text(_append_rule(content, entry), encoding="utf-8")
        self._audit(name, "save", version, reason)
        return version

    def rollback(self, name: str, version: int) -> int | None:
        """Restore ``v<version>`` into the skill file.

        Snapshots the current state first (so the rollback itself is undoable)
        and returns that safety snapshot's version, or None when ``version``
        does not exist. Writes one audit line (op="rollback", version=restored).
        """
        source = self.versions_dir(name) / f"v{int(version)}.md"
        if not source.is_file():
            return None
        safety_version = self.snapshot(name)
        shutil.copyfile(source, self.skill_path(name))
        self._audit(name, "rollback", int(version), "rollback")
        return safety_version

    def version_info(self, name: str) -> dict[str, Any]:
        """Current version number (0 when never snapshotted) plus history list."""
        directory = self.versions_dir(name)
        history: list[dict[str, Any]] = []
        if directory.is_dir():
            for path in directory.glob("v*.md"):
                number = _parse_version(path)
                if number is not None:
                    history.append({"version": number, "path": str(path)})
        history.sort(key=lambda item: item["version"])
        return {
            "skill": name,
            "version": history[-1]["version"] if history else 0,
            "history": history,
        }

    # -- internals ----------------------------------------------------------

    def _prune(self, name: str) -> None:
        """Drop the oldest snapshots beyond the per-skill version cap."""
        history = self.version_info(name)["history"]
        for item in history[: max(len(history) - _MAX_VERSIONS, 0)]:
            Path(item["path"]).unlink(missing_ok=True)

    def _audit(self, name: str, op: str, version: int, reason: str) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": _utc_timestamp(),
            "skill": name,
            "op": op,
            "version": version,
            "reason": reason,
        }
        with (self.root / "audit.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _parse_version(path: Path) -> int | None:
    stem = path.stem
    if not stem.startswith("v"):
        return None
    try:
        return int(stem[1:])
    except ValueError:
        return None


def _append_rule(content: str, entry: str) -> str:
    """Append ``entry`` into the ``## Evolved Rules`` section (create at EOF)."""
    lines = content.rstrip("\n").splitlines()
    heading_index = next(
        (index for index, line in enumerate(lines) if line.strip() == _RULES_HEADING),
        None,
    )
    if heading_index is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines.extend([_RULES_HEADING, "", entry, ""])
        return "\n".join(lines)
    # Inside an existing section: insert before the next "## " heading (or at
    # EOF), keeping entries chronological.
    section_end = len(lines)
    for index in range(heading_index + 1, len(lines)):
        if lines[index].startswith("## "):
            section_end = index
            break
    insert_at = section_end
    while insert_at > heading_index + 1 and not lines[insert_at - 1].strip():
        insert_at -= 1
    lines.insert(insert_at, entry)
    return "\n".join(lines) + "\n"
