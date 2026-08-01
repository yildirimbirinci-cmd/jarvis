from __future__ import annotations

from artmach_assistant.core.conversation_runtime import ConversationTurnToken
from artmach_assistant.core.task_orchestrator import TaskOrchestrator


def _orchestrator(tmp_path):
    return TaskOrchestrator(
        history_file=tmp_path / "history.json",
        active_file=tmp_path / "active.json",
    )


def test_parent_turn_cancellation_marks_task_and_finish_as_cancelled(tmp_path) -> None:
    parent = ConversationTurnToken("turn-1")
    orchestrator = _orchestrator(tmp_path)
    record, token = orchestrator.start(
        "Uzun görev",
        parent_token=parent,
        turn_id=parent.turn_id,
    )

    assert record.turn_id == "turn-1"
    assert parent.cancel("yeni kullanıcı cümlesi") is True
    active = orchestrator.active
    assert active is not None
    assert active.state == "cancelling"
    assert "yeni kullanıcı cümlesi" in active.status_message
    assert token.cancelled is True
    assert token.reason == "yeni kullanıcı cümlesi"

    finished = orchestrator.finish(record.task_id)
    assert finished is not None
    assert finished.state == "cancelled"
    assert finished.error == "yeni kullanıcı cümlesi"


def test_already_cancelled_parent_starts_in_cancelling_state(tmp_path) -> None:
    parent = ConversationTurnToken("turn-old")
    parent.cancel("eski tur")
    orchestrator = _orchestrator(tmp_path)

    record, token = orchestrator.start("Görev", parent_token=parent, turn_id=parent.turn_id)

    assert record.state == "cancelling"
    assert token.cancelled is True
    assert orchestrator.cancel_active("tekrar") is False


def test_cancel_active_is_idempotent_and_keeps_first_reason(tmp_path) -> None:
    orchestrator = _orchestrator(tmp_path)
    record, token = orchestrator.start("Görev")

    assert orchestrator.cancel_active("ilk neden") is True
    assert orchestrator.cancel_active("ikinci neden") is False
    assert token.reason == "ilk neden"
    finished = orchestrator.finish(record.task_id)
    assert finished is not None
    assert finished.error == "ilk neden"
