from __future__ import annotations

from pathlib import Path

from artmach_assistant.core.research_contracts import ResearchAction, ResearchRequest, ResearchTopic
from artmach_assistant.core.research_knowledge_store import ResearchKnowledgeStore
from artmach_assistant.core.research_manager import ResearchResult, ResearchSource
from artmach_assistant.core.research_runtime_service import ResearchRuntimeService


class FakeDialogue:
    def respond(self, prompt: str) -> str:
        return """[{"subject":"Ada Lovelace","predicate":"identity","object":"mathematician","evidence":"supported","source_indexes":[1],"confidence":0.9}]"""


class FailingResearcher:
    def search_many(self, *args, **kwargs):
        raise AssertionError("legacy direct search_many path must not run when research_executor is configured")


def _source() -> ResearchSource:
    return ResearchSource(
        title="Source",
        url="https://example.com/ada",
        snippet="Ada Lovelace was a mathematician.",
        content="Ada Lovelace was a mathematician.",
    )


def test_runtime_uses_existing_evidence_research_executor(tmp_path: Path):
    calls = []

    def execute(query: str) -> ResearchResult:
        calls.append(query)
        return ResearchResult(
            query=query,
            sources=[_source()],
            summary="Ada Lovelace was a mathematician.",
            evidence_confidence=0.9,
            evidence_domains=("example.com",),
            claim_key="ada|identity",
            evidence_learnable=True,
            search_queries=(query,),
        )

    store = ResearchKnowledgeStore(tmp_path / "knowledge.json")
    service = ResearchRuntimeService(
        FailingResearcher(),
        FakeDialogue(),
        store,
        research_executor=execute,
    )
    outcome = service.execute("Ada Lovelace internette arastir ve ogren", [])

    assert outcome is not None
    assert [item.casefold() for item in calls] == ["ada lovelace"]
    assert outcome.learned is True
    assert store.records


def test_runtime_does_not_learn_when_existing_evidence_gate_rejects(tmp_path: Path):
    def execute(query: str) -> ResearchResult:
        return ResearchResult(
            query=query,
            sources=[_source()],
            summary="Kaynaklar bu soruyu guvenilir bicimde dogrulamiyor.",
            evidence_confidence=0.2,
            evidence_domains=("example.com",),
            claim_key="ada|identity",
            evidence_learnable=False,
            search_queries=(query,),
        )

    store = ResearchKnowledgeStore(tmp_path / "knowledge.json")
    service = ResearchRuntimeService(
        FailingResearcher(),
        FakeDialogue(),
        store,
        research_executor=execute,
    )
    outcome = service.execute("Ada Lovelace internette arastir ve ogren", [])

    assert outcome is not None
    assert outcome.learned is False
    assert store.records == []


def test_research_result_exposes_explicit_learning_gate():
    result = ResearchResult(query="x", sources=[])
    assert result.evidence_learnable is False
