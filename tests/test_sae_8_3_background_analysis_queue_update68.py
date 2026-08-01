from __future__ import annotations

from threading import Event
from time import monotonic, sleep

from artmach_assistant.core.background_analysis_queue import BackgroundAnalysisQueue
from artmach_assistant.core.service_status import service_status_registry


def _wait_until(predicate, timeout: float = 2.0) -> bool:
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        if predicate():
            return True
        sleep(0.01)
    return predicate()


def _reset_status() -> None:
    service_status_registry._services.clear()  # isolated regression fixture


def test_immediate_callback_does_not_leave_ghost_queued_count() -> None:
    _reset_status()
    queue = BackgroundAnalysisQueue(idle_seconds=0, check_interval=0.01)
    queue._last_activity -= 10.0
    finished = Event()

    assert queue.submit("instant", finished.set)
    assert finished.wait(1.0)
    assert _wait_until(lambda: service_status_registry.snapshot(queue.SERVICE_NAME)["processed"] == 1)

    snapshot = service_status_registry.snapshot(queue.SERVICE_NAME)
    assert snapshot["queued"] == 0
    assert snapshot["processed"] == 1
    queue.stop()


def test_retry_restores_queued_count_before_second_attempt() -> None:
    _reset_status()
    queue = BackgroundAnalysisQueue(idle_seconds=0, check_interval=0.01)
    queue._last_activity -= 10.0
    attempts: list[int] = []
    second_attempt = Event()

    def callback() -> None:
        attempts.append(service_status_registry.snapshot(queue.SERVICE_NAME)["queued"])
        if len(attempts) == 1:
            raise RuntimeError("temporary")
        second_attempt.set()

    assert queue.submit("retry", callback)
    assert second_attempt.wait(2.0)
    assert _wait_until(lambda: service_status_registry.snapshot(queue.SERVICE_NAME)["processed"] == 1)

    assert attempts == [1, 1]
    snapshot = service_status_registry.snapshot(queue.SERVICE_NAME)
    assert snapshot["queued"] == 0
    assert snapshot["failed"] == 1
    assert snapshot["processed"] == 1
    queue.stop()
