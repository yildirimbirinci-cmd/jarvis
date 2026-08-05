from __future__ import annotations

from artmach_assistant.core.live_operation_dialogue import (
    is_live_operation_cancel_query,
    is_live_operation_status_query,
    should_resume_live_operation_listening,
)


def test_voice_worker_reopens_listener_for_live_queries() -> None:
    assert should_resume_live_operation_listening(
        source="voice",
        worker_running=True,
        wake_running=True,
    )


def test_listener_does_not_reopen_without_active_voice_task() -> None:
    assert not should_resume_live_operation_listening(
        source="keyboard",
        worker_running=True,
        wake_running=True,
    )
    assert not should_resume_live_operation_listening(
        source="voice",
        worker_running=False,
        wake_running=True,
    )
    assert not should_resume_live_operation_listening(
        source="voice",
        worker_running=True,
        wake_running=False,
    )


def test_live_status_and_cancel_phrases_remain_routable() -> None:
    assert is_live_operation_status_query("ne durumdasin")
    assert is_live_operation_status_query("simdiye kadar ne buldun")
    assert is_live_operation_cancel_query("bakimi durdur")
