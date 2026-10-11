"""plan/checkpoint.py — Durable graph-state checkpoints for plan runs.

One checkpoint JSON file per plan id, under ``~/.nexuscli/plan_runs/<id>.json``
(the same ``~/.nexuscli`` root convention as ``session/store.py``). The payload
is the serialized :class:`~nexuscli.plan.models.ExecutionPlan`: every task's
status and result ride along, so a resumed run skips planning, restores the
finished tasks verbatim, and ``executable_tasks()`` naturally continues with
the remaining PENDING tasks. Writes go through a temp file + rename so a crash
never leaves a truncated checkpoint behind.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

from nexuscli.plan.models import ExecutionPlan

PLAN_RUNS_DIRNAME = "plan_runs"

# Checkpoint files are addressed as ``root / f"{plan_id}.json"`` from raw user
# input (``/plan --resume <id>``), so the id is validated against a filename-
# safe shape: no separators, no ``..`` chains, nothing that could climb out of
# ``plan_runs`` and read an arbitrary ``.json`` as a plan.
_PLAN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def plan_runs_root(home: Path | None = None) -> Path:
    base = home if home is not None else Path.home()
    return base / ".nexuscli" / PLAN_RUNS_DIRNAME


class PlanCheckpointStore:
    """Read and write plan-run checkpoints under a single root directory."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root if root is not None else plan_runs_root()

    def path_for(self, plan_id: str) -> Path:
        """Checkpoint path for *plan_id*; rejects ids that are not filename-safe.

        Raises :class:`ValueError` for ids containing path separators or
        traversal chains — the same within-root discipline the vfs and path
        guard apply, on this store's read side.
        """
        if not _PLAN_ID_PATTERN.fullmatch(str(plan_id)):
            raise ValueError(f"invalid plan id: {plan_id!r}")
        return self.root / f"{plan_id}.json"

    def save(self, plan: ExecutionPlan) -> Path:
        """Persist the current plan state; returns the checkpoint path."""
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.path_for(plan.id)
        payload: dict[str, Any] = {
            "type": "plan_checkpoint",
            "plan_id": plan.id,
            "saved_at": time.time(),
            "plan": plan.to_dict(),
        }
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
        return path

    def load(self, plan_id: str) -> ExecutionPlan | None:
        """Load a checkpoint by exact plan id; None when missing or corrupt.

        A syntactically valid file with garbage fields must degrade to None,
        never raise — a corrupt checkpoint must not take down the resume path.
        """
        try:
            path = self.path_for(plan_id)
        except ValueError:
            # An unsafe id never touches the filesystem: report "not found"
            # instead of probing an arbitrary ``.json`` outside plan_runs.
            return None
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        if not isinstance(data, dict) or not isinstance(data.get("plan"), dict):
            return None
        try:
            return ExecutionPlan.from_dict(data["plan"])
        except (KeyError, TypeError, ValueError, AttributeError):
            return None
