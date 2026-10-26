from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nexuscli.memory.memory_lifecycle import hotness_score
from nexuscli.memory.signals import (
    DEFAULT_TEMPORAL_KIND,
    HOTNESS_HALF_LIFE_DAYS,
    TEMPORAL_KINDS,
    WEIGHT_CONFIDENCE,
    WEIGHT_HOTNESS,
    WEIGHT_IMPORTANCE,
    MemorySignal,
    SignalQuery,
    content_scores,
    default_signals,
    derive_temporal_kind,
    effective_temporal_kind,
    extract_entities,
    normalize_entities,
    normalize_text,
    temporal_kind_for_kind,
    temporal_multiplier,
)
from nexuscli.memory.signals import (
    MAX_ENTITIES as _MAX_ENTITIES,
)

DEFAULT_MAX_ENTRIES_PER_SCOPE = 1_000
DEFAULT_MAX_CONTENT_LENGTH = 8_000
_MAX_METADATA_LENGTH = 128
_ENTRY_COLUMNS = """
    id, scope, content, created_at, kind, source, importance, confidence,
    updated_at, expires_at, access_count, content_hash, entities, event_at, temporal_kind
"""


@dataclass(slots=True)
class MemoryEntry:
    id: int
    scope: str
    content: str
    created_at: str
    kind: str = "fact"
    source: str = "manual"
    importance: float = 0.5
    confidence: float = 1.0
    updated_at: str = ""
    expires_at: str | None = None
    access_count: int = 0
    content_hash: str = ""
    entities: list[str] = field(default_factory=list)
    event_at: str | None = None
    temporal_kind: str = DEFAULT_TEMPORAL_KIND


class MemoryManager:
    def __init__(
        self,
        db_path: str | Path,
        scope: str,
        *,
        max_entries: int = DEFAULT_MAX_ENTRIES_PER_SCOPE,
        max_content_length: int = DEFAULT_MAX_CONTENT_LENGTH,
        signals: Sequence[MemorySignal] | None = None,
    ):
        self.db_path = Path(db_path).expanduser()
        self.scope = str(scope)
        self.max_entries = _positive_int(max_entries, "max_entries")
        self.max_content_length = _positive_int(max_content_length, "max_content_length")
        # Relevance signals are injectable so tests (and future vector support)
        # can swap the set; the default is lexical + BM25 + entity.
        self.signals: list[MemorySignal] = (
            list(signals) if signals is not None else default_signals()
        )
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def save(
        self,
        content: str,
        *,
        kind: str = "fact",
        source: str = "manual",
        importance: float = 0.5,
        confidence: float = 1.0,
        expires_at: str | datetime | None = None,
        entities: Iterable[str] | None = None,
        event_at: str | datetime | None = None,
        temporal_kind: str | None = None,
    ) -> int:
        """Store a memory; the write path is ADD-only.

        A second arrival of the same normalised content (same ``content_hash``)
        accumulates onto the existing row instead of redefining it: ``kind``,
        ``source``, ``expires_at`` and ``temporal_kind`` keep their original
        declaration, ``importance`` / ``confidence`` take the max, ``entities``
        are unioned, ``event_at`` is only filled when it was empty, and
        ``updated_at`` is refreshed. Dedup, TTL and quota semantics are unchanged.
        """
        cleaned = _clean_content(content)
        if len(cleaned) > self.max_content_length:
            raise ValueError(f"memory content exceeds {self.max_content_length} characters")
        normalized_kind = _validate_metadata(kind, "kind")
        normalized_source = _validate_metadata(source, "source")
        normalized_importance = _validate_score(importance, "importance")
        normalized_confidence = _validate_score(confidence, "confidence")
        normalized_expiry = _normalize_optional_timestamp(expires_at, "expires_at")
        normalized_event_at = _normalize_optional_timestamp(event_at, "event_at")
        explicit_entities = normalize_entities(entities)
        normalized_entities = explicit_entities or extract_entities(cleaned)
        normalized_temporal = derive_temporal_kind(normalized_kind, cleaned, temporal_kind)
        now = _now_iso()
        content_hash = _hash_content(cleaned)

        with self._connect() as conn:
            conn.execute("begin immediate")
            self._purge_expired(conn)
            row = conn.execute(
                f"""
                select {_ENTRY_COLUMNS}
                from memories
                where scope = ? and content_hash = ?
                """,
                (self.scope, content_hash),
            ).fetchone()
            if row:
                existing = _entry_from_row(row)
                memory_id = existing.id
                merged_entities = sorted(set(existing.entities) | set(normalized_entities))
                conn.execute(
                    """
                    update memories
                    set content = ?, importance = ?, confidence = ?, updated_at = ?,
                        entities = ?, event_at = coalesce(event_at, ?)
                    where id = ? and scope = ?
                    """,
                    (
                        cleaned,
                        max(existing.importance, normalized_importance),
                        max(existing.confidence, normalized_confidence),
                        now,
                        _entities_to_json(merged_entities),
                        normalized_event_at,
                        memory_id,
                        self.scope,
                    ),
                )
            else:
                cursor = conn.execute(
                    """
                    insert into memories(
                        scope, content, created_at, kind, source, importance, confidence,
                        updated_at, expires_at, access_count, content_hash,
                        entities, event_at, temporal_kind
                    ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?)
                    """,
                    (
                        self.scope,
                        cleaned,
                        now,
                        normalized_kind,
                        normalized_source,
                        normalized_importance,
                        normalized_confidence,
                        now,
                        normalized_expiry,
                        content_hash,
                        _entities_to_json(normalized_entities),
                        normalized_event_at,
                        normalized_temporal,
                    ),
                )
                memory_id = int(cursor.lastrowid)
            self._enforce_quota(conn, protected_id=memory_id)
            return memory_id

    def list(self, limit: int = 20, *, kind: str | None = None) -> list[MemoryEntry]:
        normalized_limit = _normalize_limit(limit)
        if normalized_limit == 0:
            return []
        params: list[Any] = [self.scope, _now_iso()]
        kind_clause = ""
        if kind is not None:
            kind_clause = " and kind = ?"
            params.append(_validate_metadata(kind, "kind"))
        params.append(normalized_limit)
        with self._connect() as conn:
            self._purge_expired(conn)
            rows = conn.execute(
                f"""
                select {_ENTRY_COLUMNS}
                from memories
                where scope = ?
                  and (expires_at is null or expires_at > ?)
                  {kind_clause}
                order by updated_at desc, id desc
                limit ?
                """,
                params,
            ).fetchall()
        return [_entry_from_row(row) for row in rows]

    def search(self, query: str, limit: int = 10) -> list[MemoryEntry]:
        """Search without mutating access counters; kept compatible with the original API."""

        return self.recall(query, limit=limit, mark_access=False)

    def recall(
        self,
        query: str,
        limit: int = 10,
        *,
        kinds: Iterable[str] | None = None,
        min_score: float = 0.0,
        mark_access: bool = True,
        entities: Iterable[str] | None = None,
        temporal_kind: str | None = None,
    ) -> list[MemoryEntry]:
        """Relevance-ranked recall over the scope.

        ``entities`` adds explicit query entities on top of the heuristic ones;
        ``temporal_kind`` overrides the keyword-table temporal intent (one of
        ``current_state`` / ``past_event`` / ``future_plan``).
        """
        normalized_limit = _normalize_limit(limit)
        if normalized_limit == 0:
            return []
        cleaned_query = " ".join(str(query or "").split())
        if not cleaned_query:
            return self.list(normalized_limit)
        signal_query = SignalQuery.build(
            cleaned_query, entities=entities, temporal_kind=temporal_kind
        )

        normalized_kinds = None
        if kinds is not None:
            normalized_kinds = {_validate_metadata(kind, "kind") for kind in kinds}
            if not normalized_kinds:
                return []

        params: list[Any] = [self.scope, _now_iso()]
        kinds_clause = ""
        if normalized_kinds:
            placeholders = ", ".join("?" for _ in normalized_kinds)
            kinds_clause = f" and kind in ({placeholders})"
            params.extend(sorted(normalized_kinds))

        with self._connect() as conn:
            self._purge_expired(conn)
            rows = conn.execute(
                f"""
                select {_ENTRY_COLUMNS}
                from memories
                where scope = ?
                  and (expires_at is null or expires_at > ?)
                  {kinds_clause}
                """,
                params,
            ).fetchall()

        now = datetime.now(UTC)
        entries = [_entry_from_row(row) for row in rows]
        scores = _relevance_scores(signal_query, entries, now, self.signals)
        ranked: list[tuple[float, MemoryEntry]] = [
            (score, entry)
            for score, entry in zip(scores, entries, strict=True)
            if score > 0 and score >= float(min_score)
        ]
        ranked.sort(
            key=lambda item: (
                item[0],
                item[1].importance,
                item[1].confidence,
                item[1].updated_at,
                item[1].id,
            ),
            reverse=True,
        )
        selected = [entry for _score, entry in ranked[:normalized_limit]]
        if mark_access and selected:
            self.mark_access(entry.id for entry in selected)
            for entry in selected:
                entry.access_count += 1
        return selected

    def mark_access(self, memory_ids: int | Iterable[int]) -> int:
        ids = [memory_ids] if isinstance(memory_ids, int) else list(memory_ids)
        normalized_ids = list(dict.fromkeys(int(memory_id) for memory_id in ids))
        if not normalized_ids:
            return 0

        changed = 0
        with self._connect() as conn:
            for start in range(0, len(normalized_ids), 500):
                chunk = normalized_ids[start : start + 500]
                placeholders = ", ".join("?" for _ in chunk)
                cursor = conn.execute(
                    f"""
                    update memories
                    set access_count = access_count + 1
                    where scope = ? and id in ({placeholders})
                    """,
                    [self.scope, *chunk],
                )
                changed += int(cursor.rowcount)
        return changed

    def stats(self) -> dict[str, Any]:
        with self._connect() as conn:
            self._purge_expired(conn)
            row = conn.execute(
                """
                select count(*) as total,
                       coalesce(sum(access_count), 0) as total_access_count,
                       coalesce(avg(importance), 0) as average_importance,
                       coalesce(avg(confidence), 0) as average_confidence,
                       min(created_at) as oldest_created_at,
                       max(updated_at) as newest_updated_at
                from memories
                where scope = ?
                """,
                (self.scope,),
            ).fetchone()
            kind_rows = conn.execute(
                """
                select kind, count(*) as count
                from memories
                where scope = ?
                group by kind
                order by kind
                """,
                (self.scope,),
            ).fetchall()
        return {
            "scope": self.scope,
            "total": int(row["total"]),
            "by_kind": {str(item["kind"]): int(item["count"]) for item in kind_rows},
            "total_access_count": int(row["total_access_count"]),
            "average_importance": float(row["average_importance"]),
            "average_confidence": float(row["average_confidence"]),
            "oldest_created_at": row["oldest_created_at"],
            "newest_updated_at": row["newest_updated_at"],
            "max_entries": self.max_entries,
        }

    def delete(self, memory_id: int) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                "delete from memories where scope = ? and id = ?",
                (self.scope, int(memory_id)),
            )
            return bool(cursor.rowcount)

    def clear(self) -> int:
        with self._connect() as conn:
            cursor = conn.execute("delete from memories where scope = ?", (self.scope,))
            return int(cursor.rowcount)

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                create table if not exists memories (
                    id integer primary key autoincrement,
                    scope text not null,
                    content text not null,
                    created_at text not null,
                    kind text not null default 'fact',
                    source text not null default 'manual',
                    importance real not null default 0.5,
                    confidence real not null default 1.0,
                    updated_at text not null default '',
                    expires_at text,
                    access_count integer not null default 0,
                    content_hash text not null default '',
                    entities text not null default '[]',
                    event_at text,
                    temporal_kind text not null default ''
                )
                """
            )
            columns = {
                str(row["name"]) for row in conn.execute("pragma table_info(memories)").fetchall()
            }
            if "kind" not in columns:
                conn.execute("alter table memories add column kind text not null default 'fact'")
            if "source" not in columns:
                conn.execute(
                    "alter table memories add column source text not null default 'legacy'"
                )
            if "importance" not in columns:
                conn.execute("alter table memories add column importance real not null default 0.5")
            if "confidence" not in columns:
                conn.execute("alter table memories add column confidence real not null default 1.0")
            if "updated_at" not in columns:
                conn.execute("alter table memories add column updated_at text not null default ''")
            if "expires_at" not in columns:
                conn.execute("alter table memories add column expires_at text")
            if "access_count" not in columns:
                conn.execute(
                    "alter table memories add column access_count integer not null default 0"
                )
            if "content_hash" not in columns:
                conn.execute(
                    "alter table memories add column content_hash text not null default ''"
                )
            if "entities" not in columns:
                conn.execute("alter table memories add column entities text not null default '[]'")
            if "event_at" not in columns:
                conn.execute("alter table memories add column event_at text")
            if "temporal_kind" not in columns:
                conn.execute(
                    "alter table memories add column temporal_kind text not null default ''"
                )

            # The dedup pass rewrites every row; guard it with a version marker
            # so constructing a manager (which the REPL does per command) does
            # not take a write lock and rescan the whole table every time.
            version = int(conn.execute("pragma user_version").fetchone()[0])
            if version < 1:
                conn.execute("drop index if exists idx_memories_scope_hash")
                self._normalize_and_deduplicate(conn)
                conn.execute("pragma user_version = 1")
            # Version 2 adds the temporal layer and entity tags: rows written
            # before it get temporal_kind from their kind and entities from the
            # heuristic extractor, once, behind the same marker.
            if version < 2:
                self._backfill_temporal_and_entities(conn)
                conn.execute("pragma user_version = 2")
            conn.execute("create index if not exists idx_memories_scope on memories(scope, id)")
            conn.execute(
                """
                create unique index if not exists idx_memories_scope_hash
                on memories(scope, content_hash)
                """
            )
            conn.execute(
                """
                create index if not exists idx_memories_scope_updated
                on memories(scope, updated_at desc, id desc)
                """
            )
            conn.execute(
                """
                create index if not exists idx_memories_scope_kind
                on memories(scope, kind)
                """
            )
            self._enforce_quota(conn)

    def _backfill_temporal_and_entities(self, conn: sqlite3.Connection) -> None:
        rows = conn.execute(
            """
            select id, content, kind, entities, temporal_kind
            from memories
            where temporal_kind not in (?, ?, ?) or entities = '' or entities = '[]'
            order by id
            """,
            tuple(sorted(TEMPORAL_KINDS)),
        ).fetchall()
        for row in rows:
            content = str(row["content"] or "")
            temporal_kind = str(row["temporal_kind"] or "")
            if temporal_kind not in TEMPORAL_KINDS:
                temporal_kind = temporal_kind_for_kind(str(row["kind"] or "fact"))
            entities = _entities_from_json(row["entities"])
            if not entities:
                entities = extract_entities(content)
            conn.execute(
                "update memories set temporal_kind = ?, entities = ? where id = ?",
                (temporal_kind, _entities_to_json(entities), int(row["id"])),
            )

    def _normalize_and_deduplicate(self, conn: sqlite3.Connection) -> None:
        rows = conn.execute(
            f"""
            select {_ENTRY_COLUMNS}
            from memories
            order by id
            """
        ).fetchall()
        seen: dict[tuple[str, str], dict[str, Any]] = {}
        now = _now_iso()
        for row in rows:
            memory_id = int(row["id"])
            content = str(row["content"] or "").strip()
            if not content:
                conn.execute("delete from memories where id = ?", (memory_id,))
                continue
            scope = str(row["scope"] or "")
            created_at = _coerce_timestamp(row["created_at"], now)
            updated_at = _coerce_timestamp(row["updated_at"], created_at)
            state: dict[str, Any] = {
                "id": memory_id,
                "scope": scope,
                "content": content,
                "created_at": created_at,
                "kind": _coerce_metadata(row["kind"], "fact"),
                "source": _coerce_metadata(row["source"], "legacy"),
                "importance": _coerce_score(row["importance"], 0.5),
                "confidence": _coerce_score(row["confidence"], 1.0),
                "updated_at": updated_at,
                "expires_at": _coerce_optional_timestamp(row["expires_at"]),
                "access_count": max(int(row["access_count"] or 0), 0),
                "content_hash": _hash_content(content),
            }
            key = (scope, state["content_hash"])
            keeper = seen.get(key)
            if keeper is None:
                self._write_migrated_row(conn, state)
                seen[key] = state
                continue

            if state["updated_at"] >= keeper["updated_at"]:
                keeper["content"] = state["content"]
                keeper["kind"] = state["kind"]
                keeper["source"] = state["source"]
                keeper["updated_at"] = state["updated_at"]
            keeper["created_at"] = min(keeper["created_at"], state["created_at"])
            keeper["importance"] = max(keeper["importance"], state["importance"])
            keeper["confidence"] = max(keeper["confidence"], state["confidence"])
            keeper["expires_at"] = _merge_expiry(keeper["expires_at"], state["expires_at"])
            keeper["access_count"] += state["access_count"]
            self._write_migrated_row(conn, keeper)
            conn.execute("delete from memories where id = ?", (memory_id,))

    def _write_migrated_row(self, conn: sqlite3.Connection, state: dict[str, Any]) -> None:
        conn.execute(
            """
            update memories
            set scope = ?, content = ?, created_at = ?, kind = ?, source = ?,
                importance = ?, confidence = ?, updated_at = ?, expires_at = ?,
                access_count = ?, content_hash = ?
            where id = ?
            """,
            (
                state["scope"],
                state["content"],
                state["created_at"],
                state["kind"],
                state["source"],
                state["importance"],
                state["confidence"],
                state["updated_at"],
                state["expires_at"],
                state["access_count"],
                state["content_hash"],
                state["id"],
            ),
        )

    def _purge_expired(self, conn: sqlite3.Connection) -> int:
        cursor = conn.execute(
            """
            delete from memories
            where scope = ? and expires_at is not null and expires_at <= ?
            """,
            (self.scope, _now_iso()),
        )
        return int(cursor.rowcount)

    def _enforce_quota(
        self,
        conn: sqlite3.Connection,
        *,
        protected_id: int | None = None,
    ) -> int:
        self._purge_expired(conn)
        total = int(
            conn.execute("select count(*) from memories where scope = ?", (self.scope,)).fetchone()[
                0
            ]
        )
        excess = total - self.max_entries
        if excess <= 0:
            return 0
        protected_clause = ""
        params: list[Any] = [self.scope]
        if protected_id is not None:
            protected_clause = " and id != ?"
            params.append(protected_id)
        params.append(excess)
        cursor = conn.execute(
            f"""
            delete from memories
            where id in (
                select id from memories
                where scope = ? {protected_clause}
                order by importance asc, confidence asc, access_count asc,
                         updated_at asc, id asc
                limit ?
            )
            """,
            params,
        )
        return int(cursor.rowcount)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("pragma busy_timeout = 30000")
        return conn


def _entry_from_row(row: sqlite3.Row) -> MemoryEntry:
    temporal_kind = str(row["temporal_kind"] or "")
    if temporal_kind not in TEMPORAL_KINDS:
        temporal_kind = temporal_kind_for_kind(str(row["kind"]))
    return MemoryEntry(
        id=int(row["id"]),
        scope=str(row["scope"]),
        content=str(row["content"]),
        created_at=str(row["created_at"]),
        kind=str(row["kind"]),
        source=str(row["source"]),
        importance=float(row["importance"]),
        confidence=float(row["confidence"]),
        updated_at=str(row["updated_at"]),
        expires_at=str(row["expires_at"]) if row["expires_at"] is not None else None,
        access_count=int(row["access_count"]),
        content_hash=str(row["content_hash"]),
        entities=_entities_from_json(row["entities"]),
        event_at=str(row["event_at"]) if row["event_at"] is not None else None,
        temporal_kind=temporal_kind,
    )


def _entities_to_json(entities: Iterable[str]) -> str:
    # ADD-only 并集的上限（复审 iter1-F3）：同 hash 多次到达且每次带不同实体列表时，
    # 不设帽会让列内容按 32×到达次数无界增长（纯存储膨胀）。按排序确定性截断。
    return json.dumps(sorted(set(entities))[:_MAX_ENTITIES], ensure_ascii=False)


def _entities_from_json(raw: Any) -> list[str]:
    if raw is None:
        return []
    try:
        data = json.loads(str(raw))
    except (TypeError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    return sorted({str(item) for item in data if isinstance(item, str) and item})


def _clean_content(content: str) -> str:
    if not isinstance(content, str):
        raise TypeError("memory content must be a string")
    cleaned = content.strip()
    if not cleaned:
        raise ValueError("memory content must not be empty")
    return cleaned


def _hash_content(content: str) -> str:
    normalized = normalize_text(content)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _validate_metadata(value: str, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"memory {field} must not be empty")
    if len(normalized) > _MAX_METADATA_LENGTH:
        raise ValueError(f"memory {field} exceeds {_MAX_METADATA_LENGTH} characters")
    return normalized


def _coerce_metadata(value: Any, fallback: str) -> str:
    normalized = str(value or "").strip()
    if not normalized or len(normalized) > _MAX_METADATA_LENGTH:
        return fallback
    return normalized


def _validate_score(value: float, field: str) -> float:
    try:
        normalized = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"memory {field} must be a number between 0 and 1") from exc
    if not 0 <= normalized <= 1:
        raise ValueError(f"memory {field} must be between 0 and 1")
    return normalized


def _coerce_score(value: Any, fallback: float) -> float:
    try:
        normalized = float(value)
    except (TypeError, ValueError):
        return fallback
    return min(max(normalized, 0.0), 1.0)


def _positive_int(value: int, field: str) -> int:
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a positive integer") from exc
    if normalized <= 0:
        raise ValueError(f"{field} must be a positive integer")
    return normalized


def _normalize_limit(value: int) -> int:
    try:
        return max(int(value), 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("limit must be an integer") from exc


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _normalize_optional_timestamp(
    value: str | datetime | None, field: str = "expires_at"
) -> str | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    parsed = _parse_timestamp(value)
    if parsed is None:
        raise ValueError(f"{field} must be a valid ISO-8601 timestamp")
    return parsed.isoformat()


def _coerce_optional_timestamp(value: Any) -> str | None:
    if value is None or not str(value).strip():
        return None
    parsed = _parse_timestamp(str(value))
    return parsed.isoformat() if parsed else None


def _coerce_timestamp(value: Any, fallback: str) -> str:
    parsed = _parse_timestamp(str(value or ""))
    if parsed:
        return parsed.isoformat()
    fallback_parsed = _parse_timestamp(fallback)
    return fallback_parsed.isoformat() if fallback_parsed else _now_iso()


def _parse_timestamp(value: str | datetime) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _merge_expiry(left: str | None, right: str | None) -> str | None:
    if left is None or right is None:
        return None
    return max(left, right)


def _relevance_scores(
    query: SignalQuery,
    entries: Sequence[MemoryEntry],
    now: datetime,
    signals: Sequence[MemorySignal],
) -> list[float]:
    """Fuse the content signals with importance, confidence and hotness.

    Content relevance (lexical + BM25 + entity, see :mod:`signals`) gates the
    score: an entry none of the signals matched stays at 0.0 regardless of its
    metadata, which is what keeps unrelated memories out of the results. Above
    the gate, metadata terms break ties and the OpenViking hotness decay
    (access frequency x recency) replaces the old recency/access pair. Finally
    a temporal intent on the query scales down entries of another temporal
    layer (a future plan whose event time has passed counts as a past event).
    """
    relevance = content_scores(query, entries, signals)
    scores: list[float] = []
    for entry, content_relevance in zip(entries, relevance, strict=True):
        if content_relevance <= 0:
            scores.append(0.0)
            continue
        anchor = _parse_timestamp(entry.updated_at) or _parse_timestamp(entry.created_at) or now
        hotness = hotness_score(
            max(entry.access_count, 0), anchor, now, half_life_days=HOTNESS_HALF_LIFE_DAYS
        )
        total = (
            content_relevance
            + WEIGHT_IMPORTANCE * entry.importance
            + WEIGHT_CONFIDENCE * entry.confidence
            + WEIGHT_HOTNESS * hotness
        )
        event_at = _parse_timestamp(entry.event_at) if entry.event_at else None
        layer = effective_temporal_kind(entry.temporal_kind, event_at, now)
        scores.append(total * temporal_multiplier(query.intent, layer))
    return scores
