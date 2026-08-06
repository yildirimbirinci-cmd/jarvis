from __future__ import annotations

from artmach_assistant.core.evidence_ranking import score_evidence_source
from artmach_assistant.core.research_providers.base import ProviderSearchResult
from artmach_assistant.core.research_providers.manager import SearchProviderManager


class FakeProvider:
    def __init__(self, name: str, rows_by_query: dict[str, list[ProviderSearchResult]]) -> None:
        self.name = name
        self.rows_by_query = rows_by_query
        self.calls: list[str] = []

    def search(self, query: str, max_results: int) -> list[ProviderSearchResult]:
        self.calls.append(query)
        return list(self.rows_by_query.get(query, ()))[:max_results]


def _row(title: str, url: str, snippet: str = "") -> ProviderSearchResult:
    return ProviderSearchResult(title=title, url=url, snippet=snippet)


def test_technical_python_search_does_not_stop_at_generic_landing_pages() -> None:
    query = "Python cProfile performance profiling official documentation"
    first = FakeProvider(
        "first",
        {
            query: [
                _row("Welcome to Python.org", "https://www.python.org/"),
                _row("Download Python", "https://www.python.org/downloads/"),
            ]
        },
    )
    second = FakeProvider(
        "second",
        {
            "site:docs.python.org/3/library/profile.html cProfile profile pstats profiling": [
                _row(
                    "The Python Profilers",
                    "https://docs.python.org/3/library/profile.html",
                    "Official cProfile and pstats profiling documentation.",
                )
            ]
        },
    )

    rows, failures = SearchProviderManager(
        providers=(first, second),
    ).search(query, 4)

    assert failures == ()
    assert any("site:docs.python.org" in value for value in first.calls)
    assert second.calls
    assert rows[0].url == "https://docs.python.org/3/library/profile.html"
    assert not any(row.url.endswith("/downloads/") for row in rows[:1])


def test_technical_search_seeds_known_official_documentation_candidates() -> None:
    query = "Python concurrent task orchestration performance diagnostics"
    provider = FakeProvider("empty", {})

    rows, _failures = SearchProviderManager(
        providers=(provider,),
    ).search(query, 4)

    urls = {row.url for row in rows}
    assert "https://docs.python.org/3/library/concurrent.futures.html" in urls
    assert "https://docs.python.org/3/library/time.html" in urls


def test_nontechnical_search_keeps_fast_first_provider_behavior() -> None:
    query = "Python official documentation"
    first = FakeProvider(
        "first",
        {
            query: [
                _row(
                    "Python Documentation",
                    "https://docs.python.org/3/",
                    "Official Python documentation.",
                )
            ]
        },
    )
    second = FakeProvider("second", {})

    rows, failures = SearchProviderManager(
        providers=(first, second),
    ).search(query, 3)

    assert failures == ()
    assert len(rows) == 1
    assert second.calls == []


def test_generic_python_landing_page_is_penalized_for_strict_query() -> None:
    score = score_evidence_source(
        query="Python cProfile performance profiling official documentation",
        title="Welcome to Python.org",
        url="https://www.python.org/",
        snippet="Python programming language official website.",
        content="Python programming language " * 200,
    )

    assert score.accepted is False
    assert score.total < 45
