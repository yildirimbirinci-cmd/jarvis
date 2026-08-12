from pathlib import Path

from artmach_assistant.core.fact_research_runtime import FactResearchRuntime
from artmach_assistant.core.learning_memory import LearningMemory
from artmach_assistant.core.research_manager import ResearchResult, ResearchSource, ResearchManager


def test_world_fact_guard_covers_live_acceptance_topics():
    runtime = FactResearchRuntime()
    for text in (
        "fenerbahçe spor kulübü hangi ülkenin spor kulübüdür",
        "bu kulüp hangi ligde mücadele ediyor",
        "istanbul türkiyenin hangi bölgesinde bulunmaktadır",
        "Türkiye kaç bölgeden oluşur ve bu bölgelerin isimleri nelerdir",
    ):
        assert runtime.is_world_fact_question(text)
    assert not runtime.is_world_fact_question("senin bilincin var mı")
    assert not runtime.is_world_fact_question("Python asyncio ile threading arasındaki fark nedir")


def test_deictic_research_uses_isolated_last_factual_topic():
    runtime = FactResearchRuntime()
    runtime.note_factual_question("Türkiye kaç bölgeden oluşur ve bu bölgelerin isimleri nelerdir")
    assert runtime.resolve_explicit_research_query(
        "", before="bu konuyu", after=""
    ) == "Türkiye kaç bölgeden oluşur ve bu bölgelerin isimleri nelerdir"


def test_last_research_report_and_domains_are_isolated():
    runtime = FactResearchRuntime()
    result = ResearchResult(
        query="fenerbahçe hangi ülkede",
        sources=[ResearchSource("Fenerbahçe Spor Kulübü", "https://www.fenerbahce.org/", "")],
        summary="Kaynaklar bu soruyu güvenilir biçimde doğrulamıyor.",
    )
    runtime.remember_research(result.query, result, learned=False)
    assert "ARAŞTIRMA: fenerbahçe hangi ülkede" in runtime.last_research_report()
    assert "fenerbahce.org" in runtime.source_capability_report()


def test_verified_fact_lookup_tolerates_small_relation_typo(tmp_path: Path):
    memory = LearningMemory(tmp_path / "facts.json")
    memory.teach(
        "verified_fact",
        "Türkiye'nin başkenti neresidir",
        response="Türkiye'nin başkenti Ankara'dır.",
        source="verified_internet_research_v2",
        evidence="Ankara is the capital city of Turkey.",
        evidence_url="https://example.com/turkey",
    )
    record = memory.match_verified_fact("Türkiye'nin başkebnti neresi")
    assert record is not None
    assert "Ankara" in record.response


def test_semantic_tokens_cover_country_league_region_club_relations():
    assert {"fenerbahce", "spor", "kulup", "ulke"} <= ResearchManager.semantic_tokens(
        "Fenerbahçe spor kulübü hangi ülkenin spor kulübüdür"
    )
    assert "lig" in ResearchManager.semantic_tokens("hangi ligde mücadele ediyor")
    assert "bolge" in ResearchManager.semantic_tokens("hangi bölgesinde bulunmaktadır")
