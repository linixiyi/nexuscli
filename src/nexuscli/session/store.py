"""session/store.py — Durable conversation transcripts for the REPL.

Sessions live as JSONL files under ``~/.nexuscli/sessions/<id>.jsonl``. The
first line of each file is the session metadata; every following line is one
serialized :class:`~nexuscli.types.Message`. Transcripts are append-only per
turn and stay human-inspectable; resuming a session feeds the messages back
into ``Agent.history`` so the conversation continues with full context.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nexuscli.types import Message

SESSIONS_DIRNAME = "sessions"
_TITLE_MAX_CHARS = 80


@dataclass(slots=True)
class SessionMeta:
    id: str
    cwd: str
    created_at: float
    updated_at: float
    model: str = ""
    provider: str = ""
    title: str = ""
    message_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "cwd": self.cwd,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "model": self.model,
            "provider": self.provider,
            "title": self.title,
            "message_count": self.message_count,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SessionMeta:
        return cls(
            id=str(data.get("id") or ""),
            cwd=str(data.get("cwd") or ""),
            created_at=float(data.get("created_at") or 0.0),
            updated_at=float(data.get("updated_at") or 0.0),
            model=str(data.get("model") or ""),
            provider=str(data.get("provider") or ""),
            title=str(data.get("title") or ""),
            message_count=int(data.get("message_count") or 0),
        )


@dataclass
class SessionRecord:
    meta: SessionMeta
    messages: list[Message] = field(default_factory=list)


def sessions_root(home: Path | None = None) -> Path:
    base = home if home is not None else Path.home()
    return base / ".nexuscli" / SESSIONS_DIRNAME


def _message_to_dict(message: Message) -> dict[str, Any]:
    return {
        "role": message.role,
        "content": message.content,
        "name": message.name,
        "tool_call_id": message.tool_call_id,
        "tool_calls": message.tool_calls,
    }


def _message_from_dict(data: dict[str, Any]) -> Message:
    tool_calls = data.get("tool_calls")
    return Message(
        role=data.get("role", "user"),
        content=data.get("content", ""),
        name=data.get("name"),
        tool_call_id=data.get("tool_call_id"),
        tool_calls=list(tool_calls) if isinstance(tool_calls, list) else [],
    )


def _title_from(messages: list[Message]) -> str:
    for message in messages:
        if message.role == "user" and isinstance(message.content, str) and message.content.strip():
            return " ".join(message.content.split())[:_TITLE_MAX_CHARS]
    return ""


class SessionStore:
    """Read and write session transcripts under a single root directory."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root if root is not None else sessions_root()

    def new_writer(
        self,
        *,
        cwd: str,
        model: str = "",
        provider: str = "",
    ) -> SessionWriter:
        """Create an in-memory session that only touches disk on first append."""
        now = time.time()
        meta = SessionMeta(
            id=f"{time.strftime('%Y%m%d-%H%M%S', time.localtime(now))}-{uuid.uuid4().hex[:6]}",
            cwd=str(Path(cwd).resolve()),
            created_at=now,
            updated_at=now,
            model=model,
            provider=provider,
        )
        return SessionWriter(self, meta)

    def writer_for(self, meta: SessionMeta, messages: list[Message]) -> SessionWriter:
        """Continue an existing session, appending past ``messages``."""
        writer = SessionWriter(self, meta)
        writer.persisted = len(messages)
        return writer

    def list(self, limit: int = 20, cwd: str | None = None) -> list[SessionMeta]:
        """Most recent sessions first, optionally filtered by working directory."""
        if not self.root.exists():
            return []
        cwd_filter = str(Path(cwd).resolve()) if cwd is not None else None
        metas: list[SessionMeta] = []
        for path in self.root.glob("*.jsonl"):
            meta = self._read_meta(path)
            if meta is None:
                continue
            if cwd_filter is not None and meta.cwd != cwd_filter:
                continue
            metas.append(meta)
        metas.sort(key=lambda item: (item.updated_at, item.created_at), reverse=True)
        return metas[: max(0, limit)]

    def load(self, session_id: str) -> SessionRecord | None:
        """Load a session by exact id; returns None when missing or corrupt."""
        path = self.root / f"{session_id}.jsonl"
        if not path.is_file():
            return None
        meta = self._read_meta(path)
        if meta is None:
            return None
        messages: list[Message] = []
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return None
        for line in lines[1:]:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict):
                messages.append(_message_from_dict(data))
        meta.message_count = meta.message_count or len(messages)
        return SessionRecord(meta=meta, messages=messages)

    def resolve(self, ref: str, cwd: str | None = None) -> SessionRecord | None:
        """Resolve an exact/prefix session id or a 1-based index from ``list()``."""
        record = self.load(ref)
        if record is not None:
            return record
        matches = [meta for meta in self.list(limit=100, cwd=cwd) if meta.id.startswith(ref)]
        if len(matches) == 1:
            return self.load(matches[0].id)
        if ref.isdigit():
            metas = self.list(limit=100, cwd=cwd)
            index = int(ref)
            if 1 <= index <= len(metas):
                return self.load(metas[index - 1].id)
        return None

    def delete(self, session_id: str) -> bool:
        path = self.root / f"{session_id}.jsonl"
        if not path.is_file():
            return False
        path.unlink()
        return True

    def _read_meta(self, path: Path) -> SessionMeta | None:
        try:
            with path.open("r", encoding="utf-8") as handle:
                first = handle.readline().strip()
        except OSError:
            return None
        if not first:
            return None
        try:
            data = json.loads(first)
        except json.JSONDecodeError:
            return None
        if not isinstance(data, dict) or data.get("type") != "meta":
            return None
        return SessionMeta.from_dict(data)


class SessionWriter:
    """Append new messages of one live session to its JSONL transcript.

    The transcript file is created lazily on the first append, so opening and
    closing the REPL without a conversation leaves no file behind. When the
    in-memory history shrinks (context compression or /clear), the transcript
    is rewritten to stay in sync instead of appending past the truncation.
    """

    def __init__(self, store: SessionStore, meta: SessionMeta) -> None:
        self.store = store
        self.meta = meta
        self.persisted = 0

    @property
    def path(self) -> Path:
        return self.store.root / f"{self.meta.id}.jsonl"

    def append(self, messages: list[Message]) -> int:
        if len(messages) < self.persisted:
            return self._rewrite(messages)
        fresh = messages[self.persisted :]
        if not fresh:
            return 0
        if not self.meta.title:
            self.meta.title = _title_from(messages[: self.persisted + 1] or messages)
        self.store.root.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            for message in fresh:
                handle.write(json.dumps(_message_to_dict(message), ensure_ascii=False) + "\n")
        self.persisted += len(fresh)
        self.meta.message_count = self.persisted
        self.meta.updated_at = time.time()
        self._write_meta()
        return len(fresh)

    def _rewrite(self, messages: list[Message]) -> int:
        self.store.root.mkdir(parents=True, exist_ok=True)
        lines = [json.dumps(_message_to_dict(message), ensure_ascii=False) for message in messages]
        payload = ""
        if lines:
            payload = "\n".join(lines) + "\n"
        self.path.write_text(payload, encoding="utf-8")
        self.persisted = len(messages)
        self.meta.message_count = self.persisted
        self.meta.updated_at = time.time()
        self._write_meta()
        return self.persisted

    def _write_meta(self) -> None:
        self.store.root.mkdir(parents=True, exist_ok=True)
        existing: list[str] = []
        if self.path.is_file():
            existing = self.path.read_text(encoding="utf-8").splitlines()
        body: list[str] = existing
        if existing:
            try:
                head = json.loads(existing[0])
            except json.JSONDecodeError:
                head = None
            if isinstance(head, dict) and head.get("type") == "meta":
                body = existing[1:]
        meta_line = json.dumps(
            {"type": "meta", **self.meta.to_dict()},
            ensure_ascii=False,
        )
        self.path.write_text(
            "\n".join([meta_line, *body]) + ("\n" if body else ""),
            encoding="utf-8",
        )
