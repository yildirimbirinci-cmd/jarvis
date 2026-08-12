from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

import requests

from artmach_assistant.core.research_providers.base import (
    ProviderSearchResult,
    SearchProvider,
)
from artmach_assistant.core.research_providers.duckduckgo_html import (
    DuckDuckGoHtmlProvider,
)
from artmach_assistant.core.research_providers.duckduckgo_lite import (
    DuckDuckGoLiteProvider,
)
from artmach_assistant.core.research_providers.bing_html import (
    BingHtmlProvider,
)


@dataclass(frozen=True, slots=True)
class ProviderFailure:
    provider: str
    error: str


_TECHNICAL_QUERY_MARKERS = (
    "benchmark",
    "cprofile",
    "decorator",
    "diagnostic",
    "latency",
    "orchestration",
    "performance",
    "profiling",
    "runtime",
    "task execution",
    "wrapper",
)

_GENERIC_PYTHON_PATHS = {
    "",
    "/",
    "/downloads",
    "/downloads/",
}


def _is_python_technical_query(query: str) -> bool:
    lowered = str(query or "").casefold()
    return "python" in lowered and any(
        marker in lowered
        for marker in _TECHNICAL_QUERY_MARKERS
    )


def _query_variants(query: str) -> tuple[str, ...]:
    original = str(query or "").strip()
    if not original:
        return ()

    if not _is_python_technical_query(original):
        return (original,)

    lowered = original.casefold()
    rows = [
        original,
        f"site:docs.python.org/3/library {original}",
    ]

    if any(token in lowered for token in ("cprofile", "profil")):
        rows.append(
            "site:docs.python.org/3/library/profile.html "
            "cProfile profile pstats profiling"
        )

    if any(token in lowered for token in ("latency", "performance", "timing")):
        rows.append(
            "site:docs.python.org/3/library/time.html "
            "time.perf_counter performance timing"
        )

    if any(token in lowered for token in ("concurrent", "orchestration", "task execution")):
        rows.append(
            "site:docs.python.org/3/library/concurrent.futures.html "
            "Executor Future task execution"
        )

    if any(token in lowered for token in ("decorator", "wrapper")):
        rows.append(
            "site:docs.python.org/3/library/functools.html "
            "functools.wraps decorator wrapper"
        )

    return tuple(dict.fromkeys(rows))


def _official_seed_results(query: str) -> tuple[ProviderSearchResult, ...]:
    if not _is_python_technical_query(query):
        return ()

    lowered = query.casefold()
    rows: list[ProviderSearchResult] = []

    if any(token in lowered for token in ("cprofile", "profil")):
        rows.append(
            ProviderSearchResult(
                title="The Python Profilers",
                url="https://docs.python.org/3/library/profile.html",
                snippet=(
                    "Official Python documentation for profile, cProfile, "
                    "and pstats performance profiling tools."
                ),
            )
        )

    if any(token in lowered for token in ("latency", "performance", "timing")):
        rows.append(
            ProviderSearchResult(
                title="time - Time access and conversions",
                url="https://docs.python.org/3/library/time.html",
                snippet=(
                    "Official Python documentation for high-resolution "
                    "performance counters and elapsed-time measurement."
                ),
            )
        )

    if any(token in lowered for token in ("concurrent", "orchestration", "task execution")):
        rows.append(
            ProviderSearchResult(
                title="concurrent.futures - Launching parallel tasks",
                url="https://docs.python.org/3/library/concurrent.futures.html",
                snippet=(
                    "Official Python documentation for Executor, Future, "
                    "and concurrent task execution."
                ),
            )
        )

    if any(token in lowered for token in ("decorator", "wrapper")):
        rows.append(
            ProviderSearchResult(
                title="functools - Higher-order functions",
                url="https://docs.python.org/3/library/functools.html",
                snippet=(
                    "Official Python documentation for functools.wraps "
                    "and behavior-preserving decorator wrappers."
                ),
            )
        )

    return tuple(rows)


def _result_key(row: ProviderSearchResult) -> str:
    try:
        parsed = urlparse(str(row.url or "").strip())
    except ValueError:
        return ""

    host = str(parsed.hostname or "").casefold()
    if not host:
        return ""

    path = parsed.path.rstrip("/") or "/"
    return f"{host}{path.casefold()}"


def _is_strong_result(
    row: ProviderSearchResult,
    *,
    query: str,
) -> bool:
    try:
        parsed = urlparse(str(row.url or "").strip())
    except ValueError:
        return False

    host = str(parsed.hostname or "").casefold()
    path = parsed.path or "/"

    technical_query = _is_python_technical_query(query)
    if technical_query:
        if host in {"python.org", "www.python.org"} and path in _GENERIC_PYTHON_PATHS:
            return False
        if host == "docs.python.org" or host.endswith(".docs.python.org"):
            return path not in {"", "/", "/3", "/3/"}
    else:
        combined = " ".join(
            (str(row.title or ""), str(row.snippet or ""))
        ).casefold()
        query_tokens = {
            token.strip(".,:;!?()[]{}\"'")
            for token in query.casefold().split()
            if len(token.strip(".,:;!?()[]{}\"'")) >= 3
        }
        result_tokens = {
            token.strip(".,:;!?()[]{}\"'")
            for token in combined.split()
            if len(token.strip(".,:;!?()[]{}\"'")) >= 3
        }
        # For general searches, a syntactically valid result is not enough to
        # stop provider fallback.  Require meaningful query overlap so a broad
        # relation hit such as "Area" cannot terminate a "Marie Curie ..."
        # search before later providers are tried.
        return len(query_tokens & result_tokens) >= min(2, max(1, len(query_tokens)))

    combined = " ".join(
        (str(row.title or ""), str(row.snippet or ""))
    ).casefold()
    query_tokens = {
        token
        for token in query.casefold().replace(".", " ").split()
        if len(token) >= 4 and token not in {"official", "documentation", "python"}
    }
    return bool(query_tokens.intersection(combined.split()))


def _search_terms(value: str) -> tuple[str, ...]:
    cleaned = str(value or "").casefold()
    for char in '"\'.,:;!?()[]{}':
        cleaned = cleaned.replace(char, " ")
    stop = {
        "and", "the", "for", "with", "from", "hangi", "nedir", "neresi",
        "official", "documentation",
    }
    return tuple(
        token for token in cleaned.split()
        if len(token) >= 3 and token not in stop
    )


def _quoted_phrases(value: str) -> tuple[str, ...]:
    import re
    return tuple(
        " ".join(match.split()).casefold()
        for match in re.findall(r'"([^"]{2,160})"', str(value or ""))
        if " ".join(match.split())
    )


def _requires_cross_provider_collection(query: str) -> bool:
    if _is_python_technical_query(query):
        return True
    if _quoted_phrases(query):
        return True
    terms = _search_terms(query)
    return len(terms) >= 4


def _result_score(row: ProviderSearchResult, *, query: str) -> float:
    title = str(row.title or "").casefold()
    snippet = str(row.snippet or "").casefold()
    combined = f"{title} {snippet}".strip()
    terms = set(_search_terms(query))
    result_terms = set(_search_terms(combined))
    overlap = terms & result_terms
    score = float(len(overlap) * 8)
    if terms:
        score += 25.0 * (len(overlap) / len(terms))
    for phrase in _quoted_phrases(query):
        if phrase in title:
            score += 80.0
        elif phrase in combined:
            score += 55.0
        else:
            score -= 30.0
    try:
        host = str(urlparse(str(row.url or "")).hostname or "").casefold()
    except ValueError:
        host = ""
    if host.endswith("wikipedia.org") or host.endswith("britannica.com"):
        score += 3.0
    if _is_python_technical_query(query) and (host == "docs.python.org" or host.endswith(".docs.python.org")):
        score += 60.0
    return score


class SearchProviderManager:
    def __init__(
        self,
        *,
        providers: tuple[SearchProvider, ...],
    ) -> None:
        self.providers = providers

    @classmethod
    def default(
        cls,
        **provider_kwargs,
    ) -> "SearchProviderManager":
        return cls(
            providers=(
                DuckDuckGoHtmlProvider(
                    **provider_kwargs
                ),
                DuckDuckGoLiteProvider(
                    **provider_kwargs
                ),
                BingHtmlProvider(
                    **provider_kwargs
                ),
            )
        )

    def search(
        self,
        query: str,
        max_results: int,
    ) -> tuple[
        list[ProviderSearchResult],
        tuple[ProviderFailure, ...],
    ]:
        limit = max(1, int(max_results))
        failures: list[ProviderFailure] = []
        seen: set[str] = set()
        collected: list[ProviderSearchResult] = []
        exhaustive = _requires_cross_provider_collection(query)
        technical_query = _is_python_technical_query(query)

        def add_row(row: ProviderSearchResult) -> None:
            key = _result_key(row)
            if not key or key in seen:
                return
            seen.add(key)
            collected.append(row)

        for row in _official_seed_results(query):
            add_row(row)

        variants = _query_variants(query)
        provider_limit = min(max(limit * 2, 6), 20) if exhaustive else limit

        providers = self.providers
        if _quoted_phrases(query):
            # Preserve the default provider ordering contract for ordinary
            # searches, but resolve exact quoted entities with Bing first in
            # the current desktop runtime.  This avoids DDG timeout latency
            # without changing legacy fallback behavior.
            providers = tuple(
                sorted(
                    self.providers,
                    key=lambda provider: (
                        0 if "bing" in str(getattr(provider, "name", "")).casefold() else 1
                    ),
                )
            )

        for provider in providers:
            provider_had_rows = False
            for variant in variants:
                try:
                    rows = provider.search(variant, provider_limit)
                except (
                    requests.RequestException,
                    RuntimeError,
                    ValueError,
                    TypeError,
                    OSError,
                ) as exc:
                    failures.append(
                        ProviderFailure(
                            provider=provider.name,
                            error=f"{type(exc).__name__}: {exc}",
                        )
                    )
                    break

                if rows:
                    provider_had_rows = True
                for row in rows:
                    add_row(row)

                # A quoted entity hit in title/snippet is already a strong
                # identity resolution.  Do not keep probing slower fallback
                # providers after it is found; relation evidence will be
                # extracted from the hydrated entity page by ResearchManager.
                if _quoted_phrases(query):
                    strong_quoted = [
                        row for row in collected
                        if _is_strong_result(row, query=query)
                    ]
                    if strong_quoted:
                        ranked_quoted = sorted(
                            strong_quoted,
                            key=lambda row: _result_score(row, query=query),
                            reverse=True,
                        )
                        return ranked_quoted[:limit], tuple(failures)

                if not exhaustive:
                    strong = [
                        row for row in collected
                        if _is_strong_result(row, query=query)
                    ]
                    if strong:
                        return strong[:limit], tuple(failures)

            if not exhaustive and provider_had_rows:
                strong = [
                    row for row in collected
                    if _is_strong_result(row, query=query)
                ]
                if strong:
                    return strong[:limit], tuple(failures)

        if not collected:
            return [], tuple(failures)

        ranked = sorted(
            collected,
            key=lambda row: (
                _result_score(row, query=query),
                1 if _is_strong_result(row, query=query) else 0,
            ),
            reverse=True,
        )
        if technical_query:
            ranked = sorted(
                ranked,
                key=lambda row: _result_score(row, query=query),
                reverse=True,
            )
        return ranked[:limit], tuple(failures)

