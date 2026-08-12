import pytest

from artmach_assistant.core.research_contracts import ResearchAction, TopicReference
from artmach_assistant.core.research_intent import parse_research_request


@pytest.mark.parametrize(
    ("text", "subject", "action"),
    [
        ("Ada Lovelace internette arastir ve ogren", "ada lovelace", ResearchAction.RESEARCH_AND_LEARN),
        ("Zeki Muren internette arastir ve bana anlat", "zeki muren", ResearchAction.RESEARCH_AND_SUMMARIZE),
        ("Marie Curie webde ara ve ogren ve anlat", "marie curie", ResearchAction.RESEARCH_SUMMARIZE_AND_LEARN),
    ],
)
def test_subject_before_research_marker_is_explicit(text, subject, action):
    request = parse_research_request(text)
    assert request is not None
    assert request.topic.reference is TopicReference.EXPLICIT
    assert request.topic.subject == subject
    assert request.action is action


def test_topic_free_followup_still_uses_current_topic():
    request = parse_research_request("internette arastir ve ogren")
    assert request is not None
    assert request.topic.reference is TopicReference.CURRENT_TOPIC
    assert request.topic.subject == ""


def test_deictic_prefix_still_uses_current_topic():
    request = parse_research_request("Bunu internette arastir ve bulduklarini anlat")
    assert request is not None
    assert request.topic.reference is TopicReference.CURRENT_TOPIC
    assert request.topic.subject == ""
