from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

TEXT_SUFFIXES = {
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".java",
    ".kt",
    ".go",
    ".rs",
    ".md",
    ".toml",
    ".yaml",
    ".yml",
    ".json",
    ".xml",
    ".html",
    ".css",
    ".scss",
    ".sql",
    ".sh",
}

SKIP_DIRS = {".git", ".venv", "node_modules", "dist", "build", "target", "__pycache__"}

# BM25 relevance ranking (standard defaults; no third-party dependencies).
BM25_K1 = 1.5
BM25_B = 0.75
# Hard cap on candidate rows pulled from SQLite before AND filtering + scoring.
CANDIDATE_WINDOW = 500


@dataclass(slots=True)
class CodeSearchResult:
    path: str
    line: int
    snippet: str
    score: float = 0.0


@dataclass(slots=True)
class SearchStats:
    terms: list[str] = field(default_factory=list)
    candidate_rows: int = 0
    matched_rows: int = 0
    returned: int = 0


def format_hit_stats(stats: SearchStats) -> str:
    """One-line hit summary; appended after the `path:line: content` result lines."""
    terms = ", ".join(stats.terms) if stats.terms else "(none)"
    return (
        f"[hits] matched {stats.matched_rows} line(s) across "
        f"{stats.candidate_rows} indexed candidate(s); showing top {stats.returned}; "
        f"terms: {terms}"
    )


def bm25_idf(df: int, corpus_size: int) -> float:
    """Inverse document frequency for one term: ``log(1 + (N - df + 0.5) / (df + 0.5))``."""
    return math.log(1 + (corpus_size - df + 0.5) / (df + 0.5))


def bm25_score(
    terms: list[str],
    df: dict[str, int],
    corpus_size: int,
    avgdl: float,
    doc_length: int,
    lowered: str,
) -> float:
    """BM25 score of one document against ``terms`` (pure function, no index access).

    Term frequency is the substring count of each term in ``lowered``; ``df``
    maps every term to its document frequency over a corpus of ``corpus_size``
    documents with average length ``avgdl``. Shared by the code index (which
    scores its SQLite candidate window) and the memory recall path (which
    computes the same statistics in memory over one recall batch).
    """
    score = 0.0
    for term in terms:
        tf = lowered.count(term)
        if tf == 0:
            continue
        idf = bm25_idf(df[term], corpus_size)
        score += (
            idf * (tf * (BM25_K1 + 1)) / (tf + BM25_K1 * (1 - BM25_B + BM25_B * doc_length / avgdl))
        )
    return score


class CodeIndex:
    def __init__(self, root: str | Path, db_path: str | Path | None = None):
        self.root = Path(root).resolve()
        self.db_path = (
            Path(db_path).expanduser()
            if db_path
            else self.root / ".nexuscli" / "code_index.sqlite3"
        )
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def rebuild(self, path: str | Path | None = None) -> int:
        base = self._resolve(path or self.root)
        files = [base] if base.is_file() else list(self._iter_files(base))
        with self._connect() as conn:
            conn.execute("delete from code_chunks where root = ?", (str(self.root),))
            count = 0
            for file_path in files:
                rel = str(file_path.relative_to(self.root))
                try:
                    lines = file_path.read_text(encoding="utf-8", errors="ignore").splitlines()
                except OSError:
                    continue
                for line_number, line in enumerate(lines, start=1):
                    stripped = line.strip()
                    if not stripped:
                        continue
                    conn.execute(
                        """
                        insert into code_chunks(root, path, line, content)
                        values (?, ?, ?, ?)
                        """,
                        (str(self.root), rel, line_number, stripped),
                    )
                    count += 1
            return count

    def search(self, query: str, limit: int = 20) -> list[CodeSearchResult]:
        results, _ = self.search_with_stats(query, limit)
        return results

    def search_with_stats(
        self, query: str, limit: int = 20
    ) -> tuple[list[CodeSearchResult], SearchStats]:
        """AND-match rows for every query term, rank the matches with BM25, return top-N.

        The candidate window (first-term LIKE prefilter, capped at CANDIDATE_WINDOW
        rows ordered by path/line) doubles as the BM25 corpus, so scoring is
        deterministic for a given index state and query term order. Term frequency
        is the case-insensitive substring count, which keeps the existing matching
        semantics (substring, whitespace-split terms) and works for CJK terms.
        """
        terms = [term.lower() for term in query.split() if term.strip()]
        stats = SearchStats(terms=terms)
        if not terms:
            return [], stats
        rows: list[tuple[str, int, str]]
        with self._connect() as conn:
            like = f"%{terms[0]}%"
            rows = conn.execute(
                """
                select path, line, content
                from code_chunks
                where root = ? and lower(content) like ?
                order by path, line
                limit ?
                """,
                (str(self.root), like, CANDIDATE_WINDOW),
            ).fetchall()
        stats.candidate_rows = len(rows)

        docs: list[tuple[str, int, str, str]] = []
        for path, line, content in rows:
            lowered = content.lower()
            if all(term in lowered for term in terms):
                docs.append((path, int(line), content, lowered))
        stats.matched_rows = len(docs)
        if not docs:
            return [], stats

        corpus_size = len(docs)
        avgdl = sum(len(lowered) for _, _, _, lowered in docs) / corpus_size
        df = {term: sum(1 for _, _, _, lowered in docs if term in lowered) for term in terms}

        scored: list[CodeSearchResult] = []
        for path, line, content, lowered in docs:
            score = bm25_score(terms, df, corpus_size, avgdl, len(lowered), lowered)
            scored.append(CodeSearchResult(path, line, content, score))
        scored.sort(key=lambda item: (-item.score, item.path, item.line))

        top = scored[:limit] if limit > 0 else []
        stats.returned = len(top)
        return top, stats

    def _iter_files(self, base: Path):
        for path in base.rglob("*"):
            if any(part in SKIP_DIRS for part in path.parts):
                continue
            if path.is_file() and path.suffix.lower() in TEXT_SUFFIXES:
                yield path

    def _resolve(self, value: str | Path) -> Path:
        path = Path(value)
        if not path.is_absolute():
            path = self.root / path
        resolved = path.resolve()
        resolved.relative_to(self.root)
        return resolved

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                create table if not exists code_chunks (
                    id integer primary key autoincrement,
                    root text not null,
                    path text not null,
                    line integer not null,
                    content text not null
                )
                """
            )
            conn.execute(
                "create index if not exists idx_code_chunks_root_path on code_chunks(root, path)"
            )
            conn.execute(
                "create index if not exists idx_code_chunks_root_content "
                "on code_chunks(root, content)"
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)
