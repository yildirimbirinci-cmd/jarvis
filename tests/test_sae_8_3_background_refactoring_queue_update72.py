from __future__ import annotations

from threading import Event
from time import monotonic, sleep

import artmach_assistant.core.background_refactoring_queue as queue_module
from artmach_assistant.core.background_refactoring_queue import (
    BackgroundRefactoringQueue,
    RefactoringJobState,
)
from artmach_assistant.core.service_status import ServiceStatusRegistry


def _wait_until(predicate, timeout: float = 2.0) -> bool:
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        if predicate():
            return True
        sleep(0.01)
    return bool(predicate())


def _fresh_registry(monkeypatch) -> ServiceStatusRegistry:
    registry = ServiceStatusRegistry()
    monkeypatch.setattr(queue_module, "service_status_registry", registry)
    return registry


def test_cancelled_queued_job_is_discarded_not_processed(monkeypatch) -> None:
    registry = _fresh_registry(monkeypatch)
    queue = BackgroundRefactoringQueue(idle_seconds=60.0, check_interval=0.02)

    try:
        job = queue.submit("queued-cancel", lambda _cancel: "unused")
        assert job is not None
        assert queue.cancel("queued-cancel") is True

        status = registry.snapshot(queue.SERVICE_NAME)
        assert job.state is RefactoringJobState.CANCELLED
        assert status["queued"] == 0
        assert status["processed"] == 0
        assert status["failed"] == 0
    finally:
        queue.stop(cancel_pending=True)


def test_cancelled_running_job_is_discarded_not_processed(monkeypatch) -> None:
    registry = _fresh_registry(monkeypatch)
    started = Event()

    def callback(cancel_event: Event) -> str:
        started.set()
        assert cancel_event.wait(1.0)
        return "cancelled"

    queue = BackgroundRefactoringQueue(idle_seconds=0.0, check_interval=0.02)

    try:
        job = queue.submit("running-cancel", callback)
        assert job is not None
        assert started.wait(1.0)
        assert queue.cancel("running-cancel") is True
        assert _wait_until(lambda: job.state is RefactoringJobState.CANCELLED)

        status = registry.snapshot(queue.SERVICE_NAME)
        assert status["queued"] == 0
        assert status["processed"] == 0
        assert status["failed"] == 0
    finally:
        queue.stop(cancel_pending=True)
