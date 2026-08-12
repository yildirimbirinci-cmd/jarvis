from artmach_assistant.core.research_contracts import ResearchAction
from artmach_assistant.core.research_runtime_bridge import resolve_research_command


def test_explicit_topic_builds_general_plan():
    command = resolve_research_command("Internette araştır Ada Lovelace ve bana anlat")
    assert command is not None
    assert command.request.topic.subject == "ada lovelace"
    assert command.request.action is ResearchAction.RESEARCH_AND_SUMMARIZE
    assert command.plan.queries[0] == "ada lovelace"


def test_current_topic_is_resolved_from_previous_user_question():
    messages = [
        {"role": "user", "content": "Grace Hopper kimdir?"},
        {"role": "assistant", "content": "Grace Hopper hakkında kısa bir yanıt."},
    ]
    command = resolve_research_command(
        "Bunu internette araştır ve bulduklarını anlat",
        messages,
    )
    assert command is not None
    assert command.request.topic.subject == "Grace Hopper"
    assert command.request.topic.relation == "identity"
    assert command.plan.queries[0] == "Grace Hopper"


def test_topic_changes_do_not_require_entity_rules():
    for subject in ("Alan Turing", "Sabiha Gökçen", "Katherine Johnson", "Nikola Tesla"):
        messages = [
            {"role": "user", "content": f"{subject} kimdir?"},
            {"role": "assistant", "content": "Yerel cevap."},
        ]
        command = resolve_research_command("internette araştır ve öğren", messages)
        assert command is not None
        assert command.request.topic.subject == subject
        assert command.request.action is ResearchAction.RESEARCH_AND_LEARN


def test_unresolved_current_topic_returns_none():
    assert resolve_research_command("internette araştır ve anlat", []) is None


def test_non_research_text_returns_none():
    assert resolve_research_command("Ada Lovelace kimdir?") is None
