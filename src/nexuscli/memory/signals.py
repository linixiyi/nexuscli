"""Multi-signal memory recall: pluggable relevance signals plus the shared heuristics.

The recall path in :mod:`nexuscli.memory.manager` builds one :class:`SignalQuery`
per request, asks every :class:`MemorySignal` for a batch of ``[0, 1]`` scores and
fuses them with fixed weights. Three signals ship by default:

* :class:`LexicalSignal` - the pre-existing coverage + substring heuristic;
* :class:`BM25Signal` - BM25 computed in memory over the recall batch (document
  frequency / average length are recomputed per call, so the signal never touches
  the code-index SQLite file);
* :class:`EntitySignal` - overlap between query entities and the entities stored
  with each memory (explicit ``entities`` from the save tool, else the lightweight
  extractor below).

:class:`VectorSignal` is the dense-similarity slot. Nothing in the repository
embeds text, so it is *absent* from :func:`default_signals` and only participates
when a caller injects it with an embedder.

Weights are code constants on purpose (anchored by tests, not configuration).
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from nexuscli.rag.code_index import bm25_idf, bm25_score

if TYPE_CHECKING:
    from nexuscli.memory.manager import MemoryEntry

# --- temporal layer -----------------------------------------------------------

CURRENT_STATE = "current_state"
PAST_EVENT = "past_event"
FUTURE_PLAN = "future_plan"
# Display / schema order; TEMPORAL_KINDS is the membership set.
TEMPORAL_KIND_ORDER: tuple[str, ...] = (CURRENT_STATE, PAST_EVENT, FUTURE_PLAN)
TEMPORAL_KINDS: frozenset[str] = frozenset(TEMPORAL_KIND_ORDER)

# Memory ``kind`` -> default temporal layer when neither an explicit value nor a
# content marker says otherwise (also the backfill mapping for pre-existing rows).
_KIND_TO_TEMPORAL: dict[str, str] = {
    "fact": PAST_EVENT,
    "preference": CURRENT_STATE,
    "constraint": CURRENT_STATE,
    "decision": CURRENT_STATE,
    "correction": CURRENT_STATE,
}
DEFAULT_TEMPORAL_KIND = CURRENT_STATE

# --- fusion weights (fixed; see tests/test_memory_signals.py) -----------------

WEIGHT_LEXICAL = 0.40
WEIGHT_BM25 = 0.25
WEIGHT_ENTITY = 0.15
WEIGHT_VECTOR = 0.20
# Content signals share this budget: their weights are rescaled so the active
# set always sums to CONTENT_BUDGET, which keeps a lexical-only configuration on
# the same scale as the default three-signal one.
CONTENT_BUDGET = 0.80
WEIGHT_IMPORTANCE = 0.08
WEIGHT_CONFIDENCE = 0.06
WEIGHT_HOTNESS = 0.06
HOTNESS_HALF_LIFE_DAYS = 30.0
# Applied to the fused score when the query carries a temporal intent and the
# entry sits in a different temporal layer.
TEMPORAL_MISMATCH_MULTIPLIER = 0.85

# --- tokenisation (shared with the manager) -----------------------------------

_WORD_RE = re.compile(r"[a-z0-9_]+")
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")


def normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(normalized.split())


def lexical_features(value: str) -> set[str]:
    """Words plus CJK characters, bigrams and whole runs; the lexical signal's unit."""
    normalized = normalize_text(value)
    features = set(_WORD_RE.findall(normalized))
    for sequence in _CJK_RE.findall(normalized):
        characters = list(sequence)
        features.update(characters)
        features.update(
            "".join(characters[index : index + 2]) for index in range(len(characters) - 1)
        )
        if len(sequence) > 1:
            features.add(sequence)
    return features


def bm25_terms(value: str) -> tuple[str, ...]:
    """BM25 query terms: words plus CJK bigrams (a lone CJK character stands alone)."""
    normalized = normalize_text(value)
    terms = set(_WORD_RE.findall(normalized))
    for sequence in _CJK_RE.findall(normalized):
        if len(sequence) == 1:
            terms.add(sequence)
            continue
        terms.update(sequence[index : index + 2] for index in range(len(sequence) - 1))
    return tuple(sorted(terms))


# --- entity heuristics --------------------------------------------------------

_MAX_ENTITY_LENGTH = 64
MAX_ENTITIES = 32
_QUOTED_RE = re.compile(
    r'"([^"\n]{1,64})"|“([^”\n]{1,64})”|「([^」\n]{1,64})」|《([^》\n]{1,64})》|`([^`\n]{1,64})`'
)
_CAPITALIZED_RUN_RE = re.compile(
    r"\b[A-Z][A-Za-z0-9]+(?:[-_.][A-Za-z0-9]+)*(?: [A-Z][A-Za-z0-9]+(?:[-_.][A-Za-z0-9]+)*)*"
)
_IDENTIFIER_RE = re.compile(r"\b[A-Za-z0-9]+(?:[._/-][A-Za-z0-9]+)+\b")
_ALNUM_MIX_RE = re.compile(r"\b(?=[A-Za-z0-9]*[A-Za-z])(?=[A-Za-z0-9]*\d)[A-Za-z0-9]+\b")
_CAMEL_CASE_RE = re.compile(r"\b[a-z]+[A-Z][A-Za-z0-9]*\b")
_HANDLE_RE = re.compile(r"(?<![A-Za-z0-9])[@#][A-Za-z0-9_]{2,}")
# Capitalised sentence starters and generic nouns that are not entities.
_CAPITALIZED_STOPWORDS: frozenset[str] = frozenset(
    {
        "a", "an", "the", "this", "that", "these", "those", "it", "its", "we", "our",
        "you", "your", "they", "their", "he", "she", "i", "my", "me", "us", "and", "or",
        "but", "if", "in", "on", "at", "to", "for", "of", "by", "with", "from", "as", "is",
        "are", "was", "were", "be", "been", "do", "does", "did", "not", "no", "yes",
        "always", "never", "use", "uses", "used", "user", "users", "project", "please",
        "remember", "note", "when", "where", "what", "which", "who", "how", "why", "then",
        "so", "also", "all", "any", "some", "every", "each", "new", "old", "first", "last",
        "next", "previous", "current", "now", "today", "tomorrow", "yesterday", "prefer",
        "prefers", "run", "runs", "keep", "avoid", "must", "should", "can", "will",
    }
)  # fmt: skip


def normalize_entity(value: str) -> str | None:
    """Casefold + NFKC + whitespace collapse; ``None`` when nothing usable remains."""
    normalized = normalize_text(str(value or "")).strip(" \t\"'“”「」《》`.,;:!?")
    if len(normalized) < 2 or len(normalized) > _MAX_ENTITY_LENGTH:
        return None
    return normalized


def normalize_entities(values: Iterable[str] | None) -> list[str]:
    """Validate an explicit entity list: strings only, normalised, de-duplicated, sorted."""
    if values is None:
        return []
    if isinstance(values, str | bytes):
        raise ValueError("memory entities must be a list of strings")
    normalized: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            raise ValueError("memory entities must be a list of strings")
        entity = normalize_entity(value)
        if entity is not None:
            normalized.add(entity)
    return sorted(normalized)[:MAX_ENTITIES]


def extract_entities(text: str) -> list[str]:
    """Lightweight, dependency-free entity extraction used when no explicit list is given.

    Patterns: quoted spans (ASCII, CJK and backtick quotes), capitalised word runs
    (``Volcano Engine`` yields the run and each word), identifier-like tokens
    (``pyproject.toml``, ``BLUE-OTTER-77``, ``2026-10-27``), letter/digit mixes
    (``py311``), camelCase and ``@handle`` / ``#tag`` tokens.
    """
    source = str(text or "")
    candidates: set[str] = set()
    for match in _QUOTED_RE.finditer(source):
        quoted = next((group for group in match.groups() if group), "")
        candidates.add(quoted)
    for match in _CAPITALIZED_RUN_RE.finditer(source):
        words = [
            word
            for word in match.group(0).split(" ")
            if word.casefold() not in _CAPITALIZED_STOPWORDS
        ]
        candidates.update(words)
        if len(words) > 1:
            candidates.add(" ".join(words))
    for pattern in (_IDENTIFIER_RE, _ALNUM_MIX_RE, _CAMEL_CASE_RE, _HANDLE_RE):
        candidates.update(match.group(0) for match in pattern.finditer(source))
    entities: set[str] = set()
    for candidate in candidates:
        entity = normalize_entity(candidate)
        if entity is not None and entity not in _CAPITALIZED_STOPWORDS:
            entities.add(entity)
    return sorted(entities)[:MAX_ENTITIES]


# --- temporal intent -----------------------------------------------------------


def _marker_regex(markers: Sequence[str]) -> re.Pattern[str]:
    parts = []
    for marker in sorted(markers, key=len, reverse=True):
        escaped = re.escape(marker)
        if _WORD_RE.fullmatch(marker.replace(" ", "")):
            parts.append(rf"(?<![a-z0-9]){escaped}(?![a-z0-9])")
        else:
            parts.append(escaped)
    return re.compile("|".join(parts))


_FUTURE_MARKERS = _marker_regex(
    (
        "will", "going to", "plan", "plans", "planned", "planning", "upcoming", "tomorrow",
        "soon", "todo", "roadmap", "scheduled", "next week", "next month", "next sprint",
        "next release", "next version", "in the future", "eventually", "later",
        "计划", "打算", "将要", "将会", "准备", "待办", "未来", "以后", "之后", "接下来",
        "后续", "下周", "下个月", "明天", "即将", "预计",
    )
)  # fmt: skip
_PAST_MARKERS = _marker_regex(
    (
        "did", "happened", "previously", "ago", "yesterday", "earlier", "used to",
        "last week", "last month", "last time", "last sprint", "history", "historically",
        "formerly", "was", "were",
        "曾经", "之前", "以前", "过去", "上次", "上周", "上个月", "昨天", "历史", "当时", "原来",
    )
)  # fmt: skip
_CURRENT_MARKERS = _marker_regex(
    (
        "currently", "now", "nowadays", "at the moment", "these days", "right now", "still",
        "default", "convention", "always", "usually", "normally", "prefer", "prefers",
        "preference",
        "现在", "目前", "当前", "如今", "一直", "默认", "约定", "偏好", "习惯",
    )
)  # fmt: skip


def detect_temporal_intent(text: str) -> str | None:
    """Keyword-table temporal intent of a query or memory text (no LLM involved).

    Past/future markers are specific and win over the generic current-state
    markers; a text carrying both past and future markers is ambiguous and
    yields ``None`` so recall applies no temporal weighting at all.
    """
    normalized = normalize_text(text)
    if not normalized:
        return None
    future = bool(_FUTURE_MARKERS.search(normalized))
    past = bool(_PAST_MARKERS.search(normalized))
    if future and not past:
        return FUTURE_PLAN
    if past and not future:
        return PAST_EVENT
    if future and past:
        return None
    if _CURRENT_MARKERS.search(normalized):
        return CURRENT_STATE
    return None


def validate_temporal_kind(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip().casefold()
    if not normalized:
        return None
    if normalized not in TEMPORAL_KINDS:
        allowed = ", ".join(sorted(TEMPORAL_KINDS))
        raise ValueError(f"memory temporal_kind must be one of: {allowed}")
    return normalized


def temporal_kind_for_kind(kind: str) -> str:
    return _KIND_TO_TEMPORAL.get(str(kind or "").strip().casefold(), DEFAULT_TEMPORAL_KIND)


def derive_temporal_kind(kind: str, content: str, explicit: str | None = None) -> str:
    """Explicit value > content markers > ``kind`` mapping."""
    validated = validate_temporal_kind(explicit)
    if validated is not None:
        return validated
    detected = detect_temporal_intent(content)
    if detected is not None:
        return detected
    return temporal_kind_for_kind(kind)


def effective_temporal_kind(temporal_kind: str, event_at: datetime | None, now: datetime) -> str:
    """A future plan whose ``event_at`` has passed is matched as a past event."""
    if temporal_kind == FUTURE_PLAN and event_at is not None and event_at <= now:
        return PAST_EVENT
    return temporal_kind if temporal_kind in TEMPORAL_KINDS else DEFAULT_TEMPORAL_KIND


def temporal_multiplier(intent: str | None, temporal_kind: str) -> float:
    if intent is None or intent == temporal_kind:
        return 1.0
    return TEMPORAL_MISMATCH_MULTIPLIER


# --- query + signals ----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SignalQuery:
    """Everything a signal may need about one recall request, computed once."""

    text: str
    normalized: str
    features: frozenset[str]
    terms: tuple[str, ...]
    entities: frozenset[str]
    intent: str | None

    @classmethod
    def build(
        cls,
        text: str,
        *,
        entities: Iterable[str] | None = None,
        temporal_kind: str | None = None,
    ) -> SignalQuery:
        cleaned = " ".join(str(text or "").split())
        query_entities = set(extract_entities(cleaned))
        query_entities.update(normalize_entities(entities))
        intent = validate_temporal_kind(temporal_kind)
        if intent is None:
            intent = detect_temporal_intent(cleaned)
        return cls(
            text=cleaned,
            normalized=normalize_text(cleaned),
            features=frozenset(lexical_features(cleaned)),
            terms=bm25_terms(cleaned),
            entities=frozenset(query_entities),
            intent=intent,
        )


@runtime_checkable
class MemorySignal(Protocol):
    """One relevance signal; ``score`` returns a ``[0, 1]`` value per entry, in order."""

    name: str
    weight: float

    def score(self, query: SignalQuery, entries: Sequence[MemoryEntry]) -> list[float]: ...


class LexicalSignal:
    """Query-feature coverage plus a whole-query substring bonus (the original scorer)."""

    name = "lexical"
    weight = WEIGHT_LEXICAL

    def score(self, query: SignalQuery, entries: Sequence[MemoryEntry]) -> list[float]:
        if not query.features:
            return [0.0] * len(entries)
        scores: list[float] = []
        for entry in entries:
            normalized_content = normalize_text(entry.content)
            overlap = query.features & lexical_features(normalized_content)
            substring_match = query.normalized in normalized_content
            if not overlap and not substring_match:
                scores.append(0.0)
                continue
            coverage = len(overlap) / len(query.features)
            scores.append(min(1.0, 0.8 * coverage + (0.2 if substring_match else 0.0)))
        return scores


class BM25Signal:
    """BM25 over the recall batch, normalised by the idf mass of the whole query.

    Statistics (df, avgdl) are recomputed from ``entries`` on every call; the
    signal shares only the pure scoring function with the code index and never
    reads its SQLite file. Dividing by the summed idf of *all* query terms turns
    the unbounded score into an idf-weighted coverage in ``[0, 1]``: rare terms
    the entry matches count for more, terms nobody matches still cost coverage.
    """

    name = "bm25"
    weight = WEIGHT_BM25

    def score(self, query: SignalQuery, entries: Sequence[MemoryEntry]) -> list[float]:
        terms = list(query.terms)
        if not terms or not entries:
            return [0.0] * len(entries)
        documents = [normalize_text(entry.content) for entry in entries]
        corpus_size = len(documents)
        avgdl = sum(len(document) for document in documents) / corpus_size
        if avgdl <= 0:
            return [0.0] * len(entries)
        df = {term: sum(1 for document in documents if term in document) for term in terms}
        idf_total = sum(bm25_idf(df[term], corpus_size) for term in terms)
        if idf_total <= 0:
            return [0.0] * len(entries)
        return [
            min(1.0, bm25_score(terms, df, corpus_size, avgdl, len(document), document) / idf_total)
            for document in documents
        ]


class EntitySignal:
    """Share of query entities present in the entry's stored entity list."""

    name = "entity"
    weight = WEIGHT_ENTITY

    def score(self, query: SignalQuery, entries: Sequence[MemoryEntry]) -> list[float]:
        if not query.entities:
            return [0.0] * len(entries)
        scores: list[float] = []
        for entry in entries:
            entry_entities = set(entry.entities)
            if not entry_entities:
                scores.append(0.0)
                continue
            scores.append(len(query.entities & entry_entities) / len(query.entities))
        return scores


Embedder = Callable[[str], Sequence[float]]


class VectorSignal:
    """Dense-similarity slot: cosine between embedder(query) and embedder(content).

    Not part of :func:`default_signals`; construct it with an embedder and pass
    it in ``MemoryManager(signals=...)`` to opt in. No embedding dependency is
    introduced here.
    """

    name = "vector"
    weight = WEIGHT_VECTOR

    def __init__(self, embedder: Embedder):
        self._embed = embedder

    def score(self, query: SignalQuery, entries: Sequence[MemoryEntry]) -> list[float]:
        if not entries:
            return []
        query_vector = list(self._embed(query.text))
        return [
            max(0.0, _cosine(query_vector, list(self._embed(entry.content)))) for entry in entries
        ]


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if not left or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    norm = math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(b * b for b in right))
    return dot / norm if norm > 0 else 0.0


def default_signals() -> list[MemorySignal]:
    """The shipped signal set: lexical, BM25 and entity. The vector slot stays empty."""
    return [LexicalSignal(), BM25Signal(), EntitySignal()]


def content_scores(
    query: SignalQuery,
    entries: Sequence[MemoryEntry],
    signals: Sequence[MemorySignal],
) -> list[float]:
    """Weighted content relevance in ``[0, CONTENT_BUDGET]`` per entry.

    Active signal weights are rescaled to sum to :data:`CONTENT_BUDGET`, so the
    scale does not depend on how many signals are plugged in. Each signal must
    return exactly one score per entry; anything else is a programming error.
    """
    if not entries:
        return []
    active = [signal for signal in signals if signal.weight > 0]
    weight_total = sum(signal.weight for signal in active)
    if not active or weight_total <= 0:
        return [0.0] * len(entries)
    scale = CONTENT_BUDGET / weight_total
    totals = [0.0] * len(entries)
    for signal in active:
        scores = signal.score(query, entries)
        if len(scores) != len(entries):
            raise ValueError(
                f"memory signal {signal.name!r} returned {len(scores)} scores for "
                f"{len(entries)} entries"
            )
        for index, value in enumerate(scores):
            totals[index] += signal.weight * min(max(float(value), 0.0), 1.0)
    return [total * scale for total in totals]


__all__ = [
    "CONTENT_BUDGET",
    "CURRENT_STATE",
    "DEFAULT_TEMPORAL_KIND",
    "FUTURE_PLAN",
    "HOTNESS_HALF_LIFE_DAYS",
    "PAST_EVENT",
    "TEMPORAL_KIND_ORDER",
    "TEMPORAL_KINDS",
    "TEMPORAL_MISMATCH_MULTIPLIER",
    "WEIGHT_BM25",
    "WEIGHT_CONFIDENCE",
    "WEIGHT_ENTITY",
    "WEIGHT_HOTNESS",
    "WEIGHT_IMPORTANCE",
    "WEIGHT_LEXICAL",
    "WEIGHT_VECTOR",
    "BM25Signal",
    "Embedder",
    "EntitySignal",
    "LexicalSignal",
    "MemorySignal",
    "SignalQuery",
    "VectorSignal",
    "bm25_terms",
    "content_scores",
    "default_signals",
    "derive_temporal_kind",
    "detect_temporal_intent",
    "effective_temporal_kind",
    "extract_entities",
    "lexical_features",
    "normalize_entities",
    "normalize_entity",
    "normalize_text",
    "temporal_kind_for_kind",
    "temporal_multiplier",
    "validate_temporal_kind",
]
