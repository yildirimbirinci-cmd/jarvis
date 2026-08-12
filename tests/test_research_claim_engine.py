from pathlib import Path

from artmach_assistant.core.research_claim_engine import ResearchClaimEngine
from artmach_assistant.core.research_contracts import ResearchAction, ResearchRequest, ResearchTopic
from artmach_assistant.core.research_knowledge_store import ResearchKnowledgeStore
from artmach_assistant.core.research_manager import ResearchResult, ResearchSource


class FakeDialogue:
    def __init__(self, response: str) -> None:
        self.response = response
        self.prompts = []

    def respond(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.response


def request(subject="Ada Lovelace", relation="identity"):
    return ResearchRequest(
        action=ResearchAction.RESEARCH_AND_LEARN,
        topic=ResearchTopic(subject=subject, relation=relation, original_question=f"{subject} kimdir?"),
    )


def result(subject="Ada Lovelace"):
    return ResearchResult(
        query=subject,
        sources=[
            ResearchSource("A", "https://example.org/a", "mathematician and writer", "long content"),
            ResearchSource("B", "https://example.org/b", "worked on the Analytical Engine", "long content"),
        ],
        summary="summary",
    )


def test_extracts_atomic_grounded_claims():
    dialogue = FakeDialogue('[{"subject":"Ada Lovelace","predicate":"identity","object":"mathematician and writer","evidence":"source text","source_indexes":[1],"confidence":0.9},{"subject":"Ada Lovelace","predicate":"worked_on","object":"Analytical Engine","evidence":"source text","source_indexes":[2],"confidence":0.8}]')
    extracted = ResearchClaimEngine(dialogue).extract(request(), result())
    assert not extracted.used_fallback
    assert len(extracted.claims) == 2
    assert extracted.claims[0].sources == ("https://example.org/a",)
    assert extracted.claims[1].predicate == "worked_on"


def test_rejects_claim_without_source_evidence_and_uses_fallback():
    dialogue = FakeDialogue('[{"subject":"Ada Lovelace","predicate":"identity","object":"invented the internet","evidence":"","source_indexes":[],"confidence":1}]')
    extracted = ResearchClaimEngine(dialogue).extract(request(), result())
    assert extracted.used_fallback
    assert len(extracted.claims) == 1
    assert extracted.claims[0].object == "summary"


def test_accepts_json_code_fence():
    dialogue = FakeDialogue('```json\n[{"predicate":"identity","object":"mathematician","evidence":"supported","source_indexes":[1],"confidence":0.7}]\n```')
    extracted = ResearchClaimEngine(dialogue).extract(request(), result())
    assert not extracted.used_fallback
    assert extracted.claims[0].subject == "Ada Lovelace"


def test_deduplicates_same_atomic_claim():
    row = '{"subject":"Ada Lovelace","predicate":"identity","object":"mathematician","evidence":"supported","source_indexes":[1],"confidence":0.7}'
    extracted = ResearchClaimEngine(FakeDialogue(f"[{row},{row}]")).extract(request(), result())
    assert len(extracted.claims) == 1


def test_remember_all_and_relation_retrieval(tmp_path: Path):
    dialogue = FakeDialogue('[{"subject":"Ada Lovelace","predicate":"identity","object":"mathematician","evidence":"supported","source_indexes":[1],"confidence":0.7}]')
    engine = ResearchClaimEngine(dialogue)
    extracted = engine.extract(request(), result())
    store = ResearchKnowledgeStore(tmp_path / "knowledge.json")
    saved = engine.remember_all(store, extracted.claims)
    found = engine.retrieve(store, ResearchTopic(subject="Ada Lovelace", relation="identity"))
    assert len(saved) == 1
    assert len(found) == 1
    assert found[0].object == "mathematician"


def test_relation_retrieval_falls_back_to_general(tmp_path: Path):
    store = ResearchKnowledgeStore(tmp_path / "knowledge.json")
    fallback = ResearchClaimEngine(FakeDialogue("[]")).extract(
        ResearchRequest(action=ResearchAction.RESEARCH_AND_LEARN, topic=ResearchTopic(subject="Grace Hopper", relation="general")),
        result("Grace Hopper"),
    ).claims[0]
    store.remember(fallback)
    found = ResearchClaimEngine.retrieve(store, ResearchTopic(subject="Grace Hopper", relation="identity"))
    assert len(found) == 1
