from pathlib import Path

from artmach_assistant.core.research_contracts import ResearchTopic
from artmach_assistant.core.research_topic_state import ResearchTopicStateStore


def test_topic_state_persists_across_restart(tmp_path: Path):
    path = tmp_path / "topic.json"
    store = ResearchTopicStateStore(path)
    store.remember(ResearchTopic(subject="Ada Lovelace", relation="identity", original_question="Ada Lovelace kimdir?"))

    restored = ResearchTopicStateStore(path).current()

    assert restored is not None
    assert restored.subject == "Ada Lovelace"
    assert restored.relation == "identity"
    assert restored.original_question == "Ada Lovelace kimdir?"


def test_topic_state_is_scoped(tmp_path: Path):
    store = ResearchTopicStateStore(tmp_path / "topic.json")
    store.remember(ResearchTopic(subject="Alan Turing"), "project-a")
    store.remember(ResearchTopic(subject="Grace Hopper"), "project-b")

    assert store.current("project-a").subject == "Alan Turing"
    assert store.current("project-b").subject == "Grace Hopper"
    assert store.current("project-c") is None


def test_topic_state_clear_is_local_to_scope(tmp_path: Path):
    store = ResearchTopicStateStore(tmp_path / "topic.json")
    store.remember(ResearchTopic(subject="Nikola Tesla"), "a")
    store.remember(ResearchTopic(subject="Hedy Lamarr"), "b")

    assert store.clear("a") is True
    assert store.current("a") is None
    assert store.current("b").subject == "Hedy Lamarr"
