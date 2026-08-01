from __future__ import annotations

from threading import Event
from time import monotonic, sleep

import artmach_assistant.core.background_analysis_queue as queue_module
from artmach_assistant.core.service_status import ServiceStatusRegistry


def _wait_until(predicate, timeout: float = 2.0) -> bool:
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        if predicate():
            return True
        sleep(0.01)
    return predicate()


def test_stop_discards_queued_jobs_without_counting_success_or_failure(monkeypatch) -> None:
    registry = ServiceStatusRegistry()
    monkeypatch.setattr(queue_module, "service_status_registry", registry)
    queue = queue_module.BackgroundAnalysisQueue(idle_seconds=60.0, check_interval=0.25)

    assert queue.submit("first", lambda: None)
    assert queue.submit("second", lambda: None)
    assert _wait_until(lambda: registry.snapshot(queue.SERVICE_NAME)["queued"] == 2)

    queue.stop()

    status = registry.snapshot(queue.SERVICE_NAME)
    assert status["queued"] == 0
    assert status["processed"] == 0
    assert status["failed"] == 0
    assert status["state"] == "stopped"


def test_stop_accounts_for_worker_held_job_and_remaining_queue(monkeypatch) -> None:
    registry = ServiceStatusRegistry()
    monkeypatch.setattr(queue_module, "service_status_registry", registry)
    queue = queue_module.BackgroundAnalysisQueue(idle_seconds=60.0, check_interval=0.25)
    worker_has_job = Event()

    original_idle = queue._idle

    def never_idle() -> bool:
        worker_has_job.set()
        return False

    monkeypatch.setattr(queue, "_idle", never_idle)
    assert queue.submit("held", lambda: None)
    assert queue.submit("waiting", lambda: None)
    assert worker_has_job.wait(1.0)

    queue.stop()
    monkeypatch.setattr(queue, "_idle", original_idle)

    status = registry.snapshot(queue.SERVICE_NAME)
    assert status["queued"] == 0
    assert status["processed"] == 0
    assert status["failed"] == 0
    assert status["state"] == "stopped"
