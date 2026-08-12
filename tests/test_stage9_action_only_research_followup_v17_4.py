from artmach_assistant.core.assistant import AssistantEngine


def test_action_only_research_followup_without_deictic_pronoun() -> None:
    assert AssistantEngine._research_followup_action_only(
        "",
        "ve bulduklarini kisaca bana anlat",
    )
    assert AssistantEngine._research_followup_action_only(
        "",
        "ve sonuclari ozetle",
    )
    assert AssistantEngine._research_followup_action_only(
        "",
        "ve ogren hafizaya kaydet",
    )


def test_explicit_new_topic_is_not_action_only_followup() -> None:
    assert not AssistantEngine._research_followup_action_only(
        "",
        "zeki muren hakkinda",
    )
    assert not AssistantEngine._research_followup_action_only(
        "",
        "python gelistiricisini bul",
    )


def test_deictic_and_action_only_commands_share_same_classifier_boundary() -> None:
    assert AssistantEngine._research_followup_action_only(
        "bunu",
        "ve dogrula",
    ) is False
    # "bunu" remains handled by the existing explicit deictic branch; the new
    # helper is intentionally only for commands that omit the pronoun/topic.
