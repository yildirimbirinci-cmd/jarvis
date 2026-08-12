from pathlib import Path

from artmach_assistant.core.research_knowledge_store import ResearchKnowledgeStore
from artmach_assistant.core.research_manager import ResearchResult, ResearchSource
from artmach_assistant.core.research_runtime_service import ResearchRuntimeService


class FakeResearcher:
    def __init__(self):
        self.queries = []

    def search_many(self, queries, *, max_results_per_query=4):
        self.queries = list(queries)
        return [
            ResearchResult(
                query=query,
                sources=[
                    ResearchSource(
                        title=f"Source for {query}",
                        url=f"https://example.com/{index}",
                        snippet=f"Evidence about {query}",
                    )
                ],
            )
            for index, query in enumerate(self.queries)
        ]


class FakeDialogue:
    def __init__(self):
        self.prompts = []

    def respond(self, prompt):
        self.prompts.append(prompt)
        return "Verified summary"


def test_explicit_topic_runs_general_pipeline(tmp_path: Path):
    researcher = FakeResearcher()
    dialogue = FakeDialogue()
    store = ResearchKnowledgeStore(tmp_path / "knowledge.json")
    runtime = ResearchRuntimeService(researcher, dialogue, store)

    outcome = runtime.execute("internette arastir Ada Lovelace ve bana anlat")

    assert outcome is not None
    assert outcome.command.request.topic.subject == "ada lovelace"
    assert researcher.queries[0] == "ada lovelace"
    assert outcome.result.summary == "Verified summary"
    assert not outcome.learned
    assert store.records == []


def test_current_topic_is_resolved_from_conversation(tmp_path: Path):
    researcher = FakeResearcher()
    runtime = ResearchRuntimeService(
        researcher,
        FakeDialogue(),
        ResearchKnowledgeStore(tmp_path / "knowledge.json"),
    )
    messages = [
        {"role": "user", "content": "Grace Hopper kimdir?"},
        {"role": "assistant", "content": "Bilmiyorum."},
    ]

    outcome = runtime.execute("internette arastir ve bana anlat", messages)

    assert outcome is not None
    assert outcome.command.request.topic.subject == "Grace Hopper"
    assert outcome.command.request.topic.relation == "identity"
    assert researcher.queries[0] == "Grace Hopper"


def test_learning_persists_verified_claim(tmp_path: Path):
    store = ResearchKnowledgeStore(tmp_path / "knowledge.json")
    runtime = ResearchRuntimeService(FakeResearcher(), FakeDialogue(), store)

    outcome = runtime.execute("internette arastir Katherine Johnson ogren ve bana anlat")

    assert outcome is not None
    assert outcome.learned
    assert len(store.records) == 1
    assert store.records[0].subject == "katherine johnson"
    assert store.records[0].object == "Verified summary"
    reloaded = ResearchKnowledgeStore(tmp_path / "knowledge.json")
    assert len(reloaded.records) == 1
    assert reloaded.records[0].subject == "katherine johnson"


def test_different_topics_do_not_share_state(tmp_path: Path):
    runtime = ResearchRuntimeService(
        FakeResearcher(),
        FakeDialogue(),
        ResearchKnowledgeStore(tmp_path / "knowledge.json"),
    )

    first = runtime.execute("internette arastir Nikola Tesla")
    second = runtime.execute("internette arastir Hedy Lamarr")

    assert first is not None and second is not None
    assert first.command.request.topic.subject == "nikola tesla"
    assert second.command.request.topic.subject == "hedy lamarr"
