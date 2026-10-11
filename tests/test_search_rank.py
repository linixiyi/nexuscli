"""Tests for BM25 relevance ranking in the code index (search-rank lane)."""

from __future__ import annotations

import math

from nexuscli.rag import CodeIndex, SearchStats, format_hit_stats
from nexuscli.rag.code_index import BM25_B, BM25_K1, CodeSearchResult


def _make_index(tmp_path, files: dict[str, str]) -> CodeIndex:
    for name, content in files.items():
        (tmp_path / name).write_text(content, encoding="utf-8")
    index = CodeIndex(tmp_path)
    index.rebuild()
    return index


def test_bm25_ranks_higher_term_frequency_first(tmp_path):
    index = _make_index(
        tmp_path,
        {
            "a.py": "alpha alpha alpha tail\nbeta filler\n",
            "b.py": "alpha once\n",
        },
    )
    results = index.search("alpha")
    assert len(results) == 2
    assert results[0].path == "a.py"
    assert results[0].line == 1
    assert results[0].score > results[1].score
    assert results[1].path == "b.py"


def test_bm25_prefers_shorter_line_at_equal_term_frequency(tmp_path):
    index = _make_index(
        tmp_path,
        {
            "short.py": "beta\n",
            "long.py": "beta plus quite a lot of padding words here\n",
        },
    )
    results = index.search("beta")
    assert [r.path for r in results] == ["short.py", "long.py"]
    assert results[0].score > results[1].score


def test_bm25_score_matches_reference_formula(tmp_path):
    index = _make_index(
        tmp_path,
        {"a.py": "alpha alpha tail\n", "b.py": "alpha once\n", "c.py": "unrelated\n"},
    )
    results = index.search("alpha")
    # Reference computation against the two AND-matched docs (corpus size 2).
    docs = {"a.py": "alpha alpha tail", "b.py": "alpha once"}
    corpus_size, avgdl = 2, (len(docs["a.py"]) + len(docs["b.py"])) / 2
    df = 2
    idf = math.log(1 + (corpus_size - df + 0.5) / (df + 0.5))
    expected = {}
    for path, lowered in docs.items():
        tf = lowered.count("alpha")
        expected[path] = (
            idf
            * (tf * (BM25_K1 + 1))
            / (tf + BM25_K1 * (1 - BM25_B + BM25_B * len(lowered) / avgdl))
        )
    for result in results:
        assert result.score == expected[result.path]


def test_and_semantics_preserved(tmp_path):
    index = _make_index(
        tmp_path,
        {"a.py": "alpha beta line\nalpha solo line\nbeta solo line\n"},
    )
    results = index.search("alpha beta")
    assert [(r.path, r.line) for r in results] == [("a.py", 1)]
    assert index.search("alpha missing_word") == []


def test_no_hits_behavior_unchanged(tmp_path):
    index = _make_index(tmp_path, {"a.py": "alpha line\n"})
    assert index.search("zzzz") == []
    results, stats = index.search_with_stats("alpha zzzz")
    assert results == []
    assert stats.matched_rows == 0
    assert stats.returned == 0
    assert stats.candidate_rows >= 0
    assert index.search("   ") == []


def test_top_n_truncation(tmp_path):
    body = "\n".join(f"alpha hit {i}" for i in range(5)) + "\n"
    index = _make_index(tmp_path, {"many.py": body})
    full, full_stats = index.search_with_stats("alpha")
    truncated, trunc_stats = index.search_with_stats("alpha", limit=2)
    assert len(full) == 5
    assert full_stats.matched_rows == 5
    assert full_stats.returned == 5
    assert len(truncated) == 2
    assert trunc_stats.matched_rows == 5
    assert trunc_stats.returned == 2
    assert [(r.path, r.line, r.score) for r in truncated] == [
        (r.path, r.line, r.score) for r in full[:2]
    ]


def test_cjk_terms_follow_existing_substring_tokenization(tmp_path):
    index = _make_index(
        tmp_path,
        {"cjk.py": "连接 数据库 数据库 数据库\n数据库配置\n其他内容\n"},
    )
    results = index.search("数据库")
    assert [(r.path, r.line) for r in results] == [("cjk.py", 1), ("cjk.py", 2)]
    # Line 1 contains 数据库 three times, line 2 once -> higher tf ranks first.
    assert results[0].score > results[1].score
    # AND matching still required across CJK terms split on whitespace.
    multi = index.search("连接 数据库")
    assert [(r.path, r.line) for r in multi] == [("cjk.py", 1)]
    assert index.search("连接 配置") == []


def test_scores_are_deterministic(tmp_path):
    index = _make_index(tmp_path, {"a.py": "alpha alpha tail\n", "b.py": "alpha once\n"})
    first = index.search("alpha")
    second = index.search("alpha")
    assert [(r.path, r.line, r.score) for r in first] == [(r.path, r.line, r.score) for r in second]


def test_search_wrapper_keeps_public_contract(tmp_path):
    index = _make_index(tmp_path, {"a.py": "alpha alpha tail\n", "b.py": "alpha once\n"})
    results = index.search("alpha")
    assert isinstance(results, list)
    assert all(isinstance(r, CodeSearchResult) for r in results)
    assert results[0].snippet.startswith("alpha")


def test_format_hit_stats_single_line(tmp_path):
    index = _make_index(tmp_path, {"a.py": "alpha line\n"})
    _, stats = index.search_with_stats("alpha")
    assert isinstance(stats, SearchStats)
    line = format_hit_stats(stats)
    assert "\n" not in line
    assert "matched 1" in line
    assert "top 1" in line
    assert "alpha" in line
