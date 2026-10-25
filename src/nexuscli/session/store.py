"""session/store.py — Durable conversation transcripts for the REPL.

Sessions live as JSONL files under ``~/.nexuscli/sessions/<id>.jsonl``. The
first line of each file is the session metadata; every following line is one
serialized :class:`~nexuscli.types.Message`. Transcripts are append-only per
turn and stay human-inspectable; resuming a session feeds the messages back
into ``Agent.history`` so the conversation continues with full context.

Every body line (messages and ``type: usage`` records alike) carries a
``_chain`` hash-commit field — see :class:`SessionWriter` for the chain rules
and the orphan tool_use trim semantics. Both are JSON field-level additions:
the ``role`` / ``content`` / ``type: usage`` line structure is unchanged and
the extra field is transparent to every existing consumer.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nexuscli.types import Message, Usage

SESSIONS_DIRNAME = "sessions"
_TITLE_MAX_CHARS = 80
# Record ``type`` of a per-turn usage line appended to a session transcript.
USAGE_RECORD_TYPE = "usage"
_TS_FORMAT = "%Y-%m-%d %H:%M:%S"
_SNIPPET_WINDOW_CHARS = 60


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
    # Session id this transcript was forked from; "" = a native session.
    forked_from: str = ""

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
            "forked_from": self.forked_from,
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
            forked_from=str(data.get("forked_from") or ""),
        )


@dataclass
class SessionRecord:
    meta: SessionMeta
    messages: list[Message] = field(default_factory=list)


@dataclass(slots=True)
class SessionSearchHit:
    """One cross-session search match: the session plus where the query hit."""

    session_id: str
    title: str
    snippet: str
    updated_at: float
    cwd: str = ""


@dataclass(slots=True)
class SessionUsageTotals:
    """Aggregate of the per-turn usage records stored in one transcript.

    Serves two roles: the resume-time baseline (summed from a session's
    ``usage`` record lines by ``SessionStore.load_usage``) and the running
    total of turns recorded in the current process (``ReplSessionState``).
    Costs are the per-turn estimates extracted by :func:`cost_totals`.
    """

    turns: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cache_hit_tokens: int = 0
    cache_miss_tokens: int = 0
    cost_usd: float = 0.0
    cost_cny: float = 0.0

    @classmethod
    def from_turn(cls, usage: Usage, cost: dict[str, Any] | None = None) -> SessionUsageTotals:
        """Build a one-turn total from an agent's last_usage / last_cost pair."""
        usd, cny = cost_totals(cost)
        return cls(
            turns=1,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            total_tokens=usage.total_tokens,
            cache_hit_tokens=usage.cache_hit_tokens,
            cache_miss_tokens=usage.cache_miss_tokens,
            cost_usd=usd,
            cost_cny=cny,
        )

    def __add__(self, other: SessionUsageTotals) -> SessionUsageTotals:
        if not isinstance(other, SessionUsageTotals):
            return NotImplemented
        return SessionUsageTotals(
            turns=self.turns + other.turns,
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
            cache_hit_tokens=self.cache_hit_tokens + other.cache_hit_tokens,
            cache_miss_tokens=self.cache_miss_tokens + other.cache_miss_tokens,
            cost_usd=self.cost_usd + other.cost_usd,
            cost_cny=self.cost_cny + other.cost_cny,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "turns": self.turns,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "cache_hit_tokens": self.cache_hit_tokens,
            "cache_miss_tokens": self.cache_miss_tokens,
            "cost_usd": self.cost_usd,
            "cost_cny": self.cost_cny,
        }


def cost_totals(cost: dict[str, Any] | None) -> tuple[float, float]:
    """Flatten an agent ``last_cost`` mapping to ``(usd, cny)`` totals.

    Same extraction as ``UsageStore.record_turn``: the mapping follows the
    shape returned by ``agent._calculate_costs`` — ``{currency: {total_cost}}``
    — and missing/empty entries degrade to 0.0.
    """
    costs = cost or {}
    usd = costs.get("usd") or {}
    cny = costs.get("cny") or {}
    return float(usd.get("total_cost") or 0.0), float(cny.get("total_cost") or 0.0)


def _content_text(content: Any) -> str:
    """Flatten a message content (string or multimodal part list) for matching."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict) and isinstance(part.get("text"), str):
                parts.append(part["text"])
        return "\n".join(parts)
    return ""


def _snippet_around(text: str, index: int, needle_length: int) -> str:
    """A whitespace-collapsed window around a match, ellipsized at the edges."""
    flat = " ".join(text.split())
    start = max(0, index - _SNIPPET_WINDOW_CHARS)
    end = min(len(flat), index + needle_length + _SNIPPET_WINDOW_CHARS)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(flat) else ""
    return prefix + flat[start:end] + suffix


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


def _line_type(line: str) -> str:
    """The ``type`` field of a body line ('' for messages / corrupt lines)."""
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return ""
    if isinstance(data, dict):
        return str(data.get("type") or "")
    return ""


# Zero anchor of the per-line transcript commit chain: the first body line —
# and any line whose preceding body line carries no ``_chain`` (legacy
# transcript) — hashes from here.
TRANSCRIPT_CHAIN_ZERO = "0" * 64


def transcript_chain_hash(prev_hash: str, record: dict[str, Any]) -> str:
    """Commit hash of one transcript body line, hashed *without* its ``_chain``.

    Ported from kitagent (MIT, zed123214/kitagent) core/session/store.py
    ``transcript_commit`` / ``canonical_transcript_commit``: committed content
    is digested as canonical JSON (sorted keys, compact separators), so the
    hash is reproducible from the stored line regardless of key order or
    spacing. Adapted from kitagent's per-run commit hash to a per-line chain:
    ``sha256(prev_hash + canonical(record))``.
    """
    payload = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256((prev_hash + payload).encode("utf-8")).hexdigest()


def _stamp_chain(record: dict[str, Any], prev_hash: str) -> str:
    """Set ``record["_chain"]`` to its commit hash and return the new hash."""
    chain = transcript_chain_hash(prev_hash, record)
    record["_chain"] = chain
    return chain


def _last_body_chain(lines: list[str]) -> str:
    """``_chain`` of the last reader-visible body line — the next append's prev.

    Mirrors exactly what ``runtime/reads.py`` ``read_transcript`` will see, so
    writer and verifier agree on which line is "the previous one":
    unparseable lines and blank lines never become records (skipped here too),
    a stray ``type: meta`` body line is filtered by the reader (skipped as
    well), while any other visible line anchors the chain — at its ``_chain``
    value, or at the zero hash when it has none (legacy transcript). Scanning
    stops at the first visible line, so per-append hashing stays O(1) in the
    new lines; only externally damaged files can push the scan further back.
    """
    for line in reversed(lines):
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            return TRANSCRIPT_CHAIN_ZERO
        if data.get("type") == "meta":
            continue
        chain = data.get("_chain")
        if isinstance(chain, str) and chain:
            return chain
        return TRANSCRIPT_CHAIN_ZERO
    return TRANSCRIPT_CHAIN_ZERO


def _title_from(messages: list[Message]) -> str:
    for message in messages:
        if message.role == "user" and isinstance(message.content, str) and message.content.strip():
            return " ".join(message.content.split())[:_TITLE_MAX_CHARS]
    return ""


def _new_session_id(now: float) -> str:
    """Timestamped-unique session id (shared by new sessions and forks)."""
    return f"{time.strftime('%Y%m%d-%H%M%S', time.localtime(now))}-{uuid.uuid4().hex[:6]}"


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
        fsync: bool = False,
    ) -> SessionWriter:
        """Create an in-memory session that only touches disk on first append."""
        now = time.time()
        meta = SessionMeta(
            id=_new_session_id(now),
            cwd=str(Path(cwd).resolve()),
            created_at=now,
            updated_at=now,
            model=model,
            provider=provider,
        )
        return SessionWriter(self, meta, fsync=fsync)

    def writer_for(
        self,
        meta: SessionMeta,
        messages: list[Message],
        *,
        fsync: bool = False,
    ) -> SessionWriter:
        """Continue an existing session, appending past ``messages``."""
        writer = SessionWriter(self, meta, fsync=fsync)
        writer.persisted = len(messages)
        return writer

    def fork(self, session_id: str, *, title: str | None = None) -> SessionRecord | None:
        """Copy an existing transcript to a new session id; source untouched.

        The new transcript holds the full message history and records the
        source id in ``forked_from`` (a snapshot-style link: fork-point facts
        live on the fork itself, never re-read from the live parent — same
        rule as ZCode's stable-fork metadata). ``title=None`` inherits the
        source title; an empty source title lets SessionWriter.append derive
        one from the first user message as usual. Nothing else is inherited:
        the session goal (keyed by session id) is deliberately not copied at
        fork time — ZCode's fork-time goal snapshot is not ported.
        """
        record = self.load(session_id)
        if record is None:
            return None
        now = time.time()
        meta = SessionMeta(
            id=_new_session_id(now),
            cwd=record.meta.cwd,
            created_at=now,
            updated_at=now,
            model=record.meta.model,
            provider=record.meta.provider,
            title=title if title is not None else record.meta.title,
            forked_from=record.meta.id,
        )
        writer = SessionWriter(self, meta)
        writer.append(list(record.messages))
        return SessionRecord(meta=meta, messages=list(record.messages))

    def list(self, limit: int = 20, cwd: str | None = None) -> list[SessionMeta]:
        """Most recent sessions first, optionally filtered by working directory."""
        if not self.root.exists():
            return []
        cwd_filter = os.path.normcase(str(Path(cwd).resolve())) if cwd is not None else None
        metas: list[SessionMeta] = []
        for path in self.root.glob("*.jsonl"):
            meta = self._read_meta(path)
            if meta is None:
                continue
            if cwd_filter is not None and os.path.normcase(meta.cwd) != cwd_filter:
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
            if not isinstance(data, dict):
                continue
            # Non-message records (currently per-turn ``usage`` lines) are
            # tolerated by design: they ride in the transcript body but are
            # not conversation messages.
            if "type" in data:
                continue
            messages.append(_message_from_dict(data))
        meta.message_count = meta.message_count or len(messages)
        return SessionRecord(meta=meta, messages=messages)

    def load_usage(self, session_id: str) -> SessionUsageTotals:
        """Sum the per-turn ``usage`` record lines of one transcript.

        This is the resume-time usage baseline: historical turns persist with
        the transcript, so a resumed session continues counting instead of
        starting from zero. Missing keys, corrupt lines and unknown sessions
        all degrade to zeros — the baseline must never raise.
        """
        path = self.root / f"{session_id}.jsonl"
        if not path.is_file():
            return SessionUsageTotals()
        totals = SessionUsageTotals()
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return totals
        for line in lines[1:]:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(data, dict) or data.get("type") != USAGE_RECORD_TYPE:
                continue
            # A syntactically valid line with garbage numeric fields (e.g. a
            # hand-edited transcript) must degrade like a corrupt line, not
            # raise — per the contract above. Fields parse into locals first
            # so a failure skips the whole line instead of partially applying.
            try:
                input_tokens = int(data.get("input_tokens") or 0)
                output_tokens = int(data.get("output_tokens") or 0)
                total_tokens = int(data.get("total_tokens") or 0)
                cache_hit = int(data.get("cache_hit_tokens") or 0)
                cache_miss = int(data.get("cache_miss_tokens") or 0)
                cost_usd = float(data.get("cost_usd") or 0.0)
                cost_cny = float(data.get("cost_cny") or 0.0)
            except (TypeError, ValueError):
                continue
            totals.turns += 1
            totals.input_tokens += input_tokens
            totals.output_tokens += output_tokens
            totals.total_tokens += total_tokens
            totals.cache_hit_tokens += cache_hit
            totals.cache_miss_tokens += cache_miss
            totals.cost_usd += cost_usd
            totals.cost_cny += cost_cny
        return totals

    def search(
        self,
        query: str,
        *,
        cwd: str | None = None,
        limit: int = 20,
    ) -> list[SessionSearchHit]:
        """Scan transcripts for a case-insensitive keyword in message content.

        ``cwd=None`` searches every project; a cwd restricts to that project
        (same filter as ``list``). Newest session first, one hit per session
        (the earliest matching message), at most ``limit`` hits.
        """
        needle = query.strip().lower()
        if not needle:
            return []
        cwd_filter = os.path.normcase(str(Path(cwd).resolve())) if cwd is not None else None
        hits: list[SessionSearchHit] = []
        # ``limit`` on the hit list is enforced below; the meta scan itself
        # needs every session (newest first, same ordering as ``list``).
        for meta in self.list(limit=10**6, cwd=cwd_filter):
            hit = self._first_message_hit(meta, needle)
            if hit is not None:
                hits.append(hit)
            if len(hits) >= limit:
                break
        return hits

    def _first_message_hit(self, meta: SessionMeta, needle: str) -> SessionSearchHit | None:
        path = self.root / f"{meta.id}.jsonl"
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
            if not isinstance(data, dict) or "type" in data:
                continue
            text = _content_text(data.get("content"))
            index = text.lower().find(needle)
            if index < 0:
                continue
            return SessionSearchHit(
                session_id=meta.id,
                title=meta.title,
                snippet=_snippet_around(text, index, len(needle)),
                updated_at=meta.updated_at,
                cwd=meta.cwd,
            )
        return None

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

    Hash-commit chain (Ported from kitagent (MIT, zed123214/kitagent)
    core/session/store.py ``transcript_commit``, adapted from a per-run commit
    hash to a per-line chain): every body line this writer produces carries
    ``_chain = sha256(prev_hash + canonical line JSON)``, where ``prev_hash``
    is the previous body line's ``_chain`` (zero string for the first line or
    after a line without one — see ``_last_body_chain``). Hashing touches only
    the new lines, so an append pays O(1) hashing on top of its existing
    whole-file rewrite; ``_rewrite`` re-chains the entire body it already
    rebuilds. Read-side verification lives in ``runtime/reads.read_transcript``
    (opt-in ``verify_chain``).

    Orphan tool_use trimming (Ported from kitagent (MIT, zed123214/kitagent)
    core/session/store.py ``_trim_orphan_tool_use``) is deliberately NOT done
    anywhere: transcripts stay append-only historical facts and readers are
    read-only — ``runtime/reads.py`` annotates assistant messages whose tool
    calls never got a paired tool result (crash-truncation artifacts) as an
    ``orphans`` count instead of trimming. If a physical trim ever lands, its
    place is this write path (``append`` / ``_rewrite``), never the readers.
    """

    def __init__(self, store: SessionStore, meta: SessionMeta, *, fsync: bool = False) -> None:
        self.store = store
        self.meta = meta
        self.persisted = 0
        # Ported from kitagent (MIT, zed123214/kitagent) core/events/writer.py:
        # 每次写盘后 flush；fsync 为可选开关（默认关闭，不改变既有性能特征）。
        self.fsync = fsync

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
        self.persisted += len(fresh)
        self.meta.message_count = self.persisted
        self.meta.updated_at = time.time()
        body = self._read_body()
        prev_hash = _last_body_chain(body)
        for message in fresh:
            record = _message_to_dict(message)
            prev_hash = _stamp_chain(record, prev_hash)
            body.append(json.dumps(record, ensure_ascii=False))
        self._atomic_write(self._compose(body))
        return len(fresh)

    def _rewrite(self, messages: list[Message]) -> int:
        self.store.root.mkdir(parents=True, exist_ok=True)
        self.persisted = len(messages)
        self.meta.message_count = self.persisted
        self.meta.updated_at = time.time()
        # A rewrite re-chains every line from the zero hash: the body is a new
        # byte sequence (compression, /clear-style shrink), so the old chain
        # values no longer commit to anything. Hashing all lines adds O(n) on
        # top of an already-O(n) rewrite; the per-append O(1) guarantee covers
        # the append fast path above.
        body: list[str] = []
        prev_hash = TRANSCRIPT_CHAIN_ZERO
        for message in messages:
            record = _message_to_dict(message)
            prev_hash = _stamp_chain(record, prev_hash)
            body.append(json.dumps(record, ensure_ascii=False))
        # Non-message records (per-turn ``usage`` lines) are historical facts,
        # not conversation state: a history shrink (compression, /clear-style
        # rewrite) must not erase the usage ledger.
        for line in self._read_body():
            if _line_type(line) != USAGE_RECORD_TYPE:
                continue
            record = json.loads(line)
            record.pop("_chain", None)
            prev_hash = _stamp_chain(record, prev_hash)
            body.append(json.dumps(record, ensure_ascii=False))
        self._atomic_write(self._compose(body))
        return self.persisted

    def append_usage(
        self,
        *,
        model: str = "",
        provider: str = "",
        usage: Usage,
        cost: dict[str, Any] | None = None,
        ts: str | None = None,
    ) -> None:
        """Append one completed turn's usage as a ``type: usage`` record line.

        Called for every completed regular ReAct turn so a resumed session can
        rebuild its usage baseline from the transcript. ``ts`` is a
        fixed-instant seam for tests (defaults to "now", local time, in the
        same ``_TS_FORMAT`` layout as ``~/.nexuscli/usage.db``). The record
        line carries a ``_chain`` commit hash like message lines do.
        """
        usd, cny = cost_totals(cost)
        record = {
            "type": USAGE_RECORD_TYPE,
            "ts": ts or time.strftime(_TS_FORMAT),
            "model": model,
            "provider": provider,
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "total_tokens": usage.total_tokens,
            "cache_hit_tokens": usage.cache_hit_tokens,
            "cache_miss_tokens": usage.cache_miss_tokens,
            "cost_usd": usd,
            "cost_cny": cny,
        }
        self.store.root.mkdir(parents=True, exist_ok=True)
        body = self._read_body()
        _stamp_chain(record, _last_body_chain(body))
        body.append(json.dumps(record, ensure_ascii=False))
        self._atomic_write(self._compose(body))

    def _write_meta(self) -> None:
        """Rewrite the meta line in place (used to bump ordering metadata)."""
        self.store.root.mkdir(parents=True, exist_ok=True)
        self._atomic_write(self._compose(self._read_body()))

    def _compose(self, body: list[str]) -> str:
        meta_line = json.dumps(
            {"type": "meta", **self.meta.to_dict()},
            ensure_ascii=False,
        )
        return "\n".join([meta_line, *body]) + ("\n" if body else "")

    def _read_body(self) -> list[str]:
        """Message lines of the transcript (everything after the meta line)."""
        if not self.path.is_file():
            return []
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        if not lines:
            return []
        try:
            head = json.loads(lines[0])
        except json.JSONDecodeError:
            head = None
        if isinstance(head, dict) and head.get("type") == "meta":
            return lines[1:]
        # Meta line missing (legacy/corrupt file): keep the messages and let
        # the next write self-heal by prepending fresh metadata.
        return lines

    def _atomic_write(self, text: str) -> None:
        """Write via temp file + rename so a crash never truncates the transcript.

        Flush 纪律 ported from kitagent (MIT, zed123214/kitagent)
        core/events/writer.py：写后先 ``flush()`` 把载荷推到 OS，再原子替换落地；
        ``os.fsync`` 仅当 writer 以 ``fsync=True`` 构建时执行（默认关闭，保持
        既有性能特征）。临时文件 + ``os.replace`` 的原子替换语义不变。
        """
        tmp = self.path.with_name(self.path.name + ".tmp")
        with tmp.open("w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            if self.fsync:
                os.fsync(handle.fileno())
        os.replace(tmp, self.path)
