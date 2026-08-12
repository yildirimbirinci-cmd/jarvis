from types import SimpleNamespace

from artmach_assistant.core.fact_research_runtime import FactResearchRuntime
from artmach_assistant.core.research_manager import ResearchResult, ResearchSource
from artmach_assistant.core.research_providers.base import ProviderSearchResult
from artmach_assistant.core.research_providers.manager import SearchProviderManager


class Provider:
    def __init__(self, name, rows):
        self.name = name
        self.rows = rows
        self.calls = []

    def search(self, query, max_results):
        self.calls.append(query)
        return list(self.rows)[:max_results]


def test_unrelated_first_provider_does_not_stop_fallback() -> None:
    weak = Provider(
        "weak",
        [ProviderSearchResult(
            title="Area - Wikipedia",
            url="https://example.com/area",
            snippet="Area is a mathematical quantity.",
        )],
    )
    good = Provider(
        "good",
        [ProviderSearchResult(
            title="Marie Curie - Biography",
            url="https://example.org/marie-curie",
            snippet="Marie Curie was a physicist and chemist.",
        )],
    )
    rows, failures = SearchProviderManager(providers=(weak, good)).search(
        "Marie Curie scientific field", 4
    )
    assert not failures
    assert good.calls == ["Marie Curie scientific field"]
    assert rows[0].title == "Marie Curie - Biography"


def test_begin_research_invalidates_previous_report() -> None:
    runtime = FactResearchRuntime.__new__(FactResearchRuntime)
    runtime.last_factual_question = ""
    runtime.last_topic_anchor = ""
    runtime.session = SimpleNamespace(
        query="old",
        report="OLD AREA REPORT",
        learned=False,
        source_domains=("example.com",),
        topic_anchor="old",
        claim_key="",
        evidence_confidence=0.0,
    )
    runtime._topic_anchor = lambda query: "marie curie"
    runtime.begin_research("Marie Curie hangi alanda çalışmıştır")
    assert runtime.last_research_report() is None
    assert runtime.last_factual_question == "Marie Curie hangi alanda çalışmıştır"
