from artmach_assistant.core.assistant import AssistantEngine


def test_ordinary_second_task_phrase_is_not_collaborative_option_selection() -> None:
    assert AssistantEngine._collaborative_option_selection_index(
        "Sadece IKINCI GOREV CALISTI yaz.", 3
    ) is None


def test_explicit_second_solution_still_selects_second_option() -> None:
    assert AssistantEngine._collaborative_option_selection_index(
        "ikinci cozumle devam", 3
    ) == 1


def test_bare_ordinal_still_supports_existing_follow_up_contract() -> None:
    assert AssistantEngine._collaborative_option_selection_index("ikinci", 3) == 1


def test_explicit_second_option_with_suffix_still_selects_second_option() -> None:
    assert AssistantEngine._collaborative_option_selection_index(
        "ikinci secenekle devam", 3
    ) == 1


def test_ordinary_second_task_phrase_without_output_instruction_is_not_selection() -> None:
    assert AssistantEngine._collaborative_option_selection_index(
        "ikinci gorev calisti", 3
    ) is None
