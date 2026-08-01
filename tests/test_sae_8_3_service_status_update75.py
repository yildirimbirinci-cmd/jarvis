from artmach_assistant.core.service_status import ServiceStatusRegistry


def test_terminal_updates_keep_service_running_while_work_remains() -> None:
    registry = ServiceStatusRegistry()
    registry.queued("worker", 3)
    registry.set_state("worker", "running")

    registry.completed("worker", 1)
    snapshot = registry.snapshot("worker")
    assert snapshot["queued"] == 2
    assert snapshot["processed"] == 1
    assert snapshot["state"] == "running"

    registry.discarded("worker", 1)
    snapshot = registry.snapshot("worker")
    assert snapshot["queued"] == 1
    assert snapshot["processed"] == 1
    assert snapshot["failed"] == 0
    assert snapshot["state"] == "running"


def test_recovery_and_last_completion_become_idle_only_when_queue_is_empty() -> None:
    registry = ServiceStatusRegistry()
    registry.queued("worker", 2)
    registry.failed("worker", RuntimeError("temporary"), 1)

    registry.recovered("worker", "recovered")
    snapshot = registry.snapshot("worker")
    assert snapshot["queued"] == 1
    assert snapshot["state"] == "running"
    assert snapshot["last_error"] == ""
    assert snapshot["details"]["recovery_count"] == 1

    registry.completed("worker", 1)
    snapshot = registry.snapshot("worker")
    assert snapshot["queued"] == 0
    assert snapshot["state"] == "idle"
