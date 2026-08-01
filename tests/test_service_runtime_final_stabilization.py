from __future__ import annotations

import time

from artmach_assistant.core.service_status import ServiceStatusRegistry, service_status_registry
from artmach_assistant.core.service_supervisor import ServiceSupervisor


def test_terminal_state_rejects_transient_details_from_same_update() -> None:
    registry = ServiceStatusRegistry()
    registry.set_state(
        "worker",
        "idle",
        "ready",
        recovering_service="worker",
        retry_delay_seconds=4,
        retry_at=123.0,
        error_type="RuntimeError",
        stable_detail="kept",
    )

    snapshot = registry.snapshot("worker")
    assert snapshot["details"] == {"stable_detail": "kept"}


def test_disabled_service_clears_stale_error_and_retry_metadata() -> None:
    name = "final_stabilization_disabled_service"
    enabled = True

    def is_enabled() -> bool:
        return enabled

    supervisor = ServiceSupervisor(check_interval=0.25, max_backoff=2.0)
    supervisor.register(
        name,
        is_running=lambda: (_ for _ in ()).throw(RuntimeError("status failed")),
        restart=lambda: None,
        enabled=is_enabled,
    )
    supervisor.start()
    try:
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if service_status_registry.snapshot(name)["state"] == "error":
                break
            time.sleep(0.03)
        assert service_status_registry.snapshot(name)["state"] == "error"

        enabled = False
        # The previous failure created a two-second backoff. Move its retry time
        # forward through the registered object so the disabled branch is observed
        # without making the test sleep for the full production delay.
        service = supervisor._services_snapshot()[0]
        service.next_retry_at = 0.0

        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            snapshot = service_status_registry.snapshot(name)
            if snapshot["state"] == "stopped":
                break
            time.sleep(0.03)

        snapshot = service_status_registry.snapshot(name)
        assert snapshot["state"] == "stopped"
        assert snapshot["last_error"] == ""
        assert "retry_delay_seconds" not in snapshot["details"]
        assert "error_type" not in snapshot["details"]
    finally:
        supervisor.stop()
