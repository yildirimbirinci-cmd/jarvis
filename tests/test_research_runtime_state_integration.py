from pathlib import Path

from artmach_assistant.core.research_knowledge_store import ResearchKnowledgeStore
from artmach_assistant.core.research_manager import ResearchResult, ResearchSource
from artmach_assistant.core.research_runtime_service import ResearchRuntimeService
from artmach_assistant.core.research_topic_state import ResearchTopicStateStore


class FakeResearcher:
    def search_many(self, queries, *, max_results_per_query=4):
        return [
            ResearchResult(
                query=query,
                sources=[ResearchSource(title=query, url="https://example.com", snippet="evidence")],
            )
            for query in queries
        ]


class FakeDialogue:
    def respond(self, prompt):
        return "summary"


def _runtime(tmp_path: Path) -> ResearchRuntimeService:
    return ResearchRuntimeService(
        FakeResearcher(),
        FakeDialogue(),
        ResearchKnowledgeStore(tmp_path / "knowledge.json"),
        ResearchTopicStateStore(tmp_path / "topic.json"),
    )


def test_explicit_research_updates_current_topic(tmp_path: Path):
    runtime = _runtime(tmp_path)
    outcome = runtime.execute("internette arastir Ada Lovelace", scope="work")

    assert outcome is not None
    assert runtime.topic_state.current("work").subject == "ada lovelace"


def test_followup_uses_structured_topic_without_transcript(tmp_path: Path):
    runtime = _runtime(tmp_path)
    runtime.execute("internette arastir Alan Turing", scope="work")

    outcome = runtime.execute("internette arastir ve bana anlat", scope="work")

    assert outcome is not None
    assert outcome.command.request.topic.subject == "alan turing"


def test_topic_state_survives_runtime_restart(tmp_path: Path):
    first = _runtime(tmp_path)
    first.execute("internette arastir Grace Hopper", scope="work")

    second = _runtime(tmp_path)
    outcome = second.execute("internette arastir ve ogren", scope="work")

    assert outcome is not None
    assert outcome.command.request.topic.subject == "grace hopper"
    assert outcome.learned is True


def test_scope_switch_does_not_leak_topic(tmp_path: Path):
    runtime = _runtime(tmp_path)
    runtime.execute("internette arastir Katherine Johnson", scope="a")

    assert runtime.execute("internette arastir ve anlat", scope="b") is None
