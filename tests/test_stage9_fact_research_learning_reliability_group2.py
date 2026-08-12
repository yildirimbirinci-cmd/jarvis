from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from artmach_assistant.core.assistant import AssistantEngine
from artmach_assistant.core.fact_research_runtime import FactResearchRuntime
from artmach_assistant.core.learning_memory import LearningMemory
from artmach_assistant.core.research_manager import ResearchManager, ResearchResult, ResearchSource


def test_relation_aware_search_expansion_does_not_guess_answer_values() -> None:
    country = ResearchManager.expanded_queries(
        "fenerbahçe spor kulübü hangi ülkenin spor kulübüdür"
    )
    league = ResearchManager.expanded_queries(
        "fenerbahçe spor kulübü hangi ligde mücadele ediyor"
    )
    region = ResearchManager.expanded_queries(
        "istanbul türkiyenin hangi bölgesinde bulunmaktadır"
    )
    assert country[0].startswith("fenerbahçe")
    assert any("country" in row for row in country[1:])
    assert any("league" in row for row in league[1:])
    assert any("geographical region" in row for row in region[1:])
    regions = ResearchManager.expanded_queries(
        "Türkiye kaç bölgeden oluşur ve bu bölgelerin isimleri nelerdir"
    )
    assert "turkiye geographical region" in regions
    assert all("ankara" not in row.casefold() for row in country + league + region + regions)


def test_deictic_factual_followup_reuses_only_narrow_topic_anchor() -> None:
    runtime = FactResearchRuntime()
    runtime.note_factual_question(
        "fenerbahçe spor kulübü hangi ülkenin spor kulübüdür"
    )
    resolved = runtime.resolve_factual_question(
        "bu kulüp hangi ligde mücadele ediyor"
    )
    folded = runtime._fold(resolved)
    assert folded.startswith("fenerbahce spor kulubu")
    assert "hangi ligde mucadele ediyor" in folded


def test_deictic_research_after_followup_uses_resolved_question() -> None:
    runtime = FactResearchRuntime()
    runtime.note_factual_question(
        "fenerbahçe spor kulübü hangi ülkenin spor kulübüdür"
    )
    resolved = runtime.resolve_factual_question(
        "bu kulüp hangi ligde mücadele ediyor"
    )
    runtime.note_factual_question(resolved)
    query = runtime.resolve_explicit_research_query(
        "", before="bu konuyu", after=""
    )
    assert runtime._fold(query) == runtime._fold(resolved)


def test_verified_dynamic_fact_expires_but_stable_fact_does_not(tmp_path: Path) -> None:
    memory = LearningMemory(tmp_path / "facts.json")
    stable = memory.teach_verified_fact(
        "Türkiye'nin başkenti neresidir",
        response="Türkiye'nin başkenti Ankara'dır.",
        evidence="Ankara is the capital city of Turkey.",
        evidence_urls=["https://example.com/turkey"],
    )
    dynamic = memory.teach_verified_fact(
        "Fenerbahçe hangi ligde mücadele ediyor",
        response="Fenerbahçe X liginde mücadele ediyor.",
        evidence="Fenerbahce competes in X league.",
        evidence_urls=["https://example.com/league"],
        dynamic_ttl_days=30,
    )
    assert stable.expires_at == ""
    assert dynamic.expires_at
    assert dynamic.verified_at
    assert isinstance(dynamic.evidence_urls, list)
    assert any("example.com/league" in url for url in dynamic.evidence_urls)


def test_expired_dynamic_fact_is_not_reused(tmp_path: Path) -> None:
    memory = LearningMemory(tmp_path / "facts.json")
    record = memory.teach_verified_fact(
        "Fenerbahçe hangi ligde mücadele ediyor",
        response="Eski lig cevabı",
        evidence="Old league evidence.",
        evidence_urls=["https://example.com/old"],
    )
    record.expires_at = (datetime.now() - timedelta(days=1)).isoformat(timespec="seconds")
    memory.save()
    restarted = LearningMemory(memory.path)
    assert restarted.match_verified_fact("Fenerbahçe hangi ligde mücadele ediyor") is None


def test_new_equivalent_verified_fact_supersedes_old_claim(tmp_path: Path) -> None:
    memory = LearningMemory(tmp_path / "facts.json")
    memory.teach_verified_fact(
        "Fenerbahçe hangi ligde mücadele ediyor",
        response="Eski cevap",
        evidence="Old evidence.",
        evidence_urls=["https://example.com/old"],
    )
    memory.teach_verified_fact(
        "Fenerbahçe futbol takımı hangi ligde oynuyor",
        response="Yeni cevap",
        evidence="New evidence.",
        evidence_urls=["https://example.com/new"],
    )
    verified = [row for row in memory.records if row.kind == "verified_fact"]
    assert len(verified) == 1
    assert verified[0].response == "Yeni cevap"


def test_verified_fact_lookup_handles_relation_suffixes_and_filler_verbs(tmp_path: Path) -> None:
    memory = LearningMemory(tmp_path / "facts.json")
    memory.teach_verified_fact(
        "Fenerbahçe futbol kulübü hangi ligde mücadele ediyor",
        response="Kaynaklı lig cevabı",
        evidence="Fenerbahce competes in the league.",
        evidence_urls=["https://example.com/league"],
    )
    found = memory.match_verified_fact(
        "Fenerbahçe futbol kulübü hangi ligde faaliyet göstermektedir"
    )
    assert found is not None
    assert found.response == "Kaynaklı lig cevabı"


class _Config:
    internet_research_enabled = True


class _ExpandedResearcher:
    def __init__(self) -> None:
        self.queries = ()

    def search_many(self, queries, *, max_results_per_query=4):
        self.queries = tuple(queries)
        return [
            ResearchResult(
                query=self.queries[-1],
                sources=[
                    ResearchSource(
                        title="Fenerbahce Sports Club",
                        url="https://example.com/fenerbahce",
                        snippet="Fenerbahce is a Turkish professional sports club.",
                        content=(
                            "Fenerbahce is a Turkish professional sports club based in Istanbul, Turkey. "
                            "Its football team competes in the national league."
                        ),
                    )
                ],
            )
        ]

    def search(self, query, max_results=6):
        # Entity-seed-first research is allowed to probe the exact entity.
        # This fixture intentionally makes that seed unavailable so the test
        # still verifies the expanded-query fallback and original-question
        # reporting contract.
        raise RuntimeError("entity seed unavailable in fallback fixture")


class _Dialogue:
    def __init__(self) -> None:
        self.calls = []

    def answer_from_evidence(self, question, evidence, **_kwargs):
        self.calls.append((question, evidence))
        return "Fenerbahçe Spor Kulübü Türkiye'de faaliyet gösteren bir spor kulübüdür."


def test_assistant_research_uses_expanded_queries_but_reports_original_question() -> None:
    engine = AssistantEngine.__new__(AssistantEngine)
    engine.config = _Config()
    engine.researcher = _ExpandedResearcher()
    engine.dialogue = _Dialogue()
    engine._interaction_cancelled = lambda: False
    engine._interaction_model_progress = lambda _value: None

    query = "fenerbahçe spor kulübü hangi ülkenin spor kulübüdür"
    result = engine.research(query)

    assert result.query == query
    assert len(engine.researcher.queries) >= 2
    assert any("country" in row for row in engine.researcher.queries)
    assert result.summary.startswith("Fenerbahçe Spor Kulübü Türkiye")
    assert engine.dialogue.calls


def test_verified_learning_keeps_multiple_supporting_source_urls(tmp_path: Path) -> None:
    engine = AssistantEngine.__new__(AssistantEngine)
    engine.learning_memory = LearningMemory(tmp_path / "facts.json")
    result = ResearchResult(
        query="Türkiye'nin başkenti neresidir",
        sources=[
            ResearchSource(
                "Source A",
                "https://example.com/a",
                "Türkiye'nin başkenti Ankara'dır.",
                "Türkiye'nin başkenti Ankara'dır.",
            ),
            ResearchSource(
                "Source B",
                "https://example.org/b",
                "Ankara is the capital city of Turkey.",
                "Ankara is the capital city of Turkey.",
            ),
        ],
        summary="Türkiye'nin başkenti Ankara'dır.",
    )
    assert engine._learn_verified_research_fact(result.query, result)
    record = engine.learning_memory.match_verified_fact(result.query)
    assert record is not None
    assert record.evidence
    assert record.evidence_urls
    assert record.verified_at


def test_explicit_user_fact_is_kept_separate_from_verified_web_fact(tmp_path: Path) -> None:
    memory = LearningMemory(tmp_path / "facts.json")
    memory.teach_user_fact(
        "Fenerbahçe spor kulübü Türkiye'de faaliyet gösteren bir kulüptür",
        response="Fenerbahçe spor kulübü Türkiye'de faaliyet gösteren bir kulüptür",
    )
    assert memory.match_verified_fact("Fenerbahçe hangi ülkede faaliyet gösteriyor") is None
    taught = memory.match_user_fact("Fenerbahçe hangi ülkede faaliyet gösteriyor")
    assert taught is not None
    assert taught.source == "explicit_user_teaching"


def test_assistant_natural_fact_teaching_preserves_user_provenance(tmp_path: Path) -> None:
    engine = AssistantEngine.__new__(AssistantEngine)
    engine.learning_memory = LearningMemory(tmp_path / "facts.json")
    engine.dialogue = SimpleNamespace(remember=lambda *_args, **_kwargs: None)
    engine.command_key = AssistantEngine.command_key

    response = engine._learn_explicit_user_fact(
        '"Fenerbahçe spor kulübü Türkiye\'de faaliyet gösteren bir kulüptür" gibi anlamları cümleler içerisinde algılamalı ve öğrenmelisin'
    )
    assert response is not None
    record = engine.learning_memory.match_user_fact(
        "Fenerbahçe hangi ülkede faaliyet gösteriyor"
    )
    assert record is not None
    assert "Türkiye'de" in record.response
    assert record.kind == "user_fact"


def test_user_facts_are_not_bulk_injected_into_general_dialogue_context(tmp_path: Path) -> None:
    memory = LearningMemory(tmp_path / "facts.json")
    memory.teach_user_fact(
        "Fenerbahçe Türkiye'de faaliyet gösteren bir kulüptür",
        response="Fenerbahçe Türkiye'de faaliyet gösteren bir kulüptür",
    )
    memory.teach(
        "dialogue",
        "selam",
        response="Merhaba.",
        source="conversation",
    )
    context = memory.context()
    assert all(row["kind"] != "user_fact" for row in context)
    assert any(row["kind"] == "dialogue" for row in context)
