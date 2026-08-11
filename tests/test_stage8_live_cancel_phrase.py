from artmach_assistant.core.live_operation_dialogue import is_live_operation_cancel_query


def test_plain_iptal_et_is_live_cancel() -> None:
    assert is_live_operation_cancel_query("iptal et") is True


def test_plain_iptal_remains_live_cancel() -> None:
    assert is_live_operation_cancel_query("iptal") is True


def test_existing_task_cancel_phrase_remains_live_cancel() -> None:
    assert is_live_operation_cancel_query("gorevi iptal et") is True


def test_non_cancel_sentence_is_not_live_cancel() -> None:
    assert is_live_operation_cancel_query("iptal etmenin anlamini acikla") is False
