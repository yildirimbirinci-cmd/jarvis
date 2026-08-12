from __future__ import annotations

from artmach_assistant.core.assistant import AssistantEngine
from artmach_assistant.core.research_manager import ResearchManager, ResearchResult, ResearchSource


class _Config:
    internet_research_enabled = True


class _Dialogue:
    def __init__(self) -> None:
        self.calls = []

    def answer_from_evidence(self, question, evidence, **_kwargs):
        self.calls.append((question, evidence))
        assert "Terror-free" not in evidence
        assert "Ankara is the capital" in evidence
        return "Türkiye'nin başkenti Ankara'dır."


class _Researcher:
    def search(self, query, max_results=6):
        return ResearchResult(
            query=query,
            sources=[
                ResearchSource(
                    "Türkiye gündemi",
                    "https://example.com/news",
                    "Türkiye hakkında son haberler.",
                    "Terror-free Türkiye voting calendar and political developments.",
                ),
                ResearchSource(
                    "Turkey (Türkiye)",
                    "https://example.com/turkey",
                    "Ankara is the capital city of Turkey, while Istanbul is its largest city.",
                    "Turkey is a country spanning Europe and Asia. Ankara is the capital city of Turkey. Istanbul is the largest city.",
                ),
            ],
        )


def _engine():
    engine = AssistantEngine.__new__(AssistantEngine)
    engine.config = _Config()
    engine.dialogue = _Dialogue()
    engine.researcher = _Researcher()
    engine._interaction_cancelled = lambda: False
    engine._interaction_model_progress = lambda _value: None
    return engine


def test_query_evidence_extracts_answer_relation_before_model_synthesis():
    sources = _Researcher().search("turkiye nin baskentini").sources
    passages = ResearchManager.query_evidence("turkiye nin baskentini", sources)
    assert passages
    assert "Ankara is the capital" in passages[0][0]
    assert all("voting" not in passage for passage, _url in passages)


def test_research_sends_only_query_relevant_passages_to_dialogue_model():
    engine = _engine()
    result = engine.research("turkiye nin baskentini")
    assert result.summary == "Türkiye'nin başkenti Ankara'dır."
    assert engine.dialogue.calls
    _question, evidence = engine.dialogue.calls[-1]
    assert "Ankara is the capital" in evidence
    assert "voting" not in evidence
