from __future__ import annotations

import pytest

from artmach_assistant.core.research_contracts import ResearchAction, ResearchTopic, TopicReference
from artmach_assistant.core.research_intent import parse_research_request
from artmach_assistant.core.research_query_planner import build_research_query_plan


@pytest.mark.parametrize(
    "text",
    [
        "Internette arastir ve bana anlat",
        "Internette araştır ve bana anlat",
        "Webde ara ve ogren",
        "Search the web and summarize",
    ],
)
def test_topic_free_research_followups_resolve_to_current_topic(text: str) -> None:
    request = parse_research_request(text)
    assert request is not None
    assert request.topic.reference is TopicReference.CURRENT_TOPIC
    assert request.topic.subject == ""


@pytest.mark.parametrize(
    ("text", "expected_subject"),
    [
        ("Internette arastir Zeki Muren", "zeki muren"),
        ("Internette arastir Marie Curie ve bana anlat", "marie curie"),
        ("Webde ara Alan Turing ve ogren", "alan turing"),
        ("Search the web Ada Lovelace and summarize", "ada lovelace"),
    ],
)
def test_explicit_subject_is_not_entity_specific(text: str, expected_subject: str) -> None:
    request = parse_research_request(text)
    assert request is not None
    assert request.topic.reference is TopicReference.EXPLICIT
    assert request.topic.subject == expected_subject


def test_action_is_separate_from_topic_resolution() -> None:
    request = parse_research_request("Internette arastir Grace Hopper ve ogren ve bana anlat")
    assert request is not None
    assert request.action is ResearchAction.RESEARCH_SUMMARIZE_AND_LEARN
    assert request.topic.subject == "grace hopper"


def test_query_planner_requires_resolved_topic() -> None:
    with pytest.raises(ValueError):
        build_research_query_plan(
            ResearchTopic(subject="", reference=TopicReference.CURRENT_TOPIC)
        )


def test_query_planner_is_relation_driven_not_name_driven() -> None:
    plan = build_research_query_plan(
        ResearchTopic(
            subject="Nikola Tesla",
            relation="field of work",
            original_question="Nikola Tesla hangi alanda calisti?",
        )
    )
    assert plan.queries[0] == "Nikola Tesla"
    assert "Nikola Tesla field of work" in plan.queries
    assert "Nikola Tesla hangi alanda calisti?" in plan.queries
