from __future__ import annotations

from artmach_assistant.core.service_supervisor import ServiceSupervisor


def test_external_recovery_restores_supervisor_idle(monkeypatch):
    supervisor = ServiceSupervisor(check_interval=0.25)
    supervisor.register(
        "worker",
        is_running=lambda: True,
        restart=lambda: None,
        enabled=lambda: True,
    )
    service = supervisor._services[0]
    service.failures = 1

    states: list[tuple[str, str]] = []
    recovered: list[str] = []
    monkeypatch.setattr(
        "artmach_assistant.core.service_supervisor.service_status_registry.set_state",
        lambda name, state, message, **details: states.append((name, state)),
    )
    monkeypatch.setattr(
        "artmach_assistant.core.service_supervisor.service_status_registry.recovered",
        lambda name, message: recovered.append(name),
    )

    assert supervisor._mark_running(service) is True
    supervisor._restore_supervisor_idle_if_healthy()

    assert recovered == []
    assert states[-1] == ("service_supervisor", "idle")


def test_supervisor_stays_non_idle_while_another_service_is_failed(monkeypatch):
    supervisor = ServiceSupervisor(check_interval=0.25)
    supervisor.register(
        "recovered-worker",
        is_running=lambda: True,
        restart=lambda: None,
        enabled=lambda: True,
    )
    supervisor.register(
        "failed-worker",
        is_running=lambda: False,
        restart=lambda: None,
        enabled=lambda: True,
    )
    recovered_service, failed_service = supervisor._services
    recovered_service.failures = 1
    failed_service.failures = 2

    states: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "artmach_assistant.core.service_supervisor.service_status_registry.set_state",
        lambda name, state, message, **details: states.append((name, state)),
    )

    assert supervisor._mark_running(recovered_service) is True
    supervisor._restore_supervisor_idle_if_healthy()

    assert states == []
    assert failed_service.failures == 2
