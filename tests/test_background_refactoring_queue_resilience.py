from __future__ import annotations

from threading import Event

from artmach_assistant.core.background_refactoring_queue import (
    BackgroundRefactoringQueue,
    RefactoringJobState,
)


class BrokenText:
    def __str__(self) -> str:
        raise RuntimeError("cannot stringify")


def test_wait_uses_terminal_event_and_returns_completed_job() -> None:
    queue = BackgroundRefactoringQueue(idle_seconds=0, check_interval=0.01)
    job = queue.submit("event", lambda cancel: "done")
    assert job is not None
    assert queue.wait("event", 1) is job
    assert job.done_event.is_set()
    assert job.state is RefactoringJobState.COMPLETED
    queue.stop()


def test_broken_result_text_does_not_kill_worker() -> None:
    queue = BackgroundRefactoringQueue(idle_seconds=0, check_interval=0.01)
    first = queue.submit("broken-result", lambda cancel: BrokenText())
    second_called = Event()
    second = queue.submit("next", lambda cancel: second_called.set())
    assert first is not None and second is not None
    assert queue.wait("broken-result", 1).state is RefactoringJobState.COMPLETED
    assert queue.wait("next", 1).state is RefactoringJobState.COMPLETED
    assert second_called.is_set()
    queue.stop()


def test_invalid_or_broken_key_is_rejected_without_worker_damage() -> None:
    queue = BackgroundRefactoringQueue(idle_seconds=0, check_interval=0.01)
    try:
        queue.submit(BrokenText(), lambda cancel: None)  # type: ignore[arg-type]
    except ValueError:
        pass
    else:
        raise AssertionError("broken key should be rejected")
    assert queue.get(BrokenText()) is None  # type: ignore[arg-type]
    queue.stop()


def test_service_status_failure_does_not_change_job_outcome(monkeypatch) -> None:
    queue = BackgroundRefactoringQueue(idle_seconds=0, check_interval=0.01)

    def explode(*args, **kwargs):
        raise RuntimeError("status offline")

    monkeypatch.setattr(
        "artmach_assistant.core.background_refactoring_queue.service_status_registry.completed",
        explode,
    )
    job = queue.submit("status", lambda cancel: "ok")
    assert job is not None
    assert queue.wait("status", 1).state is RefactoringJobState.COMPLETED
    queue.stop()
