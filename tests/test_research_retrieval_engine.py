from pathlib import Path

from artmach_assistant.core.research_contracts import EvidenceClaim, ResearchTopic
from artmach_assistant.core.research_knowledge_store import ResearchKnowledgeStore
from artmach_assistant.core.research_retrieval_engine import ResearchRetrievalEngine
from artmach_assistant.core.research_topic_state import ResearchTopicStateStore


def _store(tmp_path: Path) -> ResearchKnowledgeStore:
    store = ResearchKnowledgeStore(tmp_path / "knowledge.json")
    store.remember(EvidenceClaim.build(
        subject="Ada Lovelace",
        predicate="identity",
        object_value="Ada Lovelace was an English mathematician and writer.",
        evidence="Source evidence",
        sources=("https://example.test/ada",),
        confidence=0.91,
        verified_at="2026-08-12T07:00:00+00:00",
    ))
    store.remember(EvidenceClaim.build(
        subject="Ada Lovelace",
        predicate="field",
        object_value="She is associated with mathematics and early computing.",
        evidence="Field evidence",
        sources=("https://example.test/field",),
        confidence=0.87,
        verified_at="2026-08-12T07:01:00+00:00",
    ))
    return store


def test_identity_surface_variants_recall_same_claim(tmp_path: Path):
    engine = ResearchRetrievalEngine(_store(tmp_path))
    first = engine.recall("Ada Lovelace kimdir?")
    second = engine.recall("Ada Lovelace kimdi?")
    assert first is not None and second is not None
    assert first.topic.relation == "identity"
    assert second.topic.relation == "identity"
    assert first.records[0].object == second.records[0].object


def test_general_question_returns_subject_claims(tmp_path: Path):
    engine = ResearchRetrievalEngine(_store(tmp_path))
    result = engine.recall("Ada Lovelace hakkinda ne biliyorsun?")
    assert result is not None
    assert result.found is True
    assert {row.predicate for row in result.records} == {"identity", "field"}


def test_relation_specific_retrieval_prefers_relation(tmp_path: Path):
    engine = ResearchRetrievalEngine(_store(tmp_path))
    result = engine.recall("Ada Lovelace hangi alanda calisti?")
    assert result is not None
    assert result.records
    assert result.records[0].predicate == "field"


def test_unknown_subject_returns_empty_result_not_false_match(tmp_path: Path):
    engine = ResearchRetrievalEngine(_store(tmp_path))
    result = engine.recall("Grace Hopper kimdir?")
    assert result is not None
    assert result.found is False
    assert result.records == ()


def test_current_topic_followup_uses_structured_state(tmp_path: Path):
    state = ResearchTopicStateStore(tmp_path / "topic_state.json")
    state.remember(ResearchTopic(subject="Ada Lovelace", relation="identity", original_question="Ada Lovelace kimdir?"), "chat")
    engine = ResearchRetrievalEngine(_store(tmp_path), state)
    result = engine.recall("Bunu bana anlat", scope="chat")
    assert result is not None
    assert result.topic.subject == "Ada Lovelace"
    assert result.records[0].predicate == "identity"


def test_unrelated_sentence_never_returns_false_memory_match(tmp_path: Path):
    engine = ResearchRetrievalEngine(_store(tmp_path))
    result = engine.recall("Yarin saat 10 icin toplantiyi hatirlat")
    assert result is not None
    assert result.records == ()
