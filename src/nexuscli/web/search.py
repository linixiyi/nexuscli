from __future__ import annotations

import html
import re
from dataclasses import dataclass
from urllib.parse import parse_qs, quote, unquote, urlparse

import httpx


@dataclass(slots=True)
class SearchResult:
    title: str
    url: str
    snippet: str


class SearchError(RuntimeError):
    """A search failure with a non-empty, human-readable reason.

    httpx exceptions often stringify to the empty string (``ConnectError()``
    is the worst case), so ``Search error: {exc}`` reached the model as
    ``Search error: `` with no hint at all. Every failure raised here names
    the exception type, the query, and whatever message the transport gave.
    """


async def search_web(query: str, max_results: int = 5, timeout: float = 15.0) -> list[SearchResult]:
    if not query.strip():
        raise SearchError("search query is empty")
    url = f"https://duckduckgo.com/html/?q={quote(query)}"
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            response = await client.get(url, headers={"user-agent": "NexusCLI-Python/0.1.0"})
            response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise SearchError(
            f"DuckDuckGo returned HTTP {exc.response.status_code} for query {query!r}"
        ) from exc
    except httpx.HTTPError as exc:
        detail = str(exc).strip() or repr(exc)
        raise SearchError(
            f"DuckDuckGo request failed ({type(exc).__name__}) for query {query!r}: {detail}"
        ) from exc
    return _parse_duckduckgo(response.text)[:max_results]


def _parse_duckduckgo(raw_html: str) -> list[SearchResult]:
    results: list[SearchResult] = []
    pattern = re.compile(
        r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>([\s\S]*?)</a>'
        r"[\s\S]*?"
        r'<a[^>]+class="result__snippet"[^>]*>([\s\S]*?)</a>',
        re.I,
    )
    for match in pattern.finditer(raw_html):
        results.append(
            SearchResult(
                title=_clean(match.group(2)),
                url=_normalize_duckduckgo_url(html.unescape(match.group(1))),
                snippet=_clean(match.group(3)),
            )
        )
    return results


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", value))).strip()


def _normalize_duckduckgo_url(url: str) -> str:
    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    if "uddg" in params and params["uddg"]:
        return unquote(params["uddg"][0])
    return url
