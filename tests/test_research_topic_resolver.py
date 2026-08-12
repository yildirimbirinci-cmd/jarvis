from __future__ import annotations

import pytest

from artmach_assistant.core.research_contracts import TopicReference
from artmach_assistant.core.research_intent import parse_research_request
from artmach_assistant.core.research_topic_resolver import resolve_research_request, topic_from_user_text


@pytest.mark.parametrize(
    ("text", "subject", "relation"),
    [
        ("Zeki Muren kimdir?", "Zeki Muren", "identity"),
        ("Marie Curie hangi alanda calisti?", "Marie Curie", "field"),
        ("Alan Turing hakkinda ne biliyorsun?", "Alan Turing", "general"),
        ("Who was Ada Lovelace?", "Ada Lovelace", "identity"),
        ("Tell me about Grace Hopper", "Grace Hopper", "general"),
    ],
)
def test_topic_inference_is_relation_driven(text: str, subject: str, relation: str) -> None:
    topic = topic_from_user_text(text)
    assert topic is not None
    assert topic.subject == subject
    assert topic.relation == relation


def test_research_command_never_becomes_its_own_topic() -> None:
    assert topic_from_user_text("Internette arastir ve bana anlat") is None


def test_current_topic_is_resolved_from_previous_user_turn() -> None:
    request = parse_research_request("Internette arastir ve bana anlat")
    assert request is not None
    assert request.topic.reference is TopicReference.CURRENT_TOPIC

    resolved = resolve_research_request(
        request,
        [
            {"role": "user", "content": "Nikola Tesla kimdir?"},
            {"role": "assistant", "content": "Yerel bilgime gore..."},
        ],
    )
    assert resolved is not None
    assert resolved.topic.subject == "Nikola Tesla"
    assert resolved.topic.relation == "identity"


def test_latest_real_user_topic_wins_without_entity_specific_rules() -> None:
    request = parse_research_request("Webde ara ve ogren")
    assert request is not None
    resolved = resolve_research_request(
        request,
        [
            {"role": "user", "content": "Marie Curie kimdir?"},
            {"role": "assistant", "content": "..."},
            {"role": "user", "content": "Katherine Johnson hangi alanda calisti?"},
            {"role": "assistant", "content": "..."},
        ],
    )
    assert resolved is not None
    assert resolved.topic.subject == "Katherine Johnson"
    assert resolved.topic.relation == "field"


def test_unresolvable_current_topic_returns_none() -> None:
    request = parse_research_request("Internette arastir ve bana anlat")
    assert request is not None
    assert resolve_research_request(request, []) is None
