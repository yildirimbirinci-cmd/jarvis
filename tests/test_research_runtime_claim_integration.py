from pathlib import Path

from artmach_assistant.core.research_claim_engine import ResearchClaimEngine
from artmach_assistant.core.research_knowledge_store import ResearchKnowledgeStore
from artmach_assistant.core.research_manager import ResearchResult, ResearchSource
from artmach_assistant.core.research_runtime_service import ResearchRuntimeService


class FakeResearcher:
    def search_many(self, queries, max_results_per_query=4):
        return [ResearchResult(query=str(tuple(queries)[0]), sources=[ResearchSource("A", "https://example.org/a", "supported", "supported")])]


class FakeDialogue:
    def __init__(self):
        self.calls = 0

    def respond(self, prompt):
        self.calls += 1
        if "JSON dizi" in prompt:
            return '[{"predicate":"identity","object":"mathematician","evidence":"supported","source_indexes":[1],"confidence":0.8}]'
        return "summary"


def test_runtime_learning_uses_atomic_claim_engine(tmp_path: Path):
    dialogue = FakeDialogue()
    store = ResearchKnowledgeStore(tmp_path / "knowledge.json")
    service = ResearchRuntimeService(FakeResearcher(), dialogue, store, claim_engine=ResearchClaimEngine(dialogue))
    outcome = service.execute("Ada Lovelace internette arastir ve ogren", [])
    assert outcome is not None
    assert outcome.learned
    assert store.records[-1].subject.casefold() == "ada lovelace"
    assert store.records[-1].object == "mathematician"
