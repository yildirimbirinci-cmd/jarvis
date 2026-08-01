from __future__ import annotations

from threading import Event
from time import sleep

from artmach_assistant.core.background_refactoring_queue import (
    BackgroundRefactoringQueue,
    RefactoringJobResult,
    RefactoringJobState,
    RefactoringPriority,
)


def test_deduplicates_active_job_and_returns_result() -> None:
    queue = BackgroundRefactoringQueue(idle_seconds=0, check_interval=0.01)
    blocker = Event()

    def callback(cancel: Event) -> RefactoringJobResult:
        blocker.wait(0.3)
        return RefactoringJobResult("proposal ready", {"approved": False})

    first = queue.submit("same", callback)
    assert first is not None
    assert queue.submit("same", callback) is None
    blocker.set()
    job = queue.wait("same", 1)
    queue.stop()
    assert job is not None
    assert job.state is RefactoringJobState.COMPLETED
    assert job.result is not None
    assert job.result.payload == {"approved": False}


def test_priority_orders_jobs_before_execution() -> None:
    queue = BackgroundRefactoringQueue(idle_seconds=0.2, check_interval=0.01)
    order: list[str] = []
    queue.submit("low", lambda cancel: order.append("low"), priority=RefactoringPriority.LOW)
    queue.submit("high", lambda cancel: order.append("high"), priority=RefactoringPriority.HIGH)
    queue.submit("normal", lambda cancel: order.append("normal"), priority=RefactoringPriority.NORMAL)
    assert queue.wait("low", 2).state is RefactoringJobState.COMPLETED
    queue.stop()
    assert order == ["high", "normal", "low"]


def test_mark_activity_postpones_execution_until_idle() -> None:
    queue = BackgroundRefactoringQueue(idle_seconds=0.12, check_interval=0.01)
    called = Event()
    queue.submit("idle", lambda cancel: called.set())
    sleep(0.06)
    queue.mark_activity()
    sleep(0.07)
    assert not called.is_set()
    assert called.wait(0.3)
    queue.stop()


def test_cancel_queued_job_prevents_callback() -> None:
    queue = BackgroundRefactoringQueue(idle_seconds=0.2, check_interval=0.01)
    called = Event()
    queue.submit("cancel", lambda cancel: called.set())
    assert queue.cancel("cancel") is True
    job = queue.wait("cancel", 0.5)
    queue.stop()
    assert job is not None
    assert job.state is RefactoringJobState.CANCELLED
    assert not called.is_set()


def test_failure_isolated_and_next_job_runs() -> None:
    queue = BackgroundRefactoringQueue(idle_seconds=0, check_interval=0.01)
    done = Event()

    def broken(cancel: Event) -> None:
        raise RuntimeError("boom")

    queue.submit("broken", broken, priority=RefactoringPriority.HIGH)
    queue.submit("next", lambda cancel: done.set())
    assert queue.wait("next", 1).state is RefactoringJobState.COMPLETED
    failed = queue.get("broken")
    queue.stop()
    assert failed is not None
    assert failed.state is RefactoringJobState.FAILED
    assert failed.error == "boom"
    assert done.is_set()
