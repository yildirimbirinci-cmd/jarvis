from __future__ import annotations

from threading import Event

from artmach_assistant.core import background_refactoring_queue as queue_module
from artmach_assistant.core.background_refactoring_queue import BackgroundRefactoringQueue


class _RecordingRegistry:
    def __init__(self) -> None:
        self.events: list[str] = []

    def ensure(self, name: str) -> None:
        self.events.append("ensure")

    def queued(self, name: str, amount: int = 1) -> None:
        self.events.append("queued")

    def set_state(self, name: str, state: str, message: str = "", **details: object) -> None:
        self.events.append(f"state:{state}")

    def completed(self, name: str, amount: int = 1, message: str = "") -> None:
        self.events.append("completed")

    def failed(self, name: str, error: BaseException | str, amount: int = 1) -> None:
        self.events.append("failed")


def test_submit_registers_queued_status_before_starting_worker(monkeypatch) -> None:
    registry = _RecordingRegistry()
    monkeypatch.setattr(queue_module, "service_status_registry", registry)
    queue = BackgroundRefactoringQueue(idle_seconds=0, check_interval=0.01)

    monkeypatch.setattr(queue, "start", lambda: registry.events.append("start"))
    job = queue.submit("fast", lambda cancel: None)

    assert job is not None
    assert registry.events[-2:] == ["queued", "start"]


def test_fast_job_cannot_leave_phantom_queued_status(monkeypatch) -> None:
    from artmach_assistant.core.service_status import ServiceStatusRegistry

    registry = ServiceStatusRegistry()
    monkeypatch.setattr(queue_module, "service_status_registry", registry)
    queue = BackgroundRefactoringQueue(idle_seconds=0, check_interval=0.01)
    finished = Event()

    job = queue.submit("fast-real", lambda cancel: finished.set())
    assert job is not None
    assert finished.wait(1)
    assert queue.wait("fast-real", 1) is not None

    snapshot = registry.snapshot(queue.SERVICE_NAME)
    assert snapshot["queued"] == 0
    assert snapshot["processed"] == 1
    queue.stop()
