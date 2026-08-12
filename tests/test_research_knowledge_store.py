from pathlib import Path

from artmach_assistant.core.research_contracts import ResearchAction, ResearchRequest, ResearchTopic
from artmach_assistant.core.research_knowledge_store import ResearchKnowledgeStore, claim_from_research
from artmach_assistant.core.research_manager import ResearchResult, ResearchSource


def _request(subject: str, relation: str = "identity") -> ResearchRequest:
    return ResearchRequest(
        action=ResearchAction.RESEARCH_SUMMARIZE_AND_LEARN,
        topic=ResearchTopic(subject=subject, relation=relation, original_question=f"{subject} kimdir?"),
        raw_text=f"{subject} internette arastir, ogren ve bana anlat",
    )


def _result(subject: str) -> ResearchResult:
    return ResearchResult(
        query=subject,
        sources=[
            ResearchSource("Source A", "https://example.com/a", f"{subject} source fact A"),
            ResearchSource("Source B", "https://example.com/b", f"{subject} source fact B"),
        ],
        summary=f"{subject} verified summary",
    )


def test_claim_uses_structured_subject_relation_sources_and_summary() -> None:
    claim = claim_from_research(_request("Ada Lovelace"), _result("Ada Lovelace"))
    assert claim.subject == "Ada Lovelace"
    assert claim.predicate == "identity"
    assert claim.object == "Ada Lovelace verified summary"
    assert claim.sources == ("https://example.com/a", "https://example.com/b")
    assert "source fact A" in claim.evidence
    assert claim.confidence > 0.5
    assert claim.verified_at


def test_store_persists_and_reloads_claim(tmp_path: Path) -> None:
    path = tmp_path / "research_knowledge.json"
    store = ResearchKnowledgeStore(path)
    stored = store.remember(claim_from_research(_request("Grace Hopper"), _result("Grace Hopper")))
    assert stored.subject == "Grace Hopper"

    reloaded = ResearchKnowledgeStore(path)
    rows = reloaded.related("Grace Hopper", "identity")
    assert len(rows) == 1
    assert rows[0].object == "Grace Hopper verified summary"
    assert rows[0].sources == ("https://example.com/a", "https://example.com/b")


def test_same_subject_relation_updates_instead_of_duplicate(tmp_path: Path) -> None:
    path = tmp_path / "research_knowledge.json"
    store = ResearchKnowledgeStore(path)
    store.remember(claim_from_research(_request("Alan Turing"), _result("Alan Turing")))
    newer = _result("Alan Turing")
    newer.summary = "Alan Turing newer verified summary"
    store.remember(claim_from_research(_request("Alan Turing"), newer))
    rows = store.related("Alan Turing", "identity")
    assert len(rows) == 1
    assert rows[0].object == "Alan Turing newer verified summary"


def test_different_relations_for_same_subject_are_independent(tmp_path: Path) -> None:
    path = tmp_path / "research_knowledge.json"
    store = ResearchKnowledgeStore(path)
    store.remember(claim_from_research(_request("Marie Curie", "identity"), _result("Marie Curie")))
    store.remember(claim_from_research(_request("Marie Curie", "field"), _result("Marie Curie")))
    assert len(store.related("Marie Curie", "identity")) == 2
    assert store.related("Marie Curie", "identity")[0].predicate == "identity"
    assert store.related("Marie Curie", "field")[0].predicate == "field"


def test_unrelated_subject_does_not_match(tmp_path: Path) -> None:
    store = ResearchKnowledgeStore(tmp_path / "research_knowledge.json")
    store.remember(claim_from_research(_request("Zeki Muren"), _result("Zeki Muren")))
    assert store.related("Nikola Tesla", "identity") == []
