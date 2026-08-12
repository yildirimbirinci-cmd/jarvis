from artmach_assistant.core.research_providers.base import ProviderSearchResult
from artmach_assistant.core.research_providers.manager import SearchProviderManager


class FakeProvider:
    def __init__(self, name, rows):
        self.name = name
        self.rows = list(rows)
        self.calls = []

    def search(self, query, max_results):
        self.calls.append(query)
        return self.rows[:max_results]


def row(title, url, snippet=''):
    return ProviderSearchResult(title=title, url=url, snippet=snippet)


def test_quoted_entity_query_prefers_bing_and_stops_after_strong_identity_hit():
    first = FakeProvider('ddg', [
        row('Area - Wikipedia', 'https://example.com/area', 'Area is a mathematical concept.'),
    ])
    second = FakeProvider('bing', [
        row('Marie Curie', 'https://example.org/marie-curie', 'Marie Curie was a physicist and chemist.'),
    ])

    rows, failures = SearchProviderManager(providers=(first, second)).search(
        '"Marie Curie" scientific field', 4
    )

    assert failures == ()
    assert second.calls == ['"Marie Curie" scientific field']
    assert first.calls == []
    assert rows[0].title == 'Marie Curie'


def test_plain_navigation_query_keeps_fast_first_provider_behavior():
    first = FakeProvider('first', [
        row('Python Documentation', 'https://docs.python.org/3/', 'Official Python documentation.'),
    ])
    second = FakeProvider('second', [
        row('Other', 'https://example.org/other', 'Other result.'),
    ])

    rows, failures = SearchProviderManager(providers=(first, second)).search(
        'Python official documentation', 3
    )

    assert failures == ()
    assert rows[0].title == 'Python Documentation'
    assert second.calls == []
