from __future__ import annotations

import json
from types import SimpleNamespace

from artmach_assistant.core.assistant import AssistantEngine
from artmach_assistant.core.learning_memory import LearningMemory
from artmach_assistant.core.research_manager import ResearchManager, ResearchResult, ResearchSource


class _Config:
    internet_research_enabled = True


class _Dialogue:
    def __init__(self, answer: str) -> None:
        self.answer = answer

    def answer_from_evidence(self, question, evidence, **_kwargs):
        return self.answer


class _Researcher:
    def __init__(self, sources):
        self.sources = list(sources)

    def search(self, query, max_results=6):
        return ResearchResult(query=query, sources=list(self.sources))


def _engine(answer: str, sources: list[ResearchSource]) -> AssistantEngine:
    engine = AssistantEngine.__new__(AssistantEngine)
    engine.config = _Config()
    engine.dialogue = _Dialogue(answer)
    engine.researcher = _Researcher(sources)
    engine._interaction_cancelled = lambda: False
    engine._interaction_model_progress = lambda _value: None
    return engine


def test_source_relevance_requires_subject_and_relation_not_only_country_name() -> None:
    unrelated = ResearchSource(
        title="Türkiye gündemi",
        url="https://example.com/news",
        snippet="Türkiye gündemindeki son siyasi gelişmeler.",
        content="Terörsüz Türkiye oylaması ve siyasi takvim hakkında haberler.",
    )
    relevant = ResearchSource(
        title="Türkiye",
        url="https://example.com/turkey",
        snippet="Türkiye'nin başkenti Ankara'dır.",
        content="Ankara is the capital city of Turkey. Istanbul is the largest city.",
    )
    assert ResearchManager._source_relevant_to_query("Türkiye'nin başkenti neresidir", unrelated) is False
    assert ResearchManager._source_relevant_to_query("Türkiye'nin başkenti neresidir", relevant) is True


def test_off_topic_model_summary_is_rejected_even_when_sources_are_relevant() -> None:
    sources = [
        ResearchSource(
            "Türkiye",
            "https://example.com/turkey",
            "Türkiye'nin başkenti Ankara'dır.",
            "Ankara is the capital city of Turkey. Istanbul is the largest city.",
        )
    ]
    engine = _engine(
        "Bu metin Terörsüz Türkiye oylaması ve siyasi gelişmeler hakkındadır.",
        sources,
    )
    result = engine.research("Türkiye'nin başkenti neresidir")
    assert result.summary == "Kaynaklar bu soruyu güvenilir biçimde doğrulamıyor."


def test_supported_answer_keeps_only_evidence_backed_fact() -> None:
    sources = [
        ResearchSource(
            "Türkiye",
            "https://example.com/turkey",
            "Türkiye'nin başkenti Ankara'dır.",
            "Ankara is the capital city of Turkey. Istanbul is the largest city.",
        )
    ]
    engine = _engine("Türkiye'nin başkenti Ankara'dır.", sources)
    result = engine.research("Türkiye'nin başkenti neresidir")
    assert result.summary == "Türkiye'nin başkenti Ankara'dır."
    evidence = ResearchManager.supporting_evidence(result.query, result.summary, result.sources)
    assert evidence
    assert "Ankara" in evidence[0][0]


def test_legacy_verified_fact_without_evidence_is_dropped_on_restart(tmp_path) -> None:
    path = tmp_path / "learned_memory.json"
    path.write_text(
        json.dumps(
            [
                {
                    "kind": "verified_fact",
                    "trigger": "turkiye nin baskenti",
                    "response": "Türkiye'nin başkenti İstanbul'dur.",
                    "source": "verified_internet_research",
                    "confidence": 1.0,
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    memory = LearningMemory(path)
    assert memory.records == []
    assert memory.match("Türkiye'nin başkenti neresidir") is None


def test_verified_fact_persists_with_claim_and_supporting_passage(tmp_path) -> None:
    path = tmp_path / "learned_memory.json"
    memory = LearningMemory(path)
    memory.teach(
        "verified_fact",
        "turkiye nin baskentini",
        response="Türkiye'nin başkenti Ankara'dır.",
        source="verified_internet_research_v2",
        confidence=1.0,
        evidence="Ankara is the capital city of Turkey.",
        evidence_url="https://example.com/turkey",
    )
    restarted = LearningMemory(path)
    record = restarted.match("Türkiye'nin başkebnti neresi")
    assert record is not None
    assert record.response == "Türkiye'nin başkenti Ankara'dır."
    assert "Ankara is the capital city" in record.evidence
    assert record.evidence_url == "https://example.com/turkey"


def test_verified_facts_are_not_bulk_injected_into_dialogue_model_context(tmp_path) -> None:
    memory = LearningMemory(tmp_path / "learned_memory.json")
    memory.teach(
        "verified_fact",
        "turkiye nin baskenti",
        response="Türkiye'nin başkenti Ankara'dır.",
        source="verified_internet_research_v2",
        evidence="Ankara is the capital city of Turkey.",
        evidence_url="https://example.com/turkey",
    )
    memory.teach(
        "dialogue",
        "selam",
        response="Merhaba.",
        source="conversation",
    )
    context = memory.context()
    assert all(row["kind"] != "verified_fact" for row in context)
    assert any(row["kind"] == "dialogue" for row in context)
