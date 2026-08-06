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
        return bool(str(row.title or "").strip() and host)

    combined = " ".join(
        (str(row.title or ""), str(row.snippet or ""))
    ).casefold()
    query_tokens = {
        token
        for token in query.casefold().replace(".", " ").split()
        if len(token) >= 4 and token not in {"official", "documentation", "python"}
    }
    return bool(query_tokens.intersection(combined.split()))


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
        strong: list[ProviderSearchResult] = []
        weak: list[ProviderSearchResult] = []
        seen: set[str] = set()
        failures: list[ProviderFailure] = []
        technical_query = _is_python_technical_query(query)

        def add_row(row: ProviderSearchResult) -> None:
            key = _result_key(row)
            if not key or key in seen:
                return
            seen.add(key)
            target = strong if _is_strong_result(row, query=query) else weak
            target.append(row)

        for row in _official_seed_results(query):
            add_row(row)

        variants = _query_variants(query)

        for provider in self.providers:
            provider_had_results = False

            for variant in variants:
                try:
                    rows = provider.search(
                        variant,
                        limit,
                    )
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
                            error=(
                                f"{type(exc).__name__}: {exc}"
                            ),
                        )
                    )
                    break

                if rows:
                    provider_had_results = True

                for row in rows:
                    add_row(row)

                if len(strong) >= limit:
                    return strong[:limit], tuple(failures)

                if strong and not technical_query:
                    return strong[:limit], tuple(failures)

            if provider_had_results and strong and not technical_query:
                break

        merged = strong + weak
        return merged[:limit], tuple(failures)
