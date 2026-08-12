from types import SimpleNamespace

from artmach_assistant.core.assistant import AssistantEngine
from artmach_assistant.core.research_manager import ResearchManager, ResearchResult, ResearchSource
from artmach_assistant.core.research_providers.base import ProviderSearchResult
from artmach_assistant.core.research_providers.manager import SearchProviderManager


def test_entity_seed_query_preserves_exact_multitoken_subject() -> None:
    assert (
        ResearchManager.entity_seed_query(
            "Marie Curie hangi alanda çalışmıştır"
        ).casefold()
        == '"marie curie"'
    )
    assert ResearchManager.entity_seed_query("Python nedir") == '"python"'


class _Provider:
    name = "bing-html"
    def search(self, query, max_results):
        assert query == '"Marie Curie"'
        return [
            ProviderSearchResult(
                title="Marie Curie - Vikipedi",
                url="https://tr.wikipedia.org/wiki/Marie_Curie",
                snippet="Marie Curie fizikçi ve kimyagerdi.",
            )
        ]


def test_quoted_entity_result_can_return_without_slow_fallback_provider() -> None:
    class _Never:
        name = "slow"
        def search(self, query, max_results):
            raise AssertionError("fallback provider must not be called")

    rows, failures = SearchProviderManager(
        providers=(_Provider(), _Never())
    ).search('"Marie Curie"', 4)

    assert not failures
    assert rows
    assert "Marie Curie" in rows[0].title


class _Researcher:
    def __init__(self):
        self.calls = []

    def search(self, query, max_results=6):
        self.calls.append(query)
        if query.casefold() == '"marie curie"':
            return ResearchResult(
                query=query,
                sources=[
                    ResearchSource(
                        "Marie Curie - Vikipedi",
                        "https://tr.wikipedia.org/wiki/Marie_Curie",
                        "Marie Curie fizikçi ve kimyagerdi.",
                        "Marie Curie fizikçi ve kimyagerdi. Radyoaktivite üzerine çalıştı.",
                    )
                ],
            )
        raise AssertionError(f"unexpected fallback search: {query!r}")


class _Dialogue:
    def plan_research_queries(self, *args, **kwargs):
        return ()
    def answer_from_evidence(self, query, source_context, **kwargs):
        assert "Marie Curie" in source_context
        return "Marie Curie fizik ve kimya alanlarında çalışmıştır."


def test_assistant_research_uses_entity_seed_before_broad_search() -> None:
    engine = AssistantEngine.__new__(AssistantEngine)
    engine.config = SimpleNamespace(internet_research_enabled=True)
    engine.dialogue = _Dialogue()
    engine.researcher = _Researcher()
    engine._interaction_cancelled = lambda: False
    engine._interaction_model_progress = lambda *_: None

    result = AssistantEngine.research(
        engine, "Marie Curie hangi alanda çalışmıştır"
    )

    assert [item.casefold() for item in engine.researcher.calls] == ['"marie curie"']
    assert result.sources
    assert "Marie Curie" in result.sources[0].title
