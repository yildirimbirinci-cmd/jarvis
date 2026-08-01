from artmach_assistant.core.service_status import ServiceStatusRegistry


def test_completed_keeps_service_running_while_work_remains() -> None:
    registry = ServiceStatusRegistry()
    registry.queued("indexer", 3)
    registry.set_state("indexer", "running")

    registry.completed("indexer", 1)

    snapshot = registry.snapshot("indexer")
    assert snapshot["queued"] == 2
    assert snapshot["processed"] == 1
    assert snapshot["state"] == "running"


def test_discarded_keeps_running_until_last_pending_item_is_removed() -> None:
    registry = ServiceStatusRegistry()
    registry.queued("refactoring", 2)
    registry.set_state("refactoring", "running")

    registry.discarded("refactoring", 1)
    first = registry.snapshot("refactoring")
    assert first["queued"] == 1
    assert first["processed"] == 0
    assert first["failed"] == 0
    assert first["state"] == "running"

    registry.discarded("refactoring", 1)
    final = registry.snapshot("refactoring")
    assert final["queued"] == 0
    assert final["processed"] == 0
    assert final["failed"] == 0
    assert final["state"] == "idle"
