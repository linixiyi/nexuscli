"""回合级 checkpoint（F10）：被中断的 react 回合从最近完整轮接续。

每轮两个快照点（同一文件覆盖，05-execution-durability.md F10 定稿）：
- (a) in-flight：assistant tool_use 已入 messages、工具分发未开始——pending_tool_calls
  为本轮带原始 id 的在途调用；
- (b) 轮末：工具结果已全部入 messages——pending_tool_calls 为空，表示该轮完整。

只覆盖不追加：一个 run 一个 checkpoint 文件（id = turn_<run_id>）。明文落盘沿用
plan_runs 现状口径（可能含工具结果中的文件内容，不做加密）。三套持久化真相分工：
会话 JSONL（对话与 usage 台账）、计划 checkpoint（DAG 推进）、回合 checkpoint（轮级
执行状态）；转录只在整回合完成后追加，checkpoint 恒领先或等于 JSONL。
"""

from __future__ import annotations

import json
import os
import secrets
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

CHECKPOINT_SCHEMA_VERSION = 1


class TurnCheckpointStoreError(RuntimeError):
    pass


def turn_checkpoints_dir() -> Path:
    return Path.home() / ".nexuscli" / "turn_checkpoints"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _checkpoint_id_for_run(run_id: str) -> str:
    return f"turn_{run_id}"


@dataclass(frozen=True, slots=True)
class TurnCheckpoint:
    checkpoint_id: str
    run_id: str
    cwd: str
    model: str
    provider: str
    turn: int
    messages: list[dict[str, Any]]
    pending_tool_calls: list[dict[str, Any]]
    usage_so_far: dict[str, Any]
    cost_so_far: dict[str, Any]
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": CHECKPOINT_SCHEMA_VERSION,
            "checkpoint_id": self.checkpoint_id,
            "run_id": self.run_id,
            "cwd": self.cwd,
            "model": self.model,
            "provider": self.provider,
            "turn": self.turn,
            "messages": self.messages,
            "pending_tool_calls": self.pending_tool_calls,
            "usage_so_far": self.usage_so_far,
            "cost_so_far": self.cost_so_far,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def _write_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
        os.replace(temp_name, path)
    except BaseException:
        with suppress_unlink():
            Path(temp_name).unlink(missing_ok=True)
        raise


class suppress_unlink:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *exc_info: object) -> None:
        return None


class TurnCheckpointStore:
    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory or turn_checkpoints_dir()

    def save(
        self,
        *,
        run_id: str,
        cwd: str,
        model: str,
        provider: str,
        turn: int,
        messages: list[dict[str, Any]],
        pending_tool_calls: list[dict[str, Any]],
        usage_so_far: dict[str, Any],
        cost_so_far: dict[str, Any],
    ) -> str:
        if not run_id:
            raise TurnCheckpointStoreError("run_id is required for a turn checkpoint")
        checkpoint_id = _checkpoint_id_for_run(run_id)
        existing = self.load(checkpoint_id)
        created_at = existing.created_at if existing is not None else _now()
        checkpoint = TurnCheckpoint(
            checkpoint_id=checkpoint_id,
            run_id=run_id,
            cwd=cwd,
            model=model,
            provider=provider,
            turn=turn,
            messages=messages,
            pending_tool_calls=pending_tool_calls,
            usage_so_far=usage_so_far,
            cost_so_far=cost_so_far,
            created_at=created_at,
            updated_at=_now(),
        )
        _write_atomic(self.directory / f"{checkpoint_id}.json", checkpoint.to_dict())
        return checkpoint_id

    def load(self, checkpoint_id: str) -> TurnCheckpoint | None:
        """损坏/缺失/非法 id 一律降级为 None（与 plan checkpoint 同取向）。"""

        if "/" in checkpoint_id or ".." in checkpoint_id or not checkpoint_id:
            return None
        path = self.directory / f"{checkpoint_id}.json"
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        if not isinstance(payload, dict):
            return None
        if int(payload.get("schema_version") or 0) != CHECKPOINT_SCHEMA_VERSION:
            return None
        try:
            return TurnCheckpoint(
                checkpoint_id=str(payload["checkpoint_id"]),
                run_id=str(payload["run_id"]),
                cwd=str(payload["cwd"]),
                model=str(payload.get("model") or ""),
                provider=str(payload.get("provider") or ""),
                turn=int(payload.get("turn") or 0),
                messages=list(payload.get("messages") or []),
                pending_tool_calls=list(payload.get("pending_tool_calls") or []),
                usage_so_far=dict(payload.get("usage_so_far") or {}),
                cost_so_far=dict(payload.get("cost_so_far") or {}),
                created_at=str(payload.get("created_at") or ""),
                updated_at=str(payload.get("updated_at") or ""),
            )
        except (KeyError, TypeError, ValueError):
            return None

    def list_checkpoints(self, cwd: str | None = None) -> list[TurnCheckpoint]:
        """按 updated_at 倒序列出；cwd 归一化过滤（与 DurableTaskManager 同口径）。"""

        import os as _os

        items: list[TurnCheckpoint] = []
        if not self.directory.is_dir():
            return items
        for path in sorted(self.directory.glob("turn_*.json")):
            checkpoint = self.load(path.stem)
            if checkpoint is None:
                continue
            if cwd is not None and _os.path.realpath(checkpoint.cwd) != _os.path.realpath(cwd):
                continue
            items.append(checkpoint)
        items.sort(key=lambda item: item.updated_at, reverse=True)
        return items

    def delete(self, checkpoint_id: str) -> bool:
        if "/" in checkpoint_id or ".." in checkpoint_id or not checkpoint_id:
            return False
        path = self.directory / f"{checkpoint_id}.json"
        if not path.is_file():
            return False
        path.unlink()
        return True


def new_run_nonce() -> str:
    return secrets.token_hex(8)
